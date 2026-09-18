import logging
import os
import re
import time
import threading
import shutil
from typing import List, Optional, Any, Tuple, Dict, cast, Set
from concurrent.futures import ThreadPoolExecutor, as_completed

# pylint: disable=no-name-in-module, import-error
import google.genai as genai
from google.genai import types

from PIL import Image

from app_tools.ai_service import BaseAIProcessor, AIAPIError
from config import Config

# ── Formatos de imagen soportados oficialmente por la API de Gemini (2026) ──
# Fuente: docs oficiales "Image understanding / file input methods" (2026):
# image/jpeg, image/png, image/gif, image/webp, image/bmp, image/heic, image/heif.
# NOTA: image/avif NO está soportado por la API.
IMG_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}

_IMG_MIME_BY_PILFMT = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "heic": "image/heic",
    "heif": "image/heif",
}

# Límite inline de la API ~20MB por request. Con BATCH_SIZE=3 y este tope por trozo
# (~6.5MB), el request nunca excede el límite sin tener que degradar calidad.
MAX_INLINE_SLICE_BYTES = 6_500_000


def mime_for_image(path: str) -> str:
    """Devuelve el MIME type correcto según la extensión; como respaldo usa el
    formato real detectado por Pillow. Así webp/gif/bmp/heic son enviados con su
    MIME real (nunca se disfrazan de image/jpeg)."""
    mime = IMG_MIME_BY_EXT.get(os.path.splitext(path.lower())[1])
    if mime:
        return mime
    try:
        with Image.open(path) as im:
            return _IMG_MIME_BY_PILFMT.get((im.format or "").lower(), "image/jpeg")
    except Exception:
        return "image/jpeg"


def _slice_format_for(src_path: str) -> Tuple[str, str, Dict[str, Any]]:
    """Formato de salida de los trozos de una imagen larga: PRESERVA el formato del
    original y es SIEMPRE lossless (nunca se degrada calidad al cortar).
    - PNG  -> PNG
    - WebP -> WebP lossless (mismas extensión, cero pérdida)
    - JPEG/GIF/BMP -> PNG (al ser fuentes lossy, se evita acumular pérdidas)
    - HEIC/HEIF   -> no se pueden cortar sin decodificador: se pasan tal cual
    Devuelve (pil_format, extension_con_punto, kwargs_de_save)."""
    ext = os.path.splitext(src_path.lower())[1]
    if ext == ".png":
        return ("PNG", ".png", {})
    if ext == ".webp":
        return ("WEBP", ".webp", {"lossless": True, "method": 6})
    return ("PNG", ".png", {})

class APIKeyPool:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(APIKeyPool, cls).__new__(cls)
                cls._instance._init_pool()
            return cls._instance

    def _init_pool(self):
        self.lock = threading.Lock()
        self.keys_state = {}
        today = time.strftime("%Y-%m-%d")

        saved_date = Config.user_settings.get("LAST_REQUEST_DATE", "")

        if saved_date != today:
            Config.save_user_settings({"LAST_REQUEST_DATE": today})

        for k in Config.GEMINI_API_KEYS:
            self.keys_state[k] = {
                'last_time': 0.0,
                'exhausted': False,
                'tpm_cooldown_until': 0.0
            }

    def mark_exhausted(self, key: str):
        with self.lock:
            if key in self.keys_state:
                self.keys_state[key]['exhausted'] = True

    def mark_tpm_limit(self, key: str, cooldown_seconds: float = 60.0):
        with self.lock:
            if key in self.keys_state:
                self.keys_state[key]['tpm_cooldown_until'] = time.time() + cooldown_seconds

    def acquire_key_and_reserve(self, limits: dict) -> Tuple[str, float]:
        rpm = limits.get("RPM", 5)
        min_interval = 60.0 / rpm

        with self.lock:
            best_key = None
            min_wait = float('inf')

            for k, state in self.keys_state.items():
                if state['exhausted']: continue

                now = time.time()
                if now < state['tpm_cooldown_until']:
                    wait = state['tpm_cooldown_until'] - now
                else:
                    elapsed = now - state['last_time']
                    wait = max(0.0, min_interval - elapsed)

                if wait < min_wait:
                    min_wait = wait
                    best_key = k

            if best_key is None:
                return "", -1.0

            if min_wait <= 0.0:
                self.keys_state[best_key]['last_time'] = time.time()
                return best_key, 0.0

            return best_key, min_wait

class GeminiAPIError(AIAPIError):
    pass


