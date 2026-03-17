import logging
import os
import re
import time
import threading
from typing import List, Optional, Any, Tuple, Dict, cast, Set
from concurrent.futures import ThreadPoolExecutor

# pylint: disable=no-name-in-module, import-error
import google.genai as genai
from google.genai import types

from PIL import Image

from app_tools.ai_service import BaseAIProcessor, AIAPIError
from config import Config

class GeminiAPIError(AIAPIError):
    pass

class GeminiProcessor(BaseAIProcessor):
    def __init__(self):
        super().__init__(model_name="Gemini")
        self._last_request_time = 0.0
        self._check_and_reset_daily_quota()
        self._exhausted_keys: Set[str] = set()
        self._failed_models: Set[str] = set() # Modelos que han fallado con la key actual

    def _check_and_reset_daily_quota(self):
        """Reinicia el contador diario si ha cambiado el día."""
        today = time.strftime("%Y-%m-%d")
        if Config.LAST_REQUEST_DATE != today:
            Config.DAILY_REQUEST_COUNT = 0
            Config.LAST_REQUEST_DATE = today
            Config.save_user_settings({
                "DAILY_REQUEST_COUNT": 0, 
                "LAST_REQUEST_DATE": today
            })

    def _increment_daily_count(self):
        """Incrementa y guarda el uso diario."""
        Config.DAILY_REQUEST_COUNT += 1
        Config.save_user_settings({
            "DAILY_REQUEST_COUNT": Config.DAILY_REQUEST_COUNT,
            "LAST_REQUEST_DATE": Config.LAST_REQUEST_DATE
        })

    def _get_current_limits(self) -> Dict[str, int]:
        model = Config.GEMINI_MODEL.lower()
        for m_name, limits in Config.MODEL_LIMITS.items():
            if m_name in model:
                return limits
        return {"RPM": 5, "TPM": 250000, "RPD": 20}

    def _wait_for_rate_limit(self):
        self._check_and_reset_daily_quota()
        limits = self._get_current_limits()

        if Config.DAILY_REQUEST_COUNT >= limits["RPD"]:
            msg = f"Límite diario alcanzado ({limits['RPD']} RPD). Espera hasta mañana."
            self._report_status(msg)
            raise GeminiAPIError(msg)

        min_interval = 60.0 / limits["RPM"]
        elapsed = time.time() - self._last_request_time
        
        if elapsed < min_interval:
            wait_needed = min_interval - elapsed
            self._report_status(f"Respetando RPM ({limits['RPM']}). Pausando {wait_needed:.1f}s...")
            time.sleep(wait_needed)

    def get_client(self, api_key: Optional[str] = None) -> Any: # type: ignore
        return genai.Client(
            api_key=api_key or Config.GEMINI_API_KEY,
            http_options={'api_version': 'v1alpha'}
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
            
            ALLOWED_FAMILIES = ["gemini-2.5-flash", "gemini-3-flash-preview", "gemini-flash", "gemini-3.1-flash-lite-preview"]
            FORBIDDEN_TERMS = [
                "pro", "2.0", "deep-research", "nano", "audio", "tts", 
                "embedding", "aqa", "gemma", "image", "face", "screen",
                "preview", "latest"
            ]

            for model in models_iter:
                model_name = getattr(model, 'name', '')
                if not model_name:
                    continue
                name: str = str(model_name).lower().replace("models/", "")
                is_gemini_3 = "gemini-3" in name
                
                if any(bad in name for bad in FORBIDDEN_TERMS):
                    if is_gemini_3 and "preview" in name and not any(other_bad in name for other_bad in ["image", "audio", "tts"]):
                        pass 
                    else:
                        continue
                if not any(good in name for good in ALLOWED_FAMILIES):
                    continue
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
                return ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3-flash-preview"]
            return available_models
        except Exception as e:
            logging.error(f"Error obteniendo modelos: {e}")
            return ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3-flash-preview"]

    def _try_switch_model(self) -> bool:
        """Intenta cambiar a otro modelo disponible si el actual falla."""
        current = Config.GEMINI_MODEL.lower()
        self._failed_models.add(current)
        
        # Lista de prioridad de fallback (De más potente/caro a más ligero)
        # Se asume que si falla uno, intentamos el siguiente.
        fallback_priority = [
            "gemini-3-flash-preview", 
            "gemini-2.5-flash", 
            "gemini-2.5-flash-lite"
        ]
        
        # Buscar el primer modelo de la lista que NO haya fallado aún
        for model in fallback_priority:
            # Check laxo para coincidir con nombres de API que pueden variar ligeramente
            # Si el modelo exacto no está en failed_models, lo usamos.
            is_failed = False
            for failed in self._failed_models:
                if model in failed or failed in model:
                    is_failed = True
                    break
            
            if not is_failed:
                self._report_status(f"Fallo en {Config.GEMINI_MODEL}. Cambiando a {model}...")
                Config.GEMINI_MODEL = model
                return True
                
        return False

    def _rotate_key(self) -> bool:
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

    def _reset_model_to_default(self):
        """Resetea el modelo al preferido al cambiar de API Key."""
        default_model = "gemini-2.5-flash"
        self._report_status(f"Nueva Key: Reseteando modelo a {default_model}")
        Config.GEMINI_MODEL = default_model

    def call_api(self, prompt: str, image_path: Optional[str] = None, content: Optional[str] = None) -> str:
        if image_path:
            results = self.call_api_batch(prompt, [image_path])
            return results[0] if results else ""
        
        self._wait_for_rate_limit()
        
        max_retries = 3
        base_delay = 2

        for attempt in range(max_retries + 1):
            try:
                client = self.get_client()
                response = client.models.generate_content(
                    model=Config.GEMINI_MODEL,
                    contents=[f"{prompt}\n\n{content}"],
                    config=types.GenerateContentConfig(temperature=Config.GEMINI_TEMPERATURE)
                )
                self._last_request_time = time.time()
                self._increment_daily_count()
                return str(response.text).strip() if response.text else ""
            
            except Exception as e:
                error_str = str(e).lower()
                # Errores transitorios de servidor (Bug 1 y 2)
                is_server_error = "503" in error_str or "disconnected" in error_str or "unavailable" in error_str or "overloaded" in error_str
                
                if is_server_error and attempt < max_retries:
                    wait_time = base_delay * (2 ** attempt) # Exponential backoff
                    self._report_status(f"Error servidor ({error_str[:30]}...). Reintentando en {wait_time}s (Intento {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                
                self._report_status(f"Error API: {str(e)[:50]}...")
                raise GeminiAPIError(str(e))
        return ""

    def _slice_long_image(self, img_path: str, max_height: int = 3000, overlap: int = 400) -> List[str]:
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

                self._report_status(f"Procesando {len(slices_info)} trozos en paralelo para: {base_name}")
                
                def process_single_slice(info: Tuple[int, int, int]):
                    t, b, p = info
                    with Image.open(img_path) as thread_img:
                        cropped = thread_img.crop((0, t, width, b))
                        if cropped.mode in ("RGBA", "P"):
                            cropped = cropped.convert("RGB")
                        s_path = os.path.join(temp_dir, f"temp_slice_{base_name}_{p}.jpg")
                        cropped.save(s_path, format="JPEG", quality=80, optimize=False)
                        if os.path.getsize(s_path) > 6.8 * 1024 * 1024:
                            low_res = cropped.resize((int(cropped.width * 0.7), int(cropped.height * 0.7)), Image.Resampling.BILINEAR)
                            low_res.save(s_path, format="JPEG", quality=75, optimize=True)
                        return p, s_path

                with ThreadPoolExecutor() as executor:
                    results = list(executor.map(process_single_slice, slices_info))
                
                results.sort(key=lambda x: x[0])
                return [r[1] for r in results]
        except Exception as e:
            logging.error(f"Error en troceado paralelo: {e}")
            return [img_path]

    def call_api_batch(self, prompt: str, images: List[str], cancel_event: Optional[threading.Event] = None, current_batch: int = 1, total_batches: int = 1) -> List[str]:
        if not images:
            return []
        
        # Guardar el modelo preferido original para restaurarlo al rotar keys
        preferred_model = Config.GEMINI_MODEL
        
        # Bucle de intentos limitado a la cantidad de Keys disponibles
        # Si tienes 5 keys, probará máximo 5 veces.
        max_attempts = len(Config.GEMINI_API_KEYS) + 1
        attempts = 0
        
        while attempts < max_attempts:
            attempts += 1
            
            # 1. Chequeo de cancelación
            if cancel_event and cancel_event.is_set():
                self._report_status("Proceso cancelado por el usuario.")
                return ["CANCELLED"] * len(images)

            # 2. Control de tasa
            try:
                self._wait_for_rate_limit()
            except GeminiAPIError as e:
                # Si falla el chequeo local de cuota, intentamos rotar inmediatamente
                if "Límite diario" in str(e) or "Resource Exhausted" in str(e):
                    if self._rotate_key():
                        continue # Reintentar con nueva key
                    else:
                        return [f"[ERROR API: {e}]"] * len(images)
                raise e

            master_protocol = self.load_prompt(Config.AI_PROMPT) or "Traduce el manga."
            img_sep = "###---FIN_DE_PAGINA---###"
            
            system_instruction = (
                f"{Config.GEMINI_SYSTEM_INSTRUCTION}\n\n"
                f"{master_protocol}\n\n"
                "INSTRUCCIÓN CRÍTICA DE FORMATO:\n"
                f"Debes procesar CADA imagen por separado.\n"
                f"AL FINAL de la traducción de CADA imagen, DEBES escribir: {img_sep}\n"
                "Si no usas este separador exacto, el sistema fallará.\n"
                "Ejemplo:\n"
                "[Traducción Imagen 1]\n"
                f"{img_sep}\n"
                "[Traducción Imagen 2]\n"
                f"{img_sep}\n"
            )

            is_gemini_3 = "gemini-3" in Config.GEMINI_MODEL.lower()
            
            # Lógica de Ultra High Quality controlada por el usuario
            # Solo permitimos ULTRA_HIGH si el usuario lo activó Y el modelo es Gemini 3 (único que lo soporta)
            use_ultra_high = is_gemini_3 and Config.GEMINI_ULTRA_HIGH_QUALITY

            # Ajuste dinámico de recorte
            # Si Ultra High está activo -> 4500px (aprovecha los 2240 tokens)
            # Si no -> 3000px (estándar seguro para 1120 tokens)
            slice_height = 4500 if use_ultra_high else 3000
            
            # Obtener el valor del Enum de forma segura (fallback a HIGH si la librería es antigua)
            resolution_enum = types.MediaResolution.MEDIA_RESOLUTION_HIGH
            if use_ultra_high:
                if hasattr(types.MediaResolution, "MEDIA_RESOLUTION_ULTRA_HIGH"):
                    resolution_enum = getattr(types.MediaResolution, "MEDIA_RESOLUTION_ULTRA_HIGH")
                else:
                    self._report_status("Tu versión de google-genai no soporta ULTRA_HIGH. Usando HIGH.")
                    use_ultra_high = False # Desactivar modo Ultra High para evitar errores lógicos abajo
                    slice_height = 3000 # Revertir recorte

            # Configuración global limpia
            gen_config_args: Dict[str, Any] = {
                "temperature": 1.0,
                "system_instruction": system_instruction,
                "safety_settings": [
                    types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
                    for c in [
                        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT
                    ]
                ]
            }
            
            # Activar Thinking si el usuario lo solicitó
            if Config.GEMINI_ENABLE_THINKING:
                gen_config_args["thinking_config"] = types.ThinkingConfig(include_thoughts=True)
            
            # Solo aplicamos resolución global si NO es Ultra High (para evitar warnings/errores en Gemini 3)
            # Si es Ultra High, se aplica por parte abajo.
            if not use_ultra_high:
                gen_config_args["media_resolution"] = resolution_enum

            config = types.GenerateContentConfig(**gen_config_args)

            final_images_list: List[str] = []
            temp_slices: List[str] = []

            try:
                if cancel_event and cancel_event.is_set():
                    return ["CANCELLED"] * len(images)

                for img_p in images:
                    # Usamos el slice_height dinámico
                    slices = self._slice_long_image(img_p, max_height=slice_height)
                    final_images_list.extend(slices)
                    if len(slices) > 1:
                        temp_slices.extend(slices)

                client = self.get_client()
                contents: List[Any] = [f"Procesa {len(final_images_list)} imágenes. Separa con {img_sep}"]
                
                for img_path in final_images_list:
                    with open(img_path, "rb") as f:
                        data = f.read()
                    mime = "image/png" if img_path.lower().endswith(".png") else "image/jpeg"
                    
                    # APLICACIÓN DE RESOLUCIÓN PER-PART
                    part_args: Dict[str, Any] = {"data": data, "mime_type": mime}
                    # Si estamos en modo Ultra High, forzamos la resolución en la parte
                    if use_ultra_high:
                        part_args["media_resolution"] = resolution_enum
                        
                    contents.append(types.Part.from_bytes(**part_args))

                # Mensaje acortado para evitar truncamiento en UI
                mode_str = "Ultra" if use_ultra_high else "Std"
                self._report_status(f"Procesando {len(final_images_list)} secciones a {Config.GEMINI_MODEL} ({slice_height}px, {mode_str})...")

                # --- PROCESAMIENTO POR LOTES INTERNOS (CORRECCIÓN LONG-STRIP) ---
                # En lugar de enviar 25 slices de golpe, enviamos de 5 en 5 para evitar límite de tokens de salida.
                BATCH_SIZE = 5
                aggregated_results: List[str] = []
                
                # Bucle de reintentos API (ahora envuelve a los lotes internos)
                # NOTA: Si falla un lote, la estrategia actual es reintentar SOLO ese lote.
                
                for batch_idx in range(0, len(final_images_list), BATCH_SIZE):
                    batch_slices = final_images_list[batch_idx : batch_idx + BATCH_SIZE]
                    current_contents = [f"Procesa estas {len(batch_slices)} secciones de imagen. Separa con {img_sep}"]
                    
                    # Preparar contenido para este lote
                    for img_path in batch_slices:
                        with open(img_path, "rb") as f:
                            data = f.read()
                        mime = "image/png" if img_path.lower().endswith(".png") else "image/jpeg"
                        part_args = {"data": data, "mime_type": mime}
                        if use_ultra_high:
                            part_args["media_resolution"] = resolution_enum
                        current_contents.append(types.Part.from_bytes(**part_args))

                    self._report_status(f"Enviando lote {current_batch}/{total_batches} (sub-lote {batch_idx//BATCH_SIZE + 1} de { (len(final_images_list) + BATCH_SIZE - 1) // BATCH_SIZE })...")
                    
                    # BUCLE DE REINTENTOS PARA ESTE LOTE ESPECÍFICO
                    api_retries = 3
                    retry_delay = 2
                    batch_response_text = ""
                    fallback_applied = False # Resetear flag por lote (o mantener global si se prefiere consistencia)

                    for api_attempt in range(api_retries + 1):
                        try:
                            response = client.models.generate_content(model=Config.GEMINI_MODEL, contents=current_contents, config=config)
                            if response.text:
                                batch_response_text = str(response.text)
                            break # Éxito del lote
                        except Exception as api_err:
                            err_msg = str(api_err).lower()
                            
                            # FALLBACK ULTRA_HIGH
                            if "ULTRA_HIGH" in err_msg and use_ultra_high and not fallback_applied:
                                self._report_status("Modelo rechazó ULTRA_HIGH. Cambiando a HIGH para este lote...")
                                # Reconstruir lote sin resolución
                                current_contents_fallback = [current_contents[0]]
                                for img_path in batch_slices:
                                    with open(img_path, "rb") as f:
                                        data = f.read()
                                    mime = "image/png" if img_path.lower().endswith(".png") else "image/jpeg"
                                    current_contents_fallback.append(types.Part.from_bytes(data=data, mime_type=mime))
                                config.media_resolution = types.MediaResolution.MEDIA_RESOLUTION_HIGH
                                current_contents = current_contents_fallback
                                fallback_applied = True
                                continue 

                            # ERROR SERVIDOR
                            is_server_issue = "503" in err_msg or "disconnected" in err_msg or "unavailable" in err_msg or "overloaded" in err_msg
                            if is_server_issue and api_attempt < api_retries:
                                wait = retry_delay * (2 ** api_attempt)
                                self._report_status(f"Servidor ocupado. Esperando {wait}s...")
                                time.sleep(wait)
                                continue
                            
                            raise api_err # Fallo irrecuperable
                    
                    # Procesar respuesta del lote
                    if batch_response_text:
                        parts = [p.strip() for p in batch_response_text.split(img_sep) if p.strip()]
                        # Rellenar si faltan partes en la respuesta del modelo
                        while len(parts) < len(batch_slices):
                            parts.append("[Error: Respuesta parcial en lote]")
                        aggregated_results.extend(parts[:len(batch_slices)])
                    else:
                        aggregated_results.extend(["[ERROR: Sin respuesta de lote]"] * len(batch_slices))

                    # Pequeña pausa entre lotes para ser amables con la API
                    time.sleep(1)

                self._report_status(f"Proceso completado. {len(aggregated_results)} secciones traducidas.")
                self._last_request_time = time.time()
                self._increment_daily_count()
                
                return aggregated_results

            except Exception as e:
                # Gestión de errores de API (Cuota agotada)
                error_str = str(e).lower()
                is_quota_error = "429" in error_str or "exhausted" in error_str or "quota" in error_str or "resource" in error_str
                
                # Limpiar temporales
                for temp_file in temp_slices:
                    try: 
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                    except Exception:
                        pass
                
                if is_quota_error:
                    self._report_status("Cuota agotada en Key actual. Intentando estrategias de recuperación...")
                    
                    # 1. Intentar cambiar de modelo en la misma Key (si está habilitado)
                    if Config.ENABLE_AUTO_MODEL_SWITCH:
                        if self._try_switch_model():
                            continue # Reintentar con nuevo modelo
                    
                    # 2. Si no se puede cambiar modelo o fallaron todos, rotar Key
                    if self._rotate_key():
                        # Al rotar key, restauramos el modelo preferido para intentar máxima calidad de nuevo
                        if Config.GEMINI_MODEL != preferred_model:
                            self._report_status(f"Nueva Key: Restaurando modelo preferido ({preferred_model})...")
                            Config.GEMINI_MODEL = preferred_model
                        continue # Reintentar con nueva key (y modelo reseteado)
                
                # Si no es error de cuota o la rotación falló, reportamos el error final y salimos.
                logging.error(f"Error Gemini API: {e}")
                self._report_status(f"Error final: {str(e)[:50]}...")
                return [f"[ERROR API: {e}]"] * len(images)
            
            finally:
                for temp_file in temp_slices:
                    try:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                    except Exception:
                        pass
        
        return ["[ERROR API: Todas las keys agotadas]"] * len(images)

    def process_chapter(self, chapter_path: str, output_dir: str, cancel_event: Any, input_base: str) -> str:
        image_files: List[str] = []
        for root, _, files in os.walk(chapter_path):
            for f in files:
                if f.lower().endswith(Config.SUPPORTED_FORMATS):
                    image_files.append(os.path.join(root, f))
        
        image_files.sort(key=lambda f: [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', f)])
        if not image_files:
            return "error"

        chunk_size = 5
        total_batches = (len(image_files) + chunk_size - 1) // chunk_size
        logging.info(f"Procesando capítulo con {Config.GEMINI_MODEL} | Lote: {chunk_size} | Total Lotes: {total_batches}")

        all_texts: List[str] = []
        for i in range(0, len(image_files), chunk_size):
            if cancel_event and cancel_event.is_set():
                return "cancelled"
            
            current_batch_num = (i // chunk_size) + 1
            chunk = image_files[i : i + chunk_size]
            results = self.call_api_batch(
                "", chunk, cancel_event=cancel_event, current_batch=current_batch_num, total_batches=total_batches
            )
            
            # Verificación de fallo inmediato
            if results and results[0] == "CANCELLED":
                return "cancelled"
            if results and results[0].startswith("[ERROR"):
                return f"Error: {results[0]}"
            
            all_texts.extend(results)

        if all_texts:
            chapter_name = os.path.basename(chapter_path)
            rel_path = os.path.relpath(chapter_path, input_base)
            self.combine_texts(os.path.join(output_dir, rel_path), cast(List[Optional[str]], all_texts), chapter_name)
            return "success"
        return "error"

    def process_selected_files_gemini(self, file_paths: List[str], output_dir: str, cancel_event: Any, callback: Any):
        if not file_paths:
            return

        file_paths.sort(key=lambda f: [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', f)])

        chunk_size = 5
        total_batches = (len(file_paths) + chunk_size - 1) // chunk_size
        logging.info(f"Procesando {len(file_paths)} archivos con {Config.GEMINI_MODEL} | Lote: {chunk_size} | Total Lotes: {total_batches}")
        
        all_texts: List[str] = []
        success_status = "success"

        for i in range(0, len(file_paths), chunk_size):
            if cancel_event and cancel_event.is_set():
                success_status = "cancelled"
                break
            
            current_batch_num = (i // chunk_size) + 1
            chunk = file_paths[i : i + chunk_size]
            results = self.call_api_batch(
                "", chunk, cancel_event=cancel_event, current_batch=current_batch_num, total_batches=total_batches
            )
            
            # Verificación de fallo inmediato
            if results and results[0] == "CANCELLED":
                success_status = "cancelled"
                break
            if results and results[0].startswith("[ERROR"):
                success_status = "error"
                # Pasamos el error real al callback
                if callback:
                    callback("error_gemini_api", results[0])
                return # Salir inmediatamente

            all_texts.extend(results)

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

    