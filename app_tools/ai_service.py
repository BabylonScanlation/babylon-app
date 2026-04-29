import os
import re
import time
import logging
import threading
from threading import Thread, Event
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Callable, Dict
from config import Config

class AIAPIError(Exception):
    """Excepción base para errores de APIs de IA."""
    pass

class BaseAIProcessor:
    def __init__(self, model_name: str):
        self.model_name: str = model_name
        self.processing_start_time: Optional[float] = None
        self.image_count: int = 0
        self.cancel_event: Optional[threading.Event] = None
        self.token_callback: Optional[Callable[[int], None]] = None
        self.status_callback: Optional[Callable[[str], None]] = None # Nuevo callback de estado
        self.max_workers: int = 3

    def set_token_callback(self, callback: Callable[[int], None]):
        self.token_callback = callback

    def set_status_callback(self, callback: Callable[[str], None]):
        """Permite a la UI recibir mensajes de estado en tiempo real."""
        self.status_callback = callback

    def _report_status(self, message: str):
        """Método helper para enviar mensajes de estado de forma segura."""
        if self.status_callback:
            try:
                self.status_callback(message)
            except Exception:
                pass # Ignorar errores en el callback de UI
        logging.info(f"[{self.model_name.upper()}] {message}")

    def reset_counters(self):
        self.processing_start_time = time.time()
        self.image_count = 0

    def load_prompt(self, prompt_path: Optional[str]) -> Optional[str]:
        if not prompt_path or not os.path.exists(prompt_path):
            return None
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logging.error(f"Error cargando el prompt desde {prompt_path}: {str(e)}")
            return None

    def call_api(self, prompt: str, image_path: Optional[str] = None, content: Optional[str] = None) -> Optional[str]:
        """Método que debe ser implementado por cada modelo específico."""
        raise NotImplementedError("Subclases deben implementar call_api")

    def process_file(self, file_path: str, output_dir: str, input_base: str) -> Optional[str]:
        """Lógica común para procesar un solo archivo."""
        prompt = self.load_prompt(Config.AI_PROMPT)
        if not prompt:
            # Fallback prompt si no hay archivo configurado
            prompt = "Traduce el texto de esta imagen."

        logging.debug(f"[AI_SERVICE] Procesando archivo: '{file_path}' | Output dir: '{output_dir}'")
        logging.debug(f"[AI_SERVICE] Prompt utilizado (primeros 50 chars): {prompt[:50]}...")
        
        result_text = self.call_api(prompt, image_path=file_path)
        
        if result_text:
            logging.debug(f"[AI_SERVICE] Respuesta recibida para '{os.path.basename(file_path)}' (longitud: {len(result_text)})")
        else:
            logging.debug(f"[AI_SERVICE] La API no devolvió texto para '{os.path.basename(file_path)}'")
            
        return result_text

    def combine_texts(self, output_dir: str, combined_content: List[Optional[str]], chapter_name: str, master_content: Optional[str] = None) -> bool:
        """Lógica común para combinar textos y generar grilla."""
        try:
            # Limpiar nombre del capítulo para que sea un nombre de archivo válido
            sanitized_chapter = "".join(c for c in chapter_name if c.isalnum() or c in (' ', '_', '-')).strip()
            if not sanitized_chapter:
                sanitized_chapter = "capitulo_generico"
            
            # Asegurar que el directorio de salida exista (Manejo robusto para USB)
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as e:
                logging.error(f"Error creando directorio en {output_dir}. Puede ser un problema de permisos o USB desconectado: {e}")
                return False

            final_output = os.path.join(output_dir, f"{sanitized_chapter}_completo.txt")
            
            if master_content is not None:
                full_content = master_content
            else:
                full_content = f"CAPÍTULO: {sanitized_chapter}\n{'='*50}\n\n"
                for i, texto in enumerate(combined_content, 1):
                    texto_limpio = str(texto).strip()
                    full_content += f"PAGINA {i}\n{'-'*50}\n{texto_limpio}\n\n"
                    if i < len(combined_content):
                        full_content += "\n" + "-" * 75 + "\n\n"

            # Escritura robusta con codificación UTF-8
            try:
                with open(final_output, "w", encoding="utf-8") as f:
                    f.write(full_content)
                logging.info(f"Archivo combinado guardado en: {final_output}")
            except OSError as e:
                 logging.error(f"Error escribiendo archivo en {final_output}. Verifica tu USB: {e}")
                 return False

            # Generar grilla solo si hay configuración para ello
            if hasattr(Config, 'GRILLA_PROMPT') and Config.GRILLA_PROMPT and os.path.exists(Config.GRILLA_PROMPT):
                grid_prompt = self.load_prompt(Config.GRILLA_PROMPT)
                if grid_prompt:
                    logging.info("Generando grilla de análisis...")
                    grid_content = self.call_api(grid_prompt, content=full_content)
                    if grid_content:
                        grid_path = os.path.join(output_dir, f"{sanitized_chapter}_grilla.txt")
                        with open(grid_path, "w", encoding="utf-8") as f:
                            f.write(f"ANÁLISIS DE PERSONAJES - {sanitized_chapter}\n{'='*50}\n\n")
                            f.write(grid_content)

            return True
        except Exception as e:
            logging.error(f"Error combinando textos: {str(e)}", exc_info=True)
            return False

    def process_chapter(self, chapter_path: str, output_dir: str, cancel_event: threading.Event, input_base: str) -> str:
        """Procesa un capítulo de forma paralela (implementación base)."""
        image_files: List[str] = []
        for root, _, files in os.walk(chapter_path):
            for file in files:
                if file.lower().endswith(Config.SUPPORTED_FORMATS):
                    image_files.append(os.path.join(root, file))
        
        # Ordenar naturalmente
        image_files.sort(key=lambda f: [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', f)])
        
        results: Dict[int, Optional[str]] = {}
        workers = self.max_workers
        
        logging.info(f"Iniciando procesamiento paralelo base con {workers} hilos para {len(image_files)} imágenes...")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_image = {
                executor.submit(self.process_file, img, output_dir, input_base): (i, img) 
                for i, img in enumerate(image_files)
            }
            
            for future in as_completed(future_to_image):
                index, img_path = future_to_image[future]
                if cancel_event and cancel_event.is_set():
                    executor.shutdown(wait=False)
                    return "cancelled"
                
                try:
                    content = future.result()
                    results[index] = content
                    logging.info(f"Procesada {index+1}/{len(image_files)}")
                except Exception as e:
                    logging.error(f"Error procesando {img_path}: {e}")
                    results[index] = f"[ERROR: {str(e)}]"

        combined_content = [results[i] for i in sorted(results.keys())]

        if combined_content:
            chapter_name = os.path.basename(chapter_path)
            rel_path = os.path.relpath(chapter_path, input_base)
            # Evitar salirnos del directorio si rel_path empieza con ..
            if rel_path.startswith(".."):
                rel_path = chapter_name
            
            chapter_output_dir = os.path.join(output_dir, rel_path)
            self.combine_texts(chapter_output_dir, combined_content, chapter_name)
            return "success"
        
        return "error"

    def process_input_path(self, input_path: str, output_dir: str, cancel_event: threading.Event) -> str:
        """Punto de entrada principal."""
        try:
            input_path = os.path.abspath(input_path)
            output_dir = os.path.abspath(output_dir)

            if os.path.isfile(input_path):
                # Caso archivo individual
                return self.process_chapter(os.path.dirname(input_path), output_dir, cancel_event, os.path.dirname(input_path))
            else:
                # Caso directorio
                input_base = input_path
                # Detectar si es un capítulo (tiene imágenes) o una serie (tiene subcarpetas)
                has_images = any(f.lower().endswith(Config.SUPPORTED_FORMATS) for f in os.listdir(input_path) if os.path.isfile(os.path.join(input_path, f)))
                
                if has_images:
                    return self.process_chapter(input_path, output_dir, cancel_event, input_base)
                
                # Procesar subdirectorios recursivamente
                status = "success"
                for root, dirs, _ in os.walk(input_path):
                    for d in dirs:
                        if cancel_event.is_set():
                            return "cancelled"
                        chapter_path = os.path.join(root, d)
                        # Verificar si tiene imágenes
                        if any(f.lower().endswith(Config.SUPPORTED_FORMATS) for f in os.listdir(chapter_path)):
                            res = self.process_chapter(chapter_path, output_dir, cancel_event, input_base)
                            if res != "success":
                                status = res
                return status

        except Exception as e:
            logging.error(f"Error en process_input_path: {e}", exc_info=True)
            return "error"

    def start_processing_in_background(self, input_path: str, output_dir: str, cancel_event: Optional[threading.Event] = None, callback: Optional[Callable[[str], None]] = None):
        if cancel_event is None:
            self.cancel_event = Event()
        else:
            self.cancel_event = cancel_event
            
        def _run():
            try:
                # self.cancel_event no será None aquí debido a la lógica anterior
                assert self.cancel_event is not None
                result = self.process_input_path(input_path, output_dir, self.cancel_event)
                if callback:
                    callback(result)
            except Exception as e:
                logging.error(f"Error en background thread: {e}", exc_info=True)
                if callback:
                    callback("error")
                
        Thread(target=_run, daemon=True).start()