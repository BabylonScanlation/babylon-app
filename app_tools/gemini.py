import logging
import os
from os import scandir
import sys
import time
from threading import Thread, Event

global_processing_start_time = None
global_image_count = 0

from google import genai
from google.genai import types


from PIL import Image

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
logging.basicConfig(level=logging.DEBUG)

sys.excepthook = global_exception_handler

# Configurar cliente de Gemini
MODEL_NAME = "gemini-2.5-flash-lite"

client = genai.Client(api_key=Config.GEMINI_API_KEY)

def load_prompt():
    """Carga el prompt desde el archivo configurado en Config.PROMPT_PATH."""
    try:
        with open(Config.AI_PROMPT, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logging.error(f"Error cargando el prompt: {str(e)}")
        return None

def generar_grilla(content):
    """Genera análisis de personajes con Gemini."""
    try:
        with open(Config.GRILLA_PROMPT, "r", encoding="utf-8") as f:
            prompt = f.read()

        ai_start_time = time.time()
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[prompt, content]
        )
        ai_end_time = time.time()
        print(f"Tiempo de procesamiento de IA para grilla: {ai_end_time - ai_start_time:.4f} segundos")
        return response.text if response.text else "Sin datos de personajes"

    except Exception as e:
        logging.error(f"Error generando grilla: {str(e)}")
        return "Error al generar análisis de personajes"

def combine_texts(output_dir, combined_content, chapter_name):
    """Combina contenido y genera archivos finales con verificación de errores"""
    try:
        # 1. Generar archivo combinado principal
        final_output = os.path.join(output_dir, f"{chapter_name}_completo.txt")
        with open(final_output, "w", encoding="utf-8") as f:
            f.write(f"CAPÍTULO: {chapter_name}\n{'='*50}\n\n")
            for i, texto in enumerate(combined_content, 1):
                f.write(f"PÁGINA {i}\n{'-'*50}\n{texto}\n\n")
                if i < len(combined_content):
                    f.write("\n" + "✦" * 75 + "\n\n")

        # 2. Generar grilla desde el contenido combinado
        with open(final_output, "r", encoding="utf-8") as f:
            full_content = f.read()

        grid_content = generar_grilla(full_content)
        grid_path = os.path.join(output_dir, f"{chapter_name}_grilla.txt")
        
        with open(grid_path, "w", encoding="utf-8") as f:
            f.write(f"ANÁLISIS DE PERSONAJES - {chapter_name}\n{'='*50}\n\n")
            f.write(grid_content)

        # Verificación final
        if not os.path.exists(final_output) or os.path.getsize(final_output) == 0:
            raise Exception("Archivo combinado no se creó correctamente")
            
        if not os.path.exists(grid_path) or os.path.getsize(grid_path) == 0:
            raise Exception("Archivo de grilla no se creó correctamente")

        return True

    except Exception as e:
        logging.error(f"ERROR EN COMBINE_TEXTS: {str(e)}")
        # Limpiar archivos incompletos
        if 'final_output' in locals(): 
            if os.path.exists(final_output): 
                os.remove(final_output) 
        if 'grid_path' in locals(): 
            if os.path.exists(grid_path): 
                os.remove(grid_path)
        return False