def _classify_api_error(e: BaseException) -> str:
    """Clasifica un error de la API de Gemini.

    Retorna una categoría de un conjunto fijo para decidir si una key se
    agota, se hace cooldown, o simplemente se reporta sin quemar keys:
      - "key": la key en sí es inválida/revocada/sin autorización.
      - "quota_daily": cuota diaria agotada (RPD) → agotar key por hoy.
      - "quota_tpm": rate limit de corto plazo (TPM/RPM).
      - "server": 503/overloaded/unavailable → reintentar.
      - "request": error de petición determinístico (400 invalid argument,
        404 modelo no encontrado, 403 permission del modelo, etc.).
      - "other": desconocido.

    El error típico del SDK google.genai es ClientError con atributos
    `code` (int) y `status` (str). Para cualquier otra excepción se
    analiza el texto. CRÍTICO: NO usar subcadenas genéricas como
    "invalid" o "permission": "400 INVALID_ARGUMENT" aparece en errores
    de request con key válida y agotaría keys sanas en cadena.
    """
    e_str = str(e)
    el = e_str.lower()
    code = getattr(e, "code", None)
    status = str(getattr(e, "status", "") or "").upper()

    # ── Errores de cuota ──────────────────────────────────────────────
    is_429 = code == 429 or status == "RESOURCE_EXHAUSTED" or "rate limit" in el or "resource_exhausted" in el
    if is_429 or "429" in el:
        if "daily" in el or "quota" in el or "rpd" in el:
            return "quota_daily"
        return "quota_tpm"

    # ── Errores de servidor (transitorios) ────────────────────────────
    if code in (500, 502, 503, 504) or any(t in el for t in ("unavailable", "overloaded", "disconnected", "backend")):
        return "server"

    # ── Errores de key REALES (mensajes explícitos o 401/403 con auth) ─
    # El mensaje típico de key inválida es
    #   "API key not valid. Please pass a valid API key." (400 INVALID_ARGUMENT)
    # y "UNAUTHENTICATED" en .status para 401.
    # Aclaración CRÍTICA: un 403 PERMISSION_DENIED por ACCESO AL MODELO
    # (p. ej. "model not allowed") NO es key inválida: rotar no lo arregla
    # y quemaría todas las keys en cadena. Solo es error de key si el
    # mensaje menciona explícitamente credenciales/apikey.
    has_key_msg = any(t in el for t in ("api key not valid", "invalid api key", "api key invalid", "bad api key", "api key is invalid"))
    has_auth_msg = any(t in el for t in ("unauthenticated", "authentication failed", "invalid credentials", "api key rejected", "invalid_api_key", "api key" and "unauthor"))
    is_unauthenticated = status == "UNAUTHENTICATED" or code == 401
    is_denied_auth = status == "PERMISSION_DENIED" and (
        "permission denied" in el
        and any(t in el for t in ("api key", "credential", "project number", "permissions for your project"))
    )
    if has_key_msg or has_auth_msg or is_unauthenticated or is_denied_auth:
        return "key"

    # ── Errores de request determinísticos (NO quemar keys) ───────────
    # 400/404/409/422 con key válida = problema de la petición o del modelo
    # (p. ej. modelo inexistente, campo no soportado), NO de la key.
    if isinstance(code, int) and 400 <= code < 500:
        return "request"
    if any(t in el for t in ("invalid_argument", "not found", "not_found", "permission_denied", "modelerror", "does not exist", "is not found", "model not allowed")):
        return "request"

    return "other"

# Escalera de modelos de MAYOR a MENOR potencia. Si un modelo está saturado
# (503), el fallback baja de a un escalón consecutivo: 3.8-flash → 3.7-flash →
# 3.6-flash → 3.5-flash → 3.5-flash-lite → … Solo se consideran modelos reales.
_GEMINI_LADDER = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
]

