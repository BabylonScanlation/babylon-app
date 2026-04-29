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
                return ["gemini-3.1-flash-lite-preview", "gemini-3-flash-preview", "gemini-2.5-flash"]
            return available_models
        except Exception as e:
            logging.error(f"Error obteniendo modelos: {e}")
            return ["gemini-3.1-flash-lite-preview", "gemini-3-flash-preview", "gemini-2.5-flash"]

    def _try_switch_model(self) -> bool:
        """Intenta cambiar a otro modelo disponible si el actual falla."""
        current = Config.GEMINI_MODEL.lower()
        self._failed_models.add(current)
        
        # Definir una jerarquía de fallback lógica basada en tus modelos disponibles
        # Orden: 3.1-lite -> 3-preview -> 2.5-flash
        hierarchy = [
            "gemini-3.1-flash-lite-preview",
            "gemini-3-flash-preview",
            "gemini-2.5-flash"
        ]
        
        # Buscar el siguiente modelo en la jerarquía que no haya fallado
        for model in hierarchy:
            if model in Config.MODEL_LIMITS and model not in self._failed_models:
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
                    for i in range(int(wait_time), 0, -1):
                        self._report_status(f"Servidor ocupado. Reintento {attempt+1}/{max_retries} en {i}s...")
                        time.sleep(1)
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
        """Une imágenes verticalmente y las guarda."""
        if len(paths) == 1:
            with Image.open(paths[0]) as img:
                return paths[0], img.size[1]

        images = [Image.open(p) for p in paths]
        max_w = max(img.size[0] for img in images)
        total_h = sum(img.size[1] for img in images)
        
        canvas = Image.new("RGB", (max_w, total_h), (255, 255, 255))
        y = 0
        for img in images:
            x = (max_w - img.size[0]) // 2
            canvas.paste(img, (x, y))
            y += img.size[1]
            img.close()

        c_path = os.path.join(temp_dir, f"temp_stitch_{int(time.time()*1000)}.jpg")
        canvas.save(c_path, format="JPEG", quality=85)
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
                self._wait_for_rate_limit()
            except GeminiAPIError as e:
                if "Límite diario" in str(e) or "Resource Exhausted" in str(e):
                    if self._rotate_key(): continue
                    else: return [f"[ERROR API: {e}]"] * len(images)
                raise e

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
            slice_height = 4500 if use_ultra_high else 3000
            
            resolution_enum = types.MediaResolution.MEDIA_RESOLUTION_HIGH
            if use_ultra_high:
                if hasattr(types.MediaResolution, "MEDIA_RESOLUTION_ULTRA_HIGH"):
                    resolution_enum = getattr(types.MediaResolution, "MEDIA_RESOLUTION_ULTRA_HIGH")
                else:
                    use_ultra_high = False
                    slice_height = 3000

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
            if Config.GEMINI_ENABLE_THINKING:
                config.thinking_config = types.ThinkingConfig(include_thoughts=True)
            if not use_ultra_high:
                config.media_resolution = resolution_enum

            temp_files_to_clean: List[str] = []

            try:
                # --- PREPARACIÓN DE IMÁGENES ---
                # Trocear imágenes largas siempre (comportamiento base necesario)
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

                client = self.get_client()
                self._report_status(f"Enviando {total_sections} secciones a {Config.GEMINI_MODEL}...")

                # BATCH_SIZE restaurado a 3 para evitar límites de tokens de salida
                BATCH_SIZE = 3 
                aggregated_results: List[str] = []
                
                for batch_idx in range(0, len(final_api_images), BATCH_SIZE):
                    batch_paths = final_api_images[batch_idx : batch_idx + BATCH_SIZE]
                    current_contents = [f"Procesa estas {len(batch_paths)} imágenes. Separa CADA una con {img_sep}"]
                    
                    for img_path in batch_paths:
                        with open(img_path, "rb") as f:
                            data = f.read()
                        mime = "image/png" if img_path.lower().endswith(".png") else "image/jpeg"
                        part_args = {"data": data, "mime_type": mime}
                        if use_ultra_high:
                            part_args["media_resolution"] = resolution_enum
                        current_contents.append(types.Part.from_bytes(**part_args))

                    self._report_status(f"Lote {current_batch}/{total_batches} (sub {batch_idx//BATCH_SIZE + 1}): {len(batch_paths)} imágenes...")
                    
                    api_retries = 3
                    retry_delay = 2
                    batch_response_text = ""

                    for api_attempt in range(api_retries + 1):
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
                                if api_attempt < api_retries:
                                    wait = retry_delay * (2 ** api_attempt)
                                    for i in range(int(wait), 0, -1):
                                        if cancel_event and cancel_event.is_set():
                                            return ["CANCELLED"] * len(images)
                                        self._report_status(f"Servidor ocupado (503). Reintento {api_attempt+1}/{api_retries} en {i}s...")
                                        time.sleep(1)
                                    continue
                                else:
                                    if self._try_switch_model():
                                        self._report_status(f"Cambiando modelo a {Config.GEMINI_MODEL}...")
                                        client = self.get_client()
                                        api_attempt = -1 
                                        continue
                            
                            raise api_err
                    
                    if batch_response_text:
                        parts = [p.strip() for p in batch_response_text.split(img_sep) if p.strip()]
                        while len(parts) < len(batch_paths):
                            parts.append("[Error: Sección faltante]")
                        aggregated_results.extend(parts[:len(batch_paths)])
                    else:
                        aggregated_results.extend(["[ERROR: Sin respuesta]"] * len(batch_paths))

                self._report_status(f"Traducido con éxito ({len(aggregated_results)} secciones).")
                self._last_request_time = time.time()
                self._increment_daily_count()
                return aggregated_results

            except Exception as e:
                error_str = str(e).lower()
                is_quota = "429" in error_str or "exhausted" in error_str
                if is_quota:
                    if Config.ENABLE_AUTO_MODEL_SWITCH and self._try_switch_model(): continue
                    if self._rotate_key():
                        if Config.GEMINI_MODEL != preferred_model: Config.GEMINI_MODEL = preferred_model
                        continue
                self._report_status(f"Error final: {str(e)[:50]}...")
                return [f"[ERROR API: {e}]"] * len(images)
            finally:
                for temp_file in temp_files_to_clean:
                    try: 
                        if os.path.exists(temp_file): os.remove(temp_file)
                    except Exception: pass
        
        return ["[ERROR: Keys agotadas]"] * len(images)

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
            canvas_height_limit = 4000
            
            # Usar la lógica de stitching para crear las nuevas imágenes
            current_canvas_paths: List[str] = []
            current_h = 0
            stitch_count = 1
            
            for img_p in image_files:
                if cancel_event and cancel_event.is_set(): return "cancelled"
                
                with Image.open(img_p) as img:
                    h = img.size[1]
                
                if h >= canvas_height_limit or (current_h + h > canvas_height_limit and current_canvas_paths):
                    if current_canvas_paths:
                        c_path, _ = self._stitch_and_save(current_canvas_paths, full_output_dir)
                        # Renombrar para que sea legible
                        final_p = os.path.join(full_output_dir, f"stitched_{stitch_count:03d}.jpg")
                        if os.path.exists(final_p): os.remove(final_p)
                        os.rename(c_path, final_p)
                        stitch_count += 1
                        current_canvas_paths = []
                        current_h = 0
                    
                    if h >= canvas_height_limit:
                        # Si es una sola imagen muy grande, solo la copiamos
                        final_p = os.path.join(full_output_dir, f"stitched_{stitch_count:03d}.jpg")
                        with Image.open(img_p) as img:
                            img.save(final_p, format="JPEG", quality=90)
                        stitch_count += 1
                    else:
                        current_canvas_paths = [img_p]
                        current_h = h
                else:
                    current_canvas_paths.append(img_p)
                    current_h += h
            
            if current_canvas_paths:
                c_path, _ = self._stitch_and_save(current_canvas_paths, full_output_dir)
                final_p = os.path.join(full_output_dir, f"stitched_{stitch_count:03d}.jpg")
                if os.path.exists(final_p): os.remove(final_p)
                os.rename(c_path, final_p)

            self._report_status(f"MODO UNIÓN COMPLETADO: {stitch_count} lienzos creados en {full_output_dir}")
            return "success"

        # --- MODO IA ESTÁNDAR (SIN UNIÓN) ---
        chunk_size = 5 # Restaurado al valor original
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
            
            if results and results[0] == "CANCELLED":
                return "cancelled"
            if results and results[0].startswith("[ERROR"):
                return f"Error: {results[0]}"
            
            all_texts.extend(results)
            
            # Guardado incremental ligero
            try:
                progreso_txt = os.path.join(full_output_dir, f"{chapter_name}_progreso.txt")
                with open(progreso_txt, "w", encoding="utf-8") as f:
                    f.write(f"PROGRESO ACTUAL DEL CAPÍTULO: {chapter_name}\n")
                    f.write(f"Traducidas {len(all_texts)} páginas hasta el momento...\n\n")
                    for idx, texto in enumerate(all_texts, 1):
                        f.write(f"PAGINA {idx}\n{'-'*50}\n{texto}\n\n")
            except Exception as e:
                logging.error(f"Error en guardado incremental: {e}")

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
            return

        file_paths.sort(key=lambda f: [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', f)])

        chunk_size = 20
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
            
            if results and results[0] == "CANCELLED":
                success_status = "cancelled"
                break
            if results and results[0].startswith("[ERROR"):
                success_status = "error"
                if callback:
                    callback("error_gemini_api", results[0])
                return 

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


    