class GeminiProcessor:
    def __init__(self):
        pass # Placeholder for now


    def process_file(self, file_path, output_dir, input_base):
        """Procesa archivos de imagen y guarda el resultado en la estructura correcta."""
        try:
            start_time = time.time()
            print(f"\nProcesando: {file_path}")
            prompt = load_prompt()
            if not prompt:
                raise ValueError("Error: Prompt no cargado")

            # Crear estructura de carpetas
            rel_path = os.path.relpath(file_path, input_base)
            chapter_dir = os.path.join(output_dir, os.path.dirname(rel_path))
            pages_dir = os.path.join(chapter_dir, "paginas")
            os.makedirs(pages_dir, exist_ok=True)

            # Procesar imagen
            image = Image.open(file_path)
            ai_start_time = time.time()
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=[prompt, image],
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=-1)
                )
            )
            ai_end_time = time.time()
            print(f"Tiempo de procesamiento de IA para {file_path}: {ai_end_time - ai_start_time:.4f} segundos")

            # Extraer y guardar contenido
            translations = []
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, 'text'):
                        translations.append(part.text)

            # Guardar archivo individual en /paginas
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            output_file = os.path.join(pages_dir, f"{base_name}.txt")
            
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("\n".join(translations))
            
            print(f"✓ {output_file} creado en {time.time() - start_time:.2f}s")
            
            global global_processing_start_time
            global global_image_count
            
            if global_processing_start_time is not None:
                global_image_count += 1
                total_elapsed_time = time.time() - global_processing_start_time
                print(f"Tiempo total transcurrido: {total_elapsed_time:.2f}s | Imágenes procesadas: {global_image_count}")
            
            return "\n".join(translations)

        except Exception as e:
            logging.error("Error procesando %s: %s", file_path, str(e))
            return ""

    
    def process_chapter(self, chapter_path, output_dir, cancel_event, input_base):
        """Procesa un capítulo individual con su propia carpeta de salida"""
        combined_content = []
        image_count = 0
        start_time = time.time()

        # Obtener estructura relativa
        rel_path = os.path.relpath(chapter_path, input_base)
        chapter_output_dir = os.path.join(output_dir, rel_path)

        def chapter_recursive_processor(current_path):
            nonlocal image_count, start_time, combined_content
            if cancel_event and cancel_event.is_set():
                return

            if os.path.isfile(current_path):
                if current_path.lower().endswith(Config.SUPPORTED_FORMATS):
                    if image_count >= 15:
                        elapsed_time = time.time() - start_time
                        if elapsed_time < 60:
                            time.sleep(60 - elapsed_time)
                        start_time = time.time()
                        image_count = 0

                    content = self.process_file(current_path, output_dir, input_base)
                    if content:
                        combined_content.append(content)
                        image_count += 1

            elif os.path.isdir(current_path):
                # Crear estructura de carpetas reflejada
                relative_dir = os.path.relpath(current_path, input_base)
                os.makedirs(os.path.join(output_dir, relative_dir), exist_ok=True)
                
                for entry in sorted([e.name for e in scandir(current_path) if e.is_file() or e.is_dir()]):
                    chapter_recursive_processor(os.path.join(current_path, entry))

        chapter_recursive_processor(chapter_path)

        if cancel_event and cancel_event.is_set():
            return False

        if combined_content:
            # Crear archivo combinado desde los archivos individuales
            result = combine_texts(chapter_output_dir, combined_content, os.path.basename(chapter_path))
            end_time = time.time()
            print(f"Tiempo total de procesamiento del capítulo {os.path.basename(chapter_path)}: {end_time - start_time:.4f} segundos")
            return result
        
        end_time = time.time()
        print(f"Tiempo total de procesamiento del capítulo {os.path.basename(chapter_path)}: {end_time - start_time:.4f} segundos")
        return False
        finally: # Correctly indented
            stop_timer_event.set()
            timer_thread.join() # Ensure the timer thread finishes

    def process_input_path(self, input_path, output_dir, cancel_event=None, input_base=None):
        try:
            # Determinar el input_base para el cálculo de rutas relativas
            # input_base debe ser el directorio que contiene los "capítulos" o "series".
            if input_base is None:
                if os.path.isfile(input_path):
                    # Si input_path es un archivo, el input_base es el directorio padre del capítulo.
                    # Ejemplo: /manga/chapter1/page1.png -> input_base = /manga
                    input_base = os.path.dirname(os.path.dirname(input_path))
                    if input_base == '': # Si el archivo está en la raíz del sistema de archivos
                        input_base = os.path.dirname(input_path) # El capítulo es el directorio padre del archivo
                else:
                    # Si input_path es un directorio, el input_base es el directorio padre de input_path
                    # Ejemplo: /manga/chapter1/ -> input_base = /manga/
                    # Ejemplo: /manga/ -> input_base = / (si manga es la raíz de la serie)
                    input_base = os.path.dirname(input_path)
                    if input_base == '': # Si el directorio está en la raíz del sistema de archivos
                        input_base = input_path # El propio directorio es el input_base

            global global_processing_start_time
            if global_processing_start_time is None:
                global_processing_start_time = time.time()

            success = True
            
            # Si la entrada es un archivo, procesar su directorio padre como un capítulo
            if os.path.isfile(input_path):
                chapter_to_process = os.path.dirname(input_path)
                chapter_success = self.process_chapter(
                    chapter_to_process,
                    output_dir,
                    cancel_event,
                    input_base
                )
                success = success and chapter_success
            else: # input_path es un directorio
                # Verificar si el directorio raíz contiene imágenes directamente (es un capítulo en sí mismo)
                contains_images_directly = any(f.lower().endswith(Config.SUPPORTED_FORMATS) for f in [e.name for e in scandir(input_path) if e.is_file()] if os.path.isfile(os.path.join(input_path, f)))
                
                if contains_images_directly:
                    chapter_success = self.process_chapter(
                        input_path, # El propio input_path es el capítulo
                        output_dir,
                        cancel_event,
                        input_base
                    )
                    success = success and chapter_success

                # Iterar sobre los subdirectorios como capítulos
                for entry in [e.name for e in scandir(input_path) if e.is_dir()]:
                    entry_path = os.path.join(input_path, entry)
                    if os.path.isdir(entry_path):
                        chapter_success = self.process_chapter(
                            entry_path,
                            output_dir,
                            cancel_event,
                            input_base
                        )
                        success = success and chapter_success
            return success

        except Exception as e:
            logging.error("Error en process_input_path: %s", str(e))
            return False

    def start_processing_in_background(self, input_path, output_dir, cancel_event=None, callback=None):
        """Inicia el procesamiento en segundo plano con estructura de capítulos."""
        
        def _process_and_callback(process_func):
            result = False
            try:
                result = process_func(input_path, output_dir, cancel_event)
            except Exception as e:
                logging.error(f"Error crítico en _process_and_callback: {str(e)}")
            finally:
                if callback:
                    callback(result)
        thread.start()
        return thread