class GeminiProcessor(BaseAIProcessor):
    def __init__(self):
        super().__init__(model_name="Gemini")
        self._failed_models: Set[str] = set()
        self._exhausted_keys: Set[str] = set()
        # Los workers del ThreadPoolExecutor mutan modelo/key y estos sets de forma
        # concurrente; un RLock serializa el fallback sin bloquear las lecturas.
        self._cfg_lock = threading.RLock()

    def _get_current_limits(self) -> Dict[str, int]:
        model = Config.GEMINI_MODEL.lower()
        for m_name, limits in Config.MODEL_LIMITS.items():
            if m_name == model:
                return limits
        return {"RPM": 5, "TPM": 250000, "RPD": 20}

    def _wait_and_get_key(self) -> str:
        limits = self._get_current_limits()
        pool = APIKeyPool()
        
        while True:
            key, wait_time = pool.acquire_key_and_reserve(limits)
            if wait_time < 0:
                msg = "Límite diario alcanzado o todas las llaves agotadas."
                self._report_status(msg)
                raise GeminiAPIError(msg)
            
            if wait_time == 0.0:
                return key
            
            self._report_status(f"Respetando RPM. Pausando {wait_time:.1f}s por llave disponible...")
            time.sleep(wait_time)

    def get_client(self, api_key: Optional[str] = None) -> Any: # type: ignore
        return genai.Client(
            api_key=api_key or Config.GEMINI_API_KEY,
            http_options={'api_version': 'v1beta'}
        )

    def validate_key(self, api_key: str) -> Tuple[bool, str]:
        try:
            client = genai.Client(api_key=api_key)
            # Intentar una llamada mínima válida para ver si la key funciona.
            # models.list() sin argumentos es lo más estándar.
            client.models.list()
            return True, "Key válida"
        except Exception as e:
            return False, str(e)

    def get_available_models(self) -> List[str]:
        # Check for API key before making requests
        current_key = Config.GEMINI_API_KEY
        if not current_key or len(current_key.strip()) < 10:
            return list(Config.MODEL_LIMITS.keys()) # Return defaults if no key

        try:
            client = self.get_client()
            models_iter = client.models.list()
            available_models: List[str] = []

            def is_translation_flash(name: str) -> bool:
                # Solo modelos 'flash' coherentes con la traducción de manga:
                # generación de texto/imagen multimodal, sin variantes especializadas
                # (tts/audio/live/omni/embedding/robotics/veo/lyria/deep-research).
                if "-tts" in name or "audio" in name or "-live-" in name or name.endswith("-live"):
                    return False
                if "omni" in name or "image" in name or "transcribe" in name:
                    return False
                if "embedding" in name or "robotics" in name or "computer-use" in name:
                    return False
                if "veo" in name or "lyria" in name or "aqa" in name or "deep-research" in name or "antigravity" in name or "nano-banana" in name:
                    return False
                # Excluir aliases genéricos "-latest" y la serie 2.5 (modelos viejos):
                # solo interesan los flash 3.x actuales.
                if "-latest" in name or name.startswith("gemini-2.5-"):
                    return False
                # Nombre base gemini[-versión]-flash, con sufijos lite/preview/latest.
                return bool(re.fullmatch(r'gemini(?:-[\d.]+)?-flash(?:-(?:lite|preview|latest))*', name))

            for model in models_iter:
                model_name = getattr(model, 'name', '')
                if not model_name:
                    continue
                name: str = str(model_name).lower().replace("models/", "")

                if is_translation_flash(name) and name not in available_models:
                    available_models.append(name)

            def sort_priority(m_name: str) -> Tuple:
                # Extraer versión principal y sub-versión
                version_match = re.search(r'gemini-(\d+(\.\d+)?)', m_name)
                main_version = 0
                sub_version = 0
                if version_match:
                    try:
                        # Dar un gran peso a la versión principal
                        version_parts = version_match.group(1).split('.')
                        main_version = int(version_parts[0]) * 100
                        if len(version_parts) > 1:
                            sub_version = int(version_parts[1]) * 10
                    except (ValueError, IndexError):
                        pass
                
                # Penalizar 'lite' y 'latest' para que vayan al final de su grupo
                penalty = 0
                if "lite" in m_name:
                    penalty = 5
                if "latest" in m_name:
                    penalty = 9

                # La prioridad final es la versión menos la penalización.
                # A mayor número, más arriba aparecerá.
                priority = main_version + sub_version - penalty
                return (priority, m_name)

            available_models.sort(key=sort_priority, reverse=True)
            if not available_models:
                return ["gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-3.5-flash"]
            return available_models
        except Exception as e:
            logging.error(f"Error obteniendo modelos: {e}")
            return ["gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-3.5-flash"]

    def _try_switch_model(self) -> bool:
        """Intenta cambiar a otro modelo disponible si el actual falla."""
        with self._cfg_lock:
            return self._switch_model_locked()

    def _switch_model_locked(self) -> bool:
        current = Config.GEMINI_MODEL.lower()
        self._failed_models.add(current)

        # Escalera descendente desde el modelo actual: primero los escalones
        # inferiores (3.8-flash → 3.7-flash → 3.6-flash → …). Si todos los
        # inferiores ya fallaron, queda la escalera completa como última red.
        ladder = [m for m in _GEMINI_LADDER if m in Config.MODEL_LIMITS]
        candidates = ladder
        if current in ladder:
            pos = ladder.index(current)
            candidates = ladder[pos + 1:] + ladder[:pos]

        for model in candidates:
            if model in Config.MODEL_LIMITS and model not in self._failed_models:
                self._report_status(f"Fallo en {Config.GEMINI_MODEL}. Cambiando a {model}...")
                Config.GEMINI_MODEL = model
                return True

        return False

    def _rotate_key(self) -> bool:
        with self._cfg_lock:
            return self._rotate_key_locked()

    def _rotate_key_locked(self) -> bool:
        # Marcar la key actual como "agotada" antes de cambiar
        self._exhausted_keys.add(Config.GEMINI_API_KEY)
        
        # Verificar si ya hemos quemado TODAS las keys disponibles
        total_keys = len(Config.GEMINI_API_KEYS)
        if len(self._exhausted_keys) >= total_keys:
            self._report_status("FATAL: Todas las API Keys disponibles se han agotado o fallan.")
            return False

        # Resetear historial de modelos fallidos para la nueva key
        self._failed_models.clear()
            
        new_key = Config.get_next_gemini_key(Config.GEMINI_API_KEY)
        
        # Seguridad extra: buscar una key no quemada
        attempts = 0
        while new_key in self._exhausted_keys and attempts < total_keys:
            new_key = Config.get_next_gemini_key(new_key)
            attempts += 1
            
        if new_key != Config.GEMINI_API_KEY and new_key not in self._exhausted_keys:
            self._report_status(f"Rotando a nueva API Key (Intento {len(self._exhausted_keys)}/{total_keys})...")
            Config.GEMINI_API_KEY = new_key
            # CRÍTICO: Resetear contador local
            Config.DAILY_REQUEST_COUNT = 0
            Config.save_user_settings({"DAILY_REQUEST_COUNT": 0})
            return True
            
        return False

    def _try_next_key_busy(self) -> bool:
        """Cambia a otra API Key por saturación (503), SIN quemar la key actual.
        Un 503 es del servidor/modelo, no de la key: no la marcamos agotada.
        Solo se usa cuando TODOS los modelos disponibles están ocupados."""
        with self._cfg_lock:
            total = len(Config.GEMINI_API_KEYS)
            if total < 2:
                return False
            current = Config.GEMINI_API_KEY
            nxt = Config.get_next_gemini_key(current)
            attempts = 0
            while attempts < total:
                if nxt != current and nxt not in self._exhausted_keys:
                    break
                nxt = Config.get_next_gemini_key(nxt)
                attempts += 1
            if nxt == current or nxt in self._exhausted_keys:
                return False
            self._report_status("Todos los modelos ocupados en esta key. Probando con otra API Key...")
            Config.GEMINI_API_KEY = nxt
            # La nueva key reinicia la escalera de modelos desde arriba.
            self._failed_models.clear()
            top = next((m for m in _GEMINI_LADDER if m in Config.MODEL_LIMITS), None)
            if top:
                Config.GEMINI_MODEL = top
                self._report_status(f"Nueva key: reiniciando modelos desde {top}...")
            return True

    def _reset_model_to_default(self):
        """Resetea el modelo al preferido al cambiar de API Key."""
        with self._cfg_lock:
            default_model = "gemini-3.1-flash-lite"
            self._report_status(f"Nueva Key: Reseteando modelo a {default_model}")
            Config.GEMINI_MODEL = default_model

    @staticmethod
    def _build_thinking_config(model: str) -> Optional["types.ThinkingConfig"]:
        """Construye el ThinkingConfig según la familia del modelo y el nivel elegido.

        - Serie 3.x: usa thinking_level (minimal/low/medium/high). No se puede apagar.
        - Serie 2.5 y otros: usa thinking_budget (tokens; 0 = apagado, -1 = dinámico).
        """
        level = str(getattr(Config, "GEMINI_THINKING_LEVEL", "auto")).lower()
        if level not in Config.THINKING_LEVELS:
            level = "auto"
        enabled = bool(Config.GEMINI_ENABLE_THINKING)

        if "gemini-3" in model.lower():
            if level == "auto" and enabled:
                return types.ThinkingConfig(include_thoughts=True)
            # 3.x no permite apagar el pensamiento: "off" = minimal
            lvl = level if enabled else "minimal"
            return types.ThinkingConfig(thinking_level=lvl)

        # Serie 2.5 / otros
        if not enabled:
            return types.ThinkingConfig(thinking_budget=0, include_thoughts=False)
        if level == "auto":
            return types.ThinkingConfig(thinking_budget=-1, include_thoughts=True)
        return types.ThinkingConfig(
            thinking_budget=Config.THINKING_BUDGET_2_5.get(level, -1),
            include_thoughts=True,
        )

    def call_api(self, prompt: str, image_path: Optional[str] = None, content: Optional[str] = None) -> str:
        if image_path:
            results = self.call_api_batch(prompt, [image_path])
            return results[0] if results else ""
        
        max_retries = 3
        base_delay = 2

        for attempt in range(max_retries + 1):
            current_key = ""
            try:
                current_key = self._wait_and_get_key()
                client = self.get_client(current_key)
                config = types.GenerateContentConfig(temperature=Config.GEMINI_TEMPERATURE)
                thinking_config = self._build_thinking_config(Config.GEMINI_MODEL)
                if thinking_config is not None:
                    config.thinking_config = thinking_config
                response = client.models.generate_content(
                    model=Config.GEMINI_MODEL,
                    contents=[f"{prompt}\n\n{content}"],
                    config=config
                )
                return str(response.text).strip() if response.text else ""

            except Exception as e:
                error_str = str(e).lower()
                err_kind = _classify_api_error(e)
                is_server_error = err_kind == "server"

                # KEY INVÁLIDA/REVOCADA (real): marcar como agotada y rotar.
                if err_kind == "key":
                    if current_key:
                        APIKeyPool().mark_exhausted(current_key)
                    self._exhausted_keys.add(current_key)
                    self._report_status(f"API Key rechazada. Marcando como inválida y rotando. {str(e)[:60]}")
                    continue

                # Errores de request determinístico (400/404/403-modelo):
                # la key es válida, el problema es de la petición o del
                # modelo. NO quemar la key ni contar la reserva: reportar y abortar.
                if err_kind == "request":
                    msg = f"Error de petición: {str(e)[:80]}... (la petición/modelo es inválida, no la API key)"
                    self._report_status(msg)
                    raise GeminiAPIError(msg)

                if err_kind == "quota_daily":
                    if current_key:
                        APIKeyPool().mark_exhausted(current_key)
                    self._report_status(f"Cuota diaria alcanzada en la llave actual. Agotando para hoy y rotando...")
                    continue  # Try again with a different key immediately

                if err_kind == "quota_tpm":
                    APIKeyPool().mark_tpm_limit(current_key, 60.0)
                    self._report_status(f"Límite TPM alcanzado en la llave actual. Cambiando...")
                    continue  # Try again with a different key immediately

                if is_server_error and attempt < max_retries:
                    wait_time = base_delay * (2 ** attempt)
                    for i in range(int(wait_time), 0, -1):
                        self._report_status(f"Servidor ocupado. Reintento {attempt+1}/{max_retries} en {i}s...")
                        time.sleep(1)
                    continue

                self._report_status(f"Error API: {str(e)[:50]}...")
                raise GeminiAPIError(str(e))
        return ""

    def _slice_long_image(self, img_path: str, max_height: int = 3072, overlap: int = 200) -> List[str]:
        """Corta imágenes verticales largas en trozos alineados al tiling de Gemini (768px).
        Si el último trozo queda muy pequeño (<40% de max_height), se fusiona con el penúltimo.

        Los trozos PRESERVAN el formato original (PNG->PNG, WebP->WEBP, etc.) y SIEMPRE se
        guardan SIN pérdida de calidad: jamás se redimensiona la imagen. Si un trozo supera el
        límite inline de la API (~6.5MB), se subdivide por la mitad en lugar de degradar."""
        try:
            with Image.open(img_path) as img:
                width, height = img.size
                base_name = os.path.splitext(os.path.basename(img_path))[0]
                self._report_status(f"Analizando img: {base_name} ({width}x{height}px)")
                
                if height <= max_height:
                    return [img_path]

                temp_dir = os.path.dirname(img_path)
                slices_info: List[Tuple[int, int, int]] = [] 
                
                top = 0
                part = 1
                while top < height:
                    bottom = min(top + max_height, height)
                    slices_info.append((top, bottom, part))
                    if bottom == height:
                        break
                    top += (max_height - overlap)
                    part += 1

                # --- PROTECCIÓN ÚLTIMO TROZO ---
                # Si el último trozo es muy pequeño (<40% de max_height), lo fusionamos
                # con el penúltimo para evitar un tile de Gemini casi vacío.
                min_useful_height = int(max_height * 0.4)  # ~1229px para 3072, ~1536px para 3840
                if len(slices_info) >= 2:
                    last_top, last_bottom, _ = slices_info[-1]
                    last_h = last_bottom - last_top
                    if last_h < min_useful_height:
                        # Eliminar el último y extender el penúltimo hasta el final
                        slices_info.pop()
                        prev_top, _, prev_part = slices_info[-1]
                        slices_info[-1] = (prev_top, height, prev_part)

                pil_fmt, ext, save_kwargs = _slice_format_for(img_path)

                self._report_status(f"Procesando {len(slices_info)} trozos en paralelo para: {base_name}")

                def save_region(t: int, b: int, part_idx: int) -> List[str]:
                    """Corta la región [t, b), la guarda lossless y, si supera el límite
                    inline de la API, la subdivide por la mitad (sin redimensionar)."""
                    region_h = b - t
                    with Image.open(img_path) as thread_img:
                        cropped = thread_img.crop((0, t, width, b))
                    if cropped.mode in ("P", "LA"):
                        cropped = cropped.convert("RGBA")
                    s_path = os.path.join(temp_dir, f"temp_slice_{base_name}_{part_idx}_{t}{ext}")
                    cropped.save(s_path, format=pil_fmt, **save_kwargs)
                    if os.path.getsize(s_path) <= MAX_INLINE_SLICE_BYTES or region_h <= 1024:
                        return [s_path]
                    # Trozo demasiado grande: NO degradar, subdividir en 2 trozos lossless
                    # (corte alineado a 256px para no desperdiciar tiles de 768px).
                    os.remove(s_path)
                    mid = t + region_h // 2
                    mid -= mid % 256
                    mid = min(max(mid, t + 512), b - 512)
                    return save_region(t, mid, part_idx) + save_region(mid, b, part_idx)

                def process_single_slice(info: Tuple[int, int, int]):
                    # Liberar GIL brevemente para evitar que la UI se congele (thread pool)
                    time.sleep(0.005)
                    
                    t, b, p = info
                    return p, save_region(t, b, p)

                with ThreadPoolExecutor() as executor:
                    results = list(executor.map(process_single_slice, slices_info))
                
                results.sort(key=lambda x: x[0])
                return [path for _, paths in results for path in paths]
        except Exception as e:
            logging.error(f"Error en troceado paralelo: {e}")
            return [img_path]

    def _consolidate_images(self, image_paths: List[str], max_canvas_height: int = 4000) -> List[Tuple[str, List[int]]]:
        """
        Une imágenes pequeñas en lienzos verticales de hasta max_canvas_height.
        Retorna una lista de tuplas (path_del_canvas, lista_de_indices_originales).
        """
        if not image_paths:
            return []

        consolidated: List[Tuple[str, List[int]]] = []
        current_batch: List[str] = []
        current_height = 0
        temp_dir = os.path.dirname(image_paths[0])
        
        for i, img_path in enumerate(image_paths):
            try:
                with Image.open(img_path) as img:
                    h = img.size[1]
                
                if h >= max_canvas_height:
                    if current_batch:
                        c_path, _ = self._stitch_and_save(current_batch, temp_dir)
                        consolidated.append((c_path, list(range(len(current_batch)))))
                        current_batch = []
                        current_height = 0
                    consolidated.append((img_path, [i]))
                    continue

                if current_height + h > max_canvas_height:
                    c_path, _ = self._stitch_and_save(current_batch, temp_dir)
                    consolidated.append((c_path, list(range(len(current_batch)))))
                    current_batch = [img_path]
                    current_height = h
                else:
                    current_batch.append(img_path)
                    current_height += h
            except Exception as e:
                logging.error(f"Error consolidando: {e}")
                consolidated.append((img_path, [i]))

        if current_batch:
            c_path, _ = self._stitch_and_save(current_batch, temp_dir)
            consolidated.append((c_path, list(range(len(current_batch)))))

        return consolidated

    def _stitch_and_save(self, paths: List[str], temp_dir: str) -> Tuple[str, int]:
        """Une imágenes verticalmente y las guarda en PNG (lossless, sin pérdida de calidad)."""
        if len(paths) == 1:
            with Image.open(paths[0]) as img:
                return paths[0], img.size[1]

        images = [Image.open(p) for p in paths]
        max_w = max(img.size[0] for img in images)
        total_h = sum(img.size[1] for img in images)

        any_alpha = any(im.mode in ("RGBA", "LA", "P") for im in images)
        canvas_mode = "RGBA" if any_alpha else "RGB"
        bg_color = (255, 255, 255, 255) if canvas_mode == "RGBA" else (255, 255, 255)
        canvas = Image.new(canvas_mode, (max_w, total_h), bg_color)
        y = 0
        for img in images:
            # Liberar GIL brevemente durante el procesado intensivo
            time.sleep(0.005)

            if img.mode == "P":
                img = img.convert("RGBA")
            if canvas_mode == "RGBA" and img.mode == "RGB":
                img = img.convert("RGBA")
            if canvas_mode == "RGBA":
                canvas.paste(img, ((max_w - img.size[0]) // 2, y), img)
            else:
                canvas.paste(img, ((max_w - img.size[0]) // 2, y))
            y += img.size[1]
            img.close()

        c_path = os.path.join(temp_dir, f"temp_stitch_{int(time.time()*1000)}.png")
        canvas.save(c_path, format="PNG")
        return c_path, total_h

    def call_api_batch(self, prompt: str, images: List[str], cancel_event: Optional[threading.Event] = None, current_batch: int = 1, total_batches: int = 1) -> List[str]:
        if not images:
            return []
        
        preferred_model = Config.GEMINI_MODEL
        max_attempts = len(Config.GEMINI_API_KEYS) + 1
        attempts = 0
        
        while attempts < max_attempts:
            attempts += 1
            if cancel_event and cancel_event.is_set():
                self._report_status("Proceso cancelado por el usuario.")
                return ["CANCELLED"] * len(images)

            try:
                current_key = self._wait_and_get_key()
            except GeminiAPIError as e:
                if cancel_event:
                    cancel_event.set()
                return [f"[ERROR API: {e}]"] * len(images)

            master_protocol = self.load_prompt(Config.AI_PROMPT) or "Traduce el manga."
            img_sep = "###---FIN_DE_PAGINA---###"
            
            system_instruction = (
                f"{Config.GEMINI_SYSTEM_INSTRUCTION}\n\n"
                f"{master_protocol}\n\n"
                "INSTRUCCIÓN CRÍTICA DE FORMATO:\n"
                "Debes procesar CADA imagen/sección enviada.\n"
                f"AL FINAL de la traducción de CADA sección visual independiente, DEBES escribir: {img_sep}\n"
            )

            is_gemini_3 = "gemini-3" in Config.GEMINI_MODEL.lower()
            use_ultra_high = is_gemini_3 and Config.GEMINI_ULTRA_HIGH_QUALITY
            # Alturas alineadas a múltiplos de 768px para 0 desperdicio de tiles:
            # Normal: 3072px = 768×4 (4 tiles, 1032 tokens)
            # Ultra:  3840px = 768×5 (5 tiles, 1290 tokens)
            slice_height = 3840 if use_ultra_high else 3072
            
            resolution_enum = types.MediaResolution.MEDIA_RESOLUTION_HIGH
            if use_ultra_high:
                if hasattr(types.PartMediaResolutionLevel, "MEDIA_RESOLUTION_ULTRA_HIGH"):
                    resolution_enum = types.PartMediaResolutionLevel.MEDIA_RESOLUTION_ULTRA_HIGH
                else:
                    use_ultra_high = False
                    slice_height = 3072

            config = types.GenerateContentConfig(
                temperature=1.0,
                system_instruction=system_instruction,
                safety_settings=[
                    types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
                    for c in [
                        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT
                    ]
                ]
            )
            thinking_config = self._build_thinking_config(Config.GEMINI_MODEL)
            if thinking_config is not None:
                config.thinking_config = thinking_config
            if not use_ultra_high:
                config.media_resolution = resolution_enum

            temp_files_to_clean: List[str] = []

            try:
                # --- PREPARACIÓN DE IMÁGENES ---
                # Trocear imágenes largas con alturas alineadas al tiling de 768px
                all_slices: List[str] = []
                for img_p in images:
                    slices = self._slice_long_image(img_p, max_height=slice_height)
                    all_slices.extend(slices)
                    if len(slices) > 1 or "temp_slice" in slices[0]:
                        temp_files_to_clean.extend(slices)

                # --- LÓGICA DE UNIÓN OPCIONAL ---
                # Si el usuario DESACTIVÓ Modo Unión, usamos all_slices directamente.
                # Nota: El usuario pidió que sin unión "es la ia de siempre", es decir, 1 imagen = 1 prompt.
                
                final_api_images = all_slices
                total_sections = len(all_slices)

                self._report_status(f"Enviando {total_sections} secciones a {Config.GEMINI_MODEL}...")

                # BATCH_SIZE restaurado a 3 para evitar límites de tokens de salida
                BATCH_SIZE = 3 
                aggregated_results: List[str] = []
                
                for batch_idx in range(0, len(final_api_images), BATCH_SIZE):
                    batch_paths = final_api_images[batch_idx : batch_idx + BATCH_SIZE]
                    # Cada sub-lote elige la API Key MENOS OCUPADA en ese momento:
                    # con varios sub-lotes/hilos concurrentes el APIKeyPool reparte los
                    # sub-lotes entre TODAS las keys ("a todo trapo"), manteniendo RPM.
                    try:
                        current_key = self._wait_and_get_key()
                    except GeminiAPIError as e:
                        if cancel_event:
                            cancel_event.set()
                        return [f"[ERROR API: {e}]"] * len(images)
                    client = self.get_client(current_key)

                    current_contents = [f"Procesa estas {len(batch_paths)} imágenes. Separa CADA una con {img_sep}"]
                    
                    for img_path in batch_paths:
                        with open(img_path, "rb") as f:
                            data = f.read()
                        mime = mime_for_image(img_path)
                        part_args = {"data": data, "mime_type": mime}
                        if use_ultra_high:
                            part_args["media_resolution"] = resolution_enum
                        current_contents.append(types.Part.from_bytes(**part_args))

                    self._report_status(f"Lote {current_batch}/{total_batches} (sub {batch_idx//BATCH_SIZE + 1}): {len(batch_paths)} imágenes...")
                    
                    api_attempt = 0
                    api_retries = 3
                    retry_delay = 2
                    batch_response_text = ""

                    while api_attempt <= api_retries:
                        if cancel_event and cancel_event.is_set():
                            return ["CANCELLED"] * len(images)

                        try:
                            response = client.models.generate_content(model=Config.GEMINI_MODEL, contents=current_contents, config=config)
                            if response.text:
                                batch_response_text = str(response.text)
                            break
                        except Exception as api_err:
                            err_msg = str(api_err).lower()
                            is_server_busy = "503" in err_msg or "overloaded" in err_msg or "unavailable" in err_msg
                            
                            if is_server_busy:
                                # 1 reintento breve con el mismo modelo (esperanza: pico puntual)
                                if api_attempt < 1:
                                    wait = retry_delay
                                    for i in range(int(wait), 0, -1):
                                        if cancel_event and cancel_event.is_set():
                                            return ["CANCELLED"] * len(images)
                                        self._report_status(f"Servidor ocupado (503). Reintento 1/{api_retries} en {i}s...")
                                        time.sleep(1)
                                    api_attempt += 1
                                    continue

                                # 503 repetido: el modelo está saturado → cambiar de modelo YA.
                                if self._try_switch_model():
                                    self._report_status(f"Servidor ocupado (503). Cambiando modelo a {Config.GEMINI_MODEL}...")
                                    client = self.get_client(current_key)
                                    api_attempt = 0
                                    continue

                                self._report_status(f"Servidor ocupado (503) y sin modelos alternativos. Error: {str(api_err)[:60]}...")
                                raise api_err
                            
                            self._report_status(f"Error en lote: {str(api_err)[:100]}")
                            raise api_err
                    
                    if batch_response_text:
                        parts = [p.strip() for p in batch_response_text.split(img_sep) if p.strip()]
                        while len(parts) < len(batch_paths):
                            parts.append("[Error: Sección faltante]")
                        aggregated_results.extend(parts[:len(batch_paths)])
                    else:
                        aggregated_results.extend(["[ERROR: Sin respuesta]"] * len(batch_paths))

                self._report_status(f"Traducido con éxito ({len(aggregated_results)} secciones).")
                return aggregated_results

            except Exception as e:
                error_str = str(e).lower()
                err_kind = _classify_api_error(e)

                # KEY INVÁLIDA/REVOCADA (real): marcar como agotada y rotar.
                if err_kind == "key":
                    if current_key:
                        APIKeyPool().mark_exhausted(current_key)
                    self._report_status(f"API Key rechazada. Marcando como inválida y rotando. {str(e)[:60]}")
                    continue

                # Error de request/modelo determinístico con key válida:
                # NO quemar la key, NO rotar, NO contar la reserva.
                if err_kind == "request":
                    self._report_status(f"Error de petición: {str(e)[:80]}... (la petición/modelo es inválida, no la API key)")
                    return [f"[ERROR API: {e}]"] * len(images)

                if err_kind == "quota_daily":
                    if current_key:
                        APIKeyPool().mark_exhausted(current_key)
                    self._report_status(f"Cuota diaria alcanzada en la llave actual. Agotando para hoy y rotando...")
                    continue

                if err_kind == "quota_tpm":
                    APIKeyPool().mark_tpm_limit(current_key, 60.0)
                    self._report_status(f"Límite TPM (429) en llave actual. Rotando...")
                    continue

                if err_kind == "server":
                    if self._try_switch_model():
                        self._report_status(f"Servidor ocupado. Cambiando modelo a {Config.GEMINI_MODEL}...")
                        continue
                    # Todos los modelos ocupados: probar con otra API Key (sin quemarla)
                    if self._try_next_key_busy():
                        continue
                    self._report_status(f"Servidor ocupado y sin modelos alternativos. Error: {str(e)[:60]}...")
                    return [f"[ERROR API: {e}]"] * len(images)

                self._report_status(f"Error final: {str(e)[:50]}...")
                return [f"[ERROR API: {e}]"] * len(images)
            finally:
                for temp_file in temp_files_to_clean:
                    try: 
                        if os.path.exists(temp_file): os.remove(temp_file)
                    except Exception: pass
        
        return ["[ERROR: Keys agotadas]"] * len(images)

    def _process_chunks_parallel(self, chunks: List[Tuple[int, List[str]]], cancel_event: Any, total_batches: Optional[int] = None) -> Tuple[str, List[str], Optional[str]]:
        """Procesa los lotes de imágenes repartidos entre TODAS las API Keys, en paralelo.

        chunk = (ordinal_1based, rutas). Con N keys se mandan hasta N lotes a la vez
        ("a todo trapo"): cada lote entra a call_api_batch, que por cada sub-lote pide
        la key MENOS OCUPADA al APIKeyPool, así los lotes corren en keys distintas.

        Preserva el ORDEN final de salida (se reconstruye por ordinal del lote).

        Retorna (estado, textos_en_orden, mensaje_error):
          estado ∈ {"success", "cancelled", "error"}."""
        total = total_batches or len(chunks)
        if not chunks:
            return "error", [], "No hay imágenes para procesar."
        if cancel_event and cancel_event.is_set():
            return "cancelled", [], None

        def run_one(chunk: Tuple[int, List[str]]) -> Tuple[int, List[str]]:
            n, paths = chunk
            res = self.call_api_batch("", paths, cancel_event=cancel_event,
                                      current_batch=n, total_batches=total)
            return n, res

        if len(chunks) == 1:
            _, res = run_one(chunks[0])
            if res and res[0] == "CANCELLED":
                return "cancelled", [], None
            if res and res[0].startswith("[ERROR"):
                return "error", [], res[0]
            return "success", res, None

        num_keys = len(Config.GEMINI_API_KEYS)
        workers = max(1, min(num_keys, len(chunks)))
        self._report_status(
            f"Modo todo-trapo activado: {len(chunks)} lotes repartidos en "
            f"{num_keys} API Keys ({workers} hilos en paralelo)..."
        )

        results: Dict[int, List[str]] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_n = {executor.submit(run_one, c): c[0] for c in chunks}
            for future in as_completed(future_to_n):
                n = future_to_n[future]
                try:
                    _, res = future.result()
                except Exception as e:
                    if cancel_event:
                        cancel_event.set()
                    executor.shutdown(wait=False, cancel_futures=True)
                    return "error", [], str(e)

                results[n] = res
                if res and res[0] == "CANCELLED":
                    if cancel_event:
                        cancel_event.set()
                    executor.shutdown(wait=False, cancel_futures=True)
                    self._report_status("Proceso cancelado por el usuario.")
                    return "cancelled", [], None
                if res and res[0].startswith("[ERROR"):
                    # No seguir saturando las keys: abortamos los lotes que faltan.
                    if cancel_event:
                        cancel_event.set()
                    executor.shutdown(wait=False, cancel_futures=True)
                    self._report_status(f"Error en lote {n}: {res[0][:80]}... Cancelando los lotes restantes.")
                    return "error", [], res[0]

        ordered: List[str] = []
        for n in sorted(results):
            ordered.extend(results[n])
        return "success", ordered, None

    def process_chapter(self, chapter_path: str, output_dir: str, cancel_event: Any, input_base: str) -> str:
        image_files: List[str] = []
        for root, _, files in os.walk(chapter_path):
            for f in files:
                if f.lower().endswith(Config.SUPPORTED_FORMATS):
                    image_files.append(os.path.join(root, f))
        
        image_files.sort(key=lambda f: [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', f)])
        if not image_files:
            return "error"

        # Preparar ruta de salida
        chapter_name = os.path.basename(chapter_path)
        rel_path = os.path.relpath(chapter_path, input_base)
        if rel_path.startswith(".."): rel_path = chapter_name
        full_output_dir = os.path.join(output_dir, rel_path)
        os.makedirs(full_output_dir, exist_ok=True)

        # --- MODO UNIÓN (SIN IA) ---
        if Config.GEMINI_STITCHING_ONLY:
            self._report_status(f"MODO UNIÓN ACTIVADO: Uniendo {len(image_files)} imágenes...")
            is_gemini_3 = "gemini-3" in Config.GEMINI_MODEL.lower()
            use_ultra_high = is_gemini_3 and Config.GEMINI_ULTRA_HIGH_QUALITY
            # Canvas alineado a múltiplos de 768px (idéntico al slice_height de la IA)
            canvas_height_limit = 3840 if use_ultra_high else 3072
            # Umbral mínimo: si el último lienzo queda más pequeño que esto,
            # se fusiona con el anterior para evitar un tile de Gemini casi vacío.
            min_useful_canvas = int(canvas_height_limit * 0.4)
            
            # Usar la lógica de stitching para crear las nuevas imágenes
            current_canvas_paths: List[str] = []
            current_h = 0
            stitch_count = 1
            saved_canvases: List[Tuple[str, List[str], int]] = []  # (path, source_imgs, height)
            
            for img_p in image_files:
                if cancel_event and cancel_event.is_set(): return "cancelled"
                
                # Liberar GIL brevemente para evitar que la UI se congele
                time.sleep(0.005)

                with Image.open(img_p) as img:
                    h = img.size[1]
                
                if h >= canvas_height_limit or (current_h + h > canvas_height_limit and current_canvas_paths):
                    if current_canvas_paths:
                        c_path, _ = self._stitch_and_save(current_canvas_paths, full_output_dir)
                        final_p = os.path.join(full_output_dir, f"stitched_{stitch_count:03d}.png")
                        if os.path.exists(final_p): os.remove(final_p)
                        os.rename(c_path, final_p)
                        saved_canvases.append((final_p, list(current_canvas_paths), current_h))
                        stitch_count += 1
                        current_canvas_paths = []
                        current_h = 0
                    
                    if h >= canvas_height_limit:
                        # Si es una sola imagen muy grande: se preserva sin re-codificar
                        # (sin pérdida). Solo los JPEG se convierten a PNG lossless.
                        img_ext = os.path.splitext(img_p)[1].lower()
                        if img_ext in (".png", ".webp", ".gif", ".bmp", ".heic", ".heif"):
                            final_p = os.path.join(full_output_dir, f"stitched_{stitch_count:03d}{img_ext}")
                            shutil.copy2(img_p, final_p)
                        else:
                            final_p = os.path.join(full_output_dir, f"stitched_{stitch_count:03d}.png")
                            with Image.open(img_p) as img:
                                if img.mode == "P":
                                    img = img.convert("RGBA")
                                img.save(final_p, format="PNG")
                        saved_canvases.append((final_p, [img_p], h))
                        stitch_count += 1
                    else:
                        current_canvas_paths = [img_p]
                        current_h = h
                else:
                    current_canvas_paths.append(img_p)
                    current_h += h
            
            # --- PROTECCIÓN ÚLTIMO LIENZO ---
            # Si el último lote de imágenes genera un canvas muy pequeño,
            # lo fusionamos con el canvas anterior para evitar desperdicio.
            if current_canvas_paths:
                if current_h < min_useful_canvas and saved_canvases:
                    # Fusionar: deshacer el último canvas guardado y re-unir todo junto
                    prev_path, prev_sources, prev_h = saved_canvases.pop()
                    stitch_count -= 1
                    try:
                        if os.path.exists(prev_path): os.remove(prev_path)
                    except Exception: pass
                    merged_sources = prev_sources + current_canvas_paths
                    c_path, _ = self._stitch_and_save(merged_sources, full_output_dir)
                    final_p = os.path.join(full_output_dir, f"stitched_{stitch_count:03d}.png")
                    if os.path.exists(final_p): os.remove(final_p)
                    os.rename(c_path, final_p)
                    self._report_status(f"Último lienzo fusionado con el anterior ({current_h}px < {min_useful_canvas}px mínimo).")
                else:
                    c_path, _ = self._stitch_and_save(current_canvas_paths, full_output_dir)
                    final_p = os.path.join(full_output_dir, f"stitched_{stitch_count:03d}.png")
                    if os.path.exists(final_p): os.remove(final_p)
                    os.rename(c_path, final_p)

            self._report_status(f"MODO UNIÓN COMPLETADO: {stitch_count} lienzos creados en {full_output_dir}")
            return "success"

        # --- MODO IA ESTÁNDAR (SIN UNIÓN) ---
        chunk_size = 5 # Restaurado al valor original
        chunks = [(n, image_files[i:i + chunk_size])
                  for n, i in enumerate(range(0, len(image_files), chunk_size), 1)]
        logging.info(f"Procesando capítulo con {Config.GEMINI_MODEL} | Lote: {chunk_size} | Total Lotes: {len(chunks)}")

        state, all_texts, err_msg = self._process_chunks_parallel(chunks, cancel_event)
        if state == "cancelled":
            return "cancelled"
        if state == "error":
            return f"Error: {err_msg}"

        if all_texts:
            # 1. Intentar el guardado oficial (con análisis)
            try:
                self.combine_texts(full_output_dir, cast(List[Optional[str]], all_texts), chapter_name)
            except Exception as e:
                logging.error(f"Error en combine_texts: {e}")

            # 2. Respaldo de seguridad: Escribir el archivo TXT directamente si el anterior falló o para asegurar visibilidad
            try:
                final_txt_path = os.path.join(full_output_dir, f"{chapter_name}_completo.txt")
                with open(final_txt_path, "w", encoding="utf-8") as f:
                    f.write(f"CAPÍTULO: {chapter_name}\n{'='*50}\n\n")
                    for idx, texto in enumerate(all_texts, 1):
                        f.write(f"PAGINA {idx}\n{'-'*50}\n{texto}\n\n")
                        f.write("-" * 75 + "\n\n")
                logging.info(f"Archivo final guardado en: {final_txt_path}")
            except Exception as e:
                logging.error(f"Error en guardado de seguridad: {e}")
            
            # 3. Limpiar archivo de progreso
            try:
                prog_file = os.path.join(full_output_dir, f"{chapter_name}_progreso.txt")
                if os.path.exists(prog_file): os.remove(prog_file)
            except: pass
            
            return "success"
        
        return "error"

    def process_selected_files_gemini(self, file_paths: List[str], output_dir: str, cancel_event: Any, callback: Any):
        if not file_paths:
            if callback:
                callback("error", "No se seleccionaron archivos.")
            return

        file_paths.sort(key=lambda f: [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', f)])

        chunk_size = 20
        chunks = [(n, file_paths[i:i + chunk_size])
                  for n, i in enumerate(range(0, len(file_paths), chunk_size), 1)]
        logging.info(f"Procesando {len(file_paths)} archivos con {Config.GEMINI_MODEL} | Lote: {chunk_size} | Total Lotes: {len(chunks)}")

        success_status = "success"

        state, all_texts, err_msg = self._process_chunks_parallel(chunks, cancel_event)
        if state == "cancelled":
            success_status = "cancelled"
        elif state == "error":
            success_status = "error"
            if callback:
                callback("error_gemini_api", err_msg or "Error de API")
            return

        if success_status == "success" and all_texts:
            first_dir = os.path.dirname(file_paths[0])
            chapter_name = os.path.basename(first_dir)
            self.combine_texts(output_dir, cast(List[Optional[str]], all_texts), f"{chapter_name}_seleccion")
            if callback:
                callback("success")
        elif success_status == "cancelled":
            if callback:
                callback("cancelled")
        else:
            if callback:
                callback("error", "No se generó contenido. Verifica que los archivos sean válidos.")


    