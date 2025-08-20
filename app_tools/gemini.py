import logging
import os
import sys
import time
import random
import re
import httpx
from threading import Thread, Event
from google import genai
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
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
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

def combine_texts(output_dir, combined_content, chapter_name, master_content=None):
    """Combina contenido y genera archivos finales con verificación de errores"""
    try:
        # Sanitizar nombre del capítulo para evitar problemas en rutas
        sanitized_chapter = "".join(c for c in chapter_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
        if not sanitized_chapter:
            sanitized_chapter = "capitulo"
        
        # 1. Generar archivo combinado principal
        final_output = os.path.join(output_dir, f"{sanitized_chapter}_completo.txt")
        
        # Asegurar que existe el directorio de salida
        os.makedirs(output_dir, exist_ok=True)
        print(f"Creando archivos en: {output_dir}")
        print(f" - Archivo combinado: {os.path.basename(final_output)}")
        
        # Construir el contenido completo en memoria
        full_content = ""
        if master_content is not None:
            full_content = master_content
        else:
            full_content = f"CAPÍTULO: {sanitized_chapter}\n{'='*50}\n\n"
            for i, texto in enumerate(combined_content, 1):
                full_content += f"PÁGINA {i}\n{'-'*50}\n{texto}\n\n"
                if i < len(combined_content):
                    full_content += "\n" + "✦" * 75 + "\n\n"

        # Escribir el archivo combinado
        with open(final_output, "w", encoding="utf-8") as f:
            f.write(full_content)

        # 2. Generar grilla desde el contenido en memoria
        grid_content = generar_grilla(full_content)
        grid_path = os.path.join(output_dir, f"{sanitized_chapter}_grilla.txt")
        print(f" - Archivo grilla: {os.path.basename(grid_path)}")
        
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

class GeminiProcessor:
    def __init__(self):
        self.processing_start_time = None
        self.image_count = 0
        self.cancel_event = None

    def reset_counters(self):
        """Reinicia los contadores para un nuevo procesamiento"""
        self.processing_start_time = time.time()
        self.image_count = 0

    def process_file(self, file_path, output_dir, input_base):
        """Procesa archivos de imagen con reintentos en caso de error de conexión"""
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

            # Procesar imagen con manejo de reintentos
            image = Image.open(file_path)
            ai_start_time = time.time()
            
            # Intento con reintentos para errores de conexión
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = client.models.generate_content(
                        model=MODEL_NAME,
                        contents=[prompt, image]
                    )
                    break  # Si tiene éxito, sal del bucle
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                    if attempt < max_retries - 1:
                        wait_time = (2 ** attempt) + random.uniform(0, 1)
                        logging.warning(f"Error de conexión (intento {attempt+1}/{max_retries}): {e}. Reintentando en {wait_time:.1f} segundos...")
                        time.sleep(wait_time)
                    else:
                        raise  # Relanza la excepción después del último intento
            
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
            
            if self.processing_start_time is not None:
                self.image_count += 1
                total_elapsed_time = time.time() - self.processing_start_time
                print(f"Tiempo total transcurrido: {total_elapsed_time:.2f}s | Imágenes procesadas: {self.image_count}")
            
            return "\n".join(translations)

        except Exception as e:
            logging.error(f"Error procesando {file_path}: {str(e)}", exc_info=True)
            return ""
    
    def process_chapter(self, chapter_path, output_dir, cancel_event, input_base):
        """Procesa un capítulo individual y genera sus archivos finales inmediatamente"""
        try:
            combined_content = []
            image_count = 0
            start_time = time.time()

            # Obtener estructura relativa
            rel_path = os.path.relpath(chapter_path, input_base)
            chapter_output_dir = os.path.join(output_dir, rel_path)
            os.makedirs(chapter_output_dir, exist_ok=True)

            print(f"\n{'='*80}")
            print(f"INICIANDO PROCESAMIENTO DE CAPÍTULO: {os.path.basename(chapter_path)}")
            print(f"Directorio de salida del capítulo: {chapter_output_dir}")
            print(f"{'='*80}\n")

            # Encontrar todas las imágenes en el capítulo (incluyendo subdirectorios)
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

            # Generar archivos combinados para el capítulo inmediatamente después de procesar las imágenes
            if combined_content:
                chapter_name = os.path.basename(chapter_path)
                print(f"\n--- Generando archivos finales para CAPÍTULO: {chapter_name} ---")
                
                result = combine_texts(chapter_output_dir, combined_content, chapter_name)
                
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
            # Determinar el input_base para el cálculo de rutas relativas
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
            
            # Identificar los directorios de los capítulos (subdirectorios inmediatos)
            subdirectories = [d.path for d in os.scandir(input_path) if d.is_dir()]
            chapters_to_process = subdirectories if subdirectories else [input_path]

            # Procesar cada capítulo individualmente
            for chapter_path in chapters_to_process:
                if cancel_event and cancel_event.is_set(): return "cancelled"
                
                print(f"\n--- Procesando capítulo: {os.path.basename(chapter_path)} ---")
                chapter_status = self.process_chapter(
                    chapter_path, output_dir, cancel_event, input_base
                )
                if chapter_status != "success":
                    status = chapter_status  # Marcar que hubo un error, pero continuar

            # Si se procesaron subdirectorios, crear los archivos consolidados para la serie
            if subdirectories:
                print(f"\n--- Generando archivos consolidados para la serie: {os.path.basename(input_path)} ---")
                
                series_rel_path = os.path.relpath(input_path, input_base)
                series_output_dir = os.path.join(output_dir, series_rel_path)
                
                master_content_list = []
                
                # Ordenar capítulos para asegurar el orden correcto en el archivo consolidado
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
                    combine_texts(series_output_dir, [], series_name, master_content=series_master_content)

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

    def _process_selected_files_gemini(self, file_paths, output_dir, cancel_event, callback):
        """Procesa una lista de archivos seleccionados para Gemini."""
        try:
            self.reset_counters()  # Reiniciar contadores
            for file_path in file_paths:
                if cancel_event and cancel_event.is_set():
                    callback("cancelled")
                    return

                # Determine input_base for each file (its parent directory)
                input_base = os.path.dirname(file_path)
                content = self.process_file(file_path, output_dir, input_base)
                if not content: # If process_file returns empty string, it indicates an error
                    callback("error")
                    return

            callback("success")
        except Exception as e:
            logging.error(f"Error procesando archivos seleccionados para Gemini: {str(e)}", exc_info=True)
            callback("error")
