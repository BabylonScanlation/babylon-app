import base64
import logging
import os
import re
import sys
import time
import random
import httpx
from threading import Thread, Event
from os import scandir

# Define the directory for generated pages/content
pages_dir = os.path.join(os.path.dirname(__file__), "pages")
from PIL import Image
import requests

# Configurar rutas del proyecto
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

try:
    from config import Config, global_exception_handler
except ImportError as e:
    logging.error("Error importando config: %s", e)
    sys.exit(1)

# Configuración inicial
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
sys.excepthook = global_exception_handler

class MistralProcessor:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {Config.MISTRAL_API_KEY}",
            "Content-Type": "application/json",
        }
        self.model = "mistral-medium-2508"
        self.processing_start_time = None
        self.image_count = 0
        self.cancel_event = None

    def reset_counters(self):
        """Reinicia los contadores para un nuevo procesamiento"""
        self.processing_start_time = time.time()
        self.image_count = 0

    def load_prompt(self):
        """Carga el prompt desde el archivo especificado en Config."""
        try:
            with open(Config.AI_PROMPT, "r", encoding="utf-8") as file:
                return file.read()
        except IOError as e:
            logging.error("Error cargando prompt: %s", e)
            return ""

    def encode_image(self, image_path):
        """Codifica imagen a base64 con manejo de errores."""
        try:
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode("utf-8")
        except Exception as e:
            logging.error(f"Error codificando imagen: {str(e)}")
            return None

    def process_file(self, file_path, output_dir, input_base):
        """Procesa archivos de imagen con reintentos en caso de error de conexión."""
        try:
            start_time = time.time()
            print(f"\nProcesando: {file_path}")
            
            prompt = self.load_prompt()
            if not prompt:
                raise ValueError("Error: Prompt no cargado")

            # Crear estructura de carpetas
            
            os.makedirs(pages_dir, exist_ok=True)

            # Procesar imagen con manejo de reintentos
            base64_image = self.encode_image(file_path)
            if not base64_image:
                return ""

            ai_start_time = time.time()
            
            # Intento con reintentos para errores de conexión
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = requests.post(
                        "https://api.mistral.ai/v1/chat/completions",
                        headers=self.headers,
                        json={
                            "model": self.model,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": prompt},
                                        {
                                            "type": "image_url",
                                            "image_url": f"data:image/jpeg;base64,{base64_image}",
                                        },
                                    ],
                                }
                            ],
                            "temperature": 0.7,
                            "max_tokens": 6000,
                        },
                        timeout=60  # Añadir timeout
                    )
                    break  # Si tiene éxito, sal del bucle
                except (requests.exceptions.ConnectionError, 
                        requests.exceptions.Timeout,
                        requests.exceptions.ChunkedEncodingError) as e:
                    if attempt < max_retries - 1:
                        wait_time = (2 ** attempt) + random.uniform(0, 1)
                        logging.warning(f"Error de conexión (intento {attempt+1}/{max_retries}): {e}. Reintentando en {wait_time:.1f} segundos...")
                        time.sleep(wait_time)
                    else:
                        raise  # Relanza la excepción después del último intento
            
            ai_end_time = time.time()
            print(f"Tiempo de procesamiento de IA para {file_path}: {ai_end_time - ai_start_time:.4f} segundos")

            if response.status_code == 200:
                result_text = response.json()["choices"][0]["message"]["content"]
                
                # Guardar archivo individual en /paginas
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                output_file = os.path.join(pages_dir, f"{base_name}.txt")
                
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(result_text)
                
                if self.processing_start_time is not None:
                    self.image_count += 1
                    total_elapsed_time = time.time() - self.processing_start_time
                    print(f"Tiempo total transcurrido: {total_elapsed_time:.2f}s | Imágenes procesadas: {self.image_count}")
                
                return result_text

            else:
                logging.error(f"Error en procesamiento: {response.text}")
                return ""

        except Exception as e:
            logging.error(f"Error procesando {file_path}: {str(e)}", exc_info=True)
            return ""

    def generar_grilla(self, content):
        """Genera análisis de personajes con reintentos para errores de conexión."""
        try:
            with open(Config.GRILLA_PROMPT, "r", encoding="utf-8") as f:
                prompt = f.read()

            ai_start_time = time.time()
            
            # Intento con reintentos para errores de conexión
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = requests.post(
                        "https://api.mistral.ai/v1/chat/completions",
                        headers=self.headers,
                        json={
                            "model": self.model,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": prompt},
                                        {"type": "text", "text": content},
                                    ],
                                }
                            ],
                            "temperature": 0.7,
                            "max_tokens": 6000,
                        },
                        timeout=60  # Añadir timeout
                    )
                    break  # Si tiene éxito, sal del bucle
                except (requests.exceptions.ConnectionError, 
                        requests.exceptions.Timeout,
                        requests.exceptions.ChunkedEncodingError) as e:
                    if attempt < max_retries - 1:
                        wait_time = (2 ** attempt) + random.uniform(0, 1)
                        logging.warning(f"Error de conexión (intento {attempt+1}/{max_retries}): {e}. Reintentando en {wait_time:.1f} segundos...")
                        time.sleep(wait_time)
                    else:
                        raise  # Relanza la excepción después del último intento
            
            ai_end_time = time.time()
            print(f"Tiempo de procesamiento de IA para grilla: {ai_end_time - ai_start_time:.4f} segundos")

            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]
            else:
                logging.error(f"Error en generación de grilla: {response.text}")
                return "Error al generar análisis de personajes"

        except Exception as e:
            logging.error(f"Error generando grilla: {str(e)}")
            return "Error al generar análisis de personajes"

    def combine_texts(self, output_dir, combined_content, chapter_name, master_content=None):
        """Combina contenido y genera archivos finales con verificación de errores"""
        try:
            # Sanitizar nombre del capítulo
            sanitized_chapter = "".join(c for c in chapter_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
            if not sanitized_chapter:
                sanitized_chapter = "capitulo"
            
            # 1. Generar archivo combinado principal
            final_output = os.path.join(output_dir, f"{sanitized_chapter}_completo.txt")
            os.makedirs(output_dir, exist_ok=True)
            
            # Construir contenido completo
            full_content = ""
            if master_content is not None:
                full_content = master_content
            else:
                full_content = f"CAPÍTULO: {sanitized_chapter}\n{'='*50}\n\n"
                for i, texto in enumerate(combined_content, 1):
                    full_content += f"PÁGINA {i}\n{'-'*50}\n{texto}\n\n"
                    if i < len(combined_content):
                        full_content += "\n" + "✦" * 75 + "\n\n"

            # Escribir archivo combinado
            with open(final_output, "w", encoding="utf-8") as f:
                f.write(full_content)

            # 2. Generar grilla desde el contenido
            grid_content = self.generar_grilla(full_content)
            grid_path = os.path.join(output_dir, f"{sanitized_chapter}_grilla.txt")
            
            with open(grid_path, "w", encoding="utf-8") as f:
                f.write(f"ANÁLISIS DE PERSONAJES - {sanitized_chapter}\n{'='*50}\n\n")
                f.write(grid_content)

            # Verificación final
            if not os.path.exists(final_output) or os.path.getsize(final_output) == 0:
                raise Exception(f"Archivo combinado no se creó correctamente: {final_output}")
                
            if not os.path.exists(grid_path) or os.path.getsize(grid_path) == 0:
                raise Exception(f"Archivo de grilla no se creó correctamente: {grid_path}")

            print(f"✓ Archivos generados en: {output_dir}")
            return True

        except Exception as e:
            logging.error(f"ERROR EN COMBINE_TEXTS: {str(e)}", exc_info=True)
            # Limpiar archivos incompletos
            if 'final_output' in locals() and os.path.exists(final_output): 
                os.remove(final_output) 
            if 'grid_path' in locals() and os.path.exists(grid_path): 
                os.remove(grid_path)
            return False

    def process_chapter(self, chapter_path, output_dir, cancel_event, input_base):
        """Procesa un capítulo individual con control de tasa."""
        try:
            combined_content = []
            start_time = time.time()

            # Obtener estructura relativa
            rel_path = os.path.relpath(chapter_path, input_base)
            chapter_output_dir = os.path.join(output_dir, rel_path)
            os.makedirs(chapter_output_dir, exist_ok=True)

            print(f"\n{'='*80}")
            print(f"INICIANDO PROCESAMIENTO DE CAPÍTULO: {os.path.basename(chapter_path)}")
            print(f"Directorio de salida del capítulo: {chapter_output_dir}")
            print(f"{'='*80}\n")

            # Encontrar todas las imágenes en el capítulo
            image_files = []
            for root, _, files in os.walk(chapter_path):
                for file in files:
                    if file.lower().endswith(Config.SUPPORTED_FORMATS):
                        image_files.append(os.path.join(root, file))
            
            # Ordenar imágenes naturalmente
            image_files.sort(key=lambda f: [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', f)])

            # Procesar cada imagen
            for i, image_path in enumerate(image_files, 1):
                if cancel_event and cancel_event.is_set():
                    print(f"Proceso cancelado durante el capítulo {os.path.basename(chapter_path)}")
                    return "cancelled"

                # Control de tasa: 15 imágenes por minuto
                if i % 15 == 0:
                    elapsed_time = time.time() - start_time
                    if elapsed_time < 60:
                        sleep_time = 60 - elapsed_time
                        print(f"Alcanzado límite de tasa. Durmiendo {sleep_time:.2f} segundos...")
                        time.sleep(sleep_time)
                    start_time = time.time()

                print(f"Procesando imagen {i}/{len(image_files)}: {os.path.basename(image_path)}")
                content = self.process_file(image_path, output_dir, input_base)
                if content:
                    combined_content.append(content)

            # Generar archivos combinados para el capítulo
            if combined_content:
                chapter_name = os.path.basename(chapter_path)
                print(f"\n--- Generando archivos finales para CAPÍTULO: {chapter_name} ---")
                
                result = self.combine_texts(chapter_output_dir, combined_content, chapter_name)
                
                end_time = time.time()
                print(f"\n{'='*80}")
                print(f"CAPÍTULO COMPLETADO: {chapter_name}")
                print(f"Tiempo total del capítulo: {end_time - start_time:.4f} segundos")
                print(f"{'='*80}\n")
                
                if result:
                    return "success"
                else:
                    return "error"
            
            print(f"\n{'!'*80}")
            print(f"ADVERTENCIA: No se encontraron imágenes en el capítulo {os.path.basename(chapter_path)}")
            print(f"{'!'*80}\n")
            return "error"
        
        except Exception as e:
            logging.error(f"Error crítico en process_chapter: {str(e)}", exc_info=True)
            return "error"

    def process_input_path(self, input_path, output_dir, cancel_event, input_base=None):
        try:
            # Determinar el input_base para rutas relativas
            if input_base is None:
                if os.path.isfile(input_path):
                    input_base = os.path.dirname(os.path.dirname(input_path))
                    if input_base == '':
                        input_base = os.path.dirname(input_path)
                else:
                    input_base = os.path.dirname(input_path)
                    if input_base == '' or input_base == input_path:
                         input_base = os.path.abspath(os.path.join(input_path, os.pardir))

            self.reset_counters()
            status = "success"
            
            # Identificar directorios de capítulos
            subdirectories = [d.path for d in os.scandir(input_path) if d.is_dir()]
            chapters_to_process = subdirectories if subdirectories else [input_path]

            # Procesar cada capítulo
            for chapter_path in chapters_to_process:
                if cancel_event and cancel_event.is_set(): 
                    return "cancelled"
                
                print(f"\n--- Procesando capítulo: {os.path.basename(chapter_path)} ---")
                chapter_status = self.process_chapter(
                    chapter_path, output_dir, cancel_event, input_base
                )
                if chapter_status != "success":
                    status = chapter_status

            # Crear archivos consolidados para la serie
            if chapters_to_process and len(chapters_to_process) > 1:
                print(f"\n--- Generando archivos consolidados para la serie: {os.path.basename(input_path)} ---")
                
                series_rel_path = os.path.relpath(input_path, input_base)
                series_output_dir = os.path.join(output_dir, series_rel_path)
                
                master_content_list = []
                
                # Ordenar capítulos naturalmente
                sorted_chapters = sorted(chapters_to_process, key=lambda f: [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', os.path.basename(f))])

                for chapter_path in sorted_chapters:
                    chapter_name = os.path.basename(chapter_path)
                    chapter_rel_path = os.path.relpath(chapter_path, input_base)
                    chapter_output_dir = os.path.join(output_dir, chapter_rel_path)
                    
                    # Nombre del archivo completo del capítulo
                    sanitized_chapter_name = "".join(c for c in chapter_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
                    if not sanitized_chapter_name:
                        sanitized_chapter_name = "capitulo"
                    chapter_complete_file = os.path.join(chapter_output_dir, f"{sanitized_chapter_name}_completo.txt")

                    if os.path.exists(chapter_complete_file):
                        with open(chapter_complete_file, 'r', encoding='utf-8') as f:
                            master_content_list.append(f.read())
                    else:
                        print(f"Advertencia: No se encontró el archivo completo para el capítulo {chapter_name} en {chapter_complete_file}")

                if master_content_list:
                    series_master_content = ("\n\n" + "╬" * 75 + "\n\n").join(master_content_list)
                    series_name = os.path.basename(input_path)
                    self.combine_texts(series_output_dir, [], series_name, master_content=series_master_content)

            return status

        except Exception as e:
            logging.error(f"Error en process_input_path: {str(e)}", exc_info=True)
            return "error"

    def start_processing_in_background(self, input_path, output_dir, cancel_event=None, callback=None):
        """Inicia el procesamiento en segundo plano con estructura de capítulos."""
        if cancel_event is None:
            self.cancel_event = Event()
        else:
            self.cancel_event = cancel_event
            
        def _process_and_callback():
            result = "error"
            try:
                result = self.process_input_path(input_path, output_dir, self.cancel_event)
            except Exception as e:
                logging.error(f"Error crítico en _process_and_callback: {str(e)}", exc_info=True)
            finally:
                if callback:
                    callback(result)
            print("Procesamiento completado")
            
        thread = Thread(target=_process_and_callback)
        thread.start()
        return thread

    def _process_selected_files_mistral(self, file_paths, output_dir, cancel_event, callback):
        """Procesa una lista de archivos seleccionados para Mistral."""
        try:
            self.reset_counters()  # Reiniciar contadores
            for file_path in file_paths:
                if cancel_event and cancel_event.is_set():
                    callback("cancelled")
                    return

                # Determine a common input_base for all selected files
            common_input_base = os.path.commonpath(file_paths)
            # Ensure common_input_base is a directory, not a file
            if os.path.isfile(common_input_base):
                common_input_base = os.path.dirname(common_input_base)
            

            for file_path in file_paths:
                if cancel_event and cancel_event.is_set():
                    callback("cancelled")
                    return

                content = self.process_file(file_path, output_dir, common_input_base)
                if not content: # If process_file returns empty string, it indicates an error
                    callback("error")
                    return

            callback("success")
        except Exception as e:
            logging.error(f"Error procesando archivos seleccionados para Mistral: {str(e)}", exc_info=True)
            callback("error")