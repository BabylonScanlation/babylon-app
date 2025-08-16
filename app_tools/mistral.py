import base64
import logging
import os
import re
import sys
import time
import traceback
from threading import Thread

import requests
from PIL import Image
from PyQt5.QtWidgets import QMessageBox  # pylint: disable=no-name-in-module

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
logging.basicConfig(level=logging.ERROR)


sys.excepthook = global_exception_handler


class MistralProcessor:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {Config.MISTRAL_API_KEY}",
            "Content-Type": "application/json",
        }
        self.model = "pixtral-large-latest"

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

    def process_file(self, file_path, output_file_path):
        """Procesa archivos de imagen y guarda los resultados."""
        try:
            start_time = time.time()
            print(f"\nProcesando: {file_path}")
            prompt = self.load_prompt()
            if not prompt:
                raise ValueError("Error: Prompt no cargado")

            base64_image = self.encode_image(file_path)
            if not base64_image:
                return False

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
            )

            if response.status_code == 200:
                result_text = response.json()["choices"][0]["message"]["content"]
                os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
                with open(output_file_path, "w", encoding="utf-8") as f:
                    f.write(result_text)

                print(f"✓ Resultado guardado: {output_file_path}")
                print(f"⏱ Tiempo total: {time.time() - start_time:.2f}s")
                return True
            else:
                logging.error(f"Error en procesamiento: {response.text}")
                return False

        except Exception as e:
            logging.error(f"Error procesando {file_path}: {str(e)}")
            return False

    def generar_grilla(self, content):
        """Genera análisis de personajes con Mistral."""
        try:
            with open(Config.GRILLA_PROMPT, "r", encoding="utf-8") as f:
                prompt = f.read()

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
            )

            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]
            else:
                logging.error(f"Error en generación de grilla: {response.text}")
                return "Error al generar análisis de personajes"

        except Exception as e:
            logging.error(f"Error generando grilla: {str(e)}")
            return "Error al generar análisis de personajes"


def process_input_path(input_path, output_dir, cancel_event=None, input_base=None):
    """Procesa la ruta de entrada de manera recursiva."""
    try:
        input_base = (
            input_base or os.path.dirname(input_path)
            if os.path.isfile(input_path)
            else input_path
        )

        def process_recursive(current_path):
            if cancel_event and cancel_event.is_set():
                return

            if os.path.isfile(current_path):
                if current_path.lower().endswith(Config.SUPPORTED_FORMATS):
                    rel_path = os.path.relpath(current_path, start=input_base)
                    output_path = os.path.join(output_dir, rel_path)
                    output_path = os.path.splitext(output_path)[0] + ".txt"
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    processor = MistralProcessor()
                    processor.process_file(current_path, output_path)

            elif os.path.isdir(current_path):
                for entry in sorted(os.listdir(current_path)):
                    process_recursive(os.path.join(current_path, entry))

        process_recursive(input_path)

        # Verificar si se canceló el proceso antes de combinar textos
        if cancel_event and cancel_event.is_set():
            return False  # 🔴 Retornar False si fue cancelado
            
        # Llamar a combine_texts después de procesar todo
        combine_texts(output_dir)
        return True  # 🟢 Retornar True si todo fue exitoso
    except Exception as e:
        logging.error("Error general: %s", str(e))
        return False  # 🔴 Retornar False en caso de error


def start_processing_in_background(input_path, output_dir, cancel_event=None, callback=None):  # Añadir callback
    """Inicia el procesamiento en segundo plano con estructura de capítulos."""
    def wrapper():
        result = False  # 🔴 Valor por defecto en caso de excepción
        try:
            chapter_output_dir = os.path.join(output_dir, os.path.basename(input_path))
            os.makedirs(chapter_output_dir, exist_ok=True)
            result = process_input_path(input_path, chapter_output_dir, cancel_event)
            if callback:
                callback(result)
        except Exception as e:
            logging.error(f"Error crítico en wrapper: {str(e)}")
        finally:
            if callback:
                callback(result)  # 🟢 Siempre ejecutar el callback

    thread = Thread(target=wrapper)
    thread.start()
    return thread


def combine_texts(output_dir):
    """Combina archivos y genera análisis de personajes con formato compacto."""
    try:
        # Validar directorio
        if not os.path.exists(output_dir):
            raise FileNotFoundError(f"Directorio no encontrado: {output_dir}")

        # Obtener el nombre del capítulo del directorio
        chapter_name = os.path.basename(os.path.normpath(output_dir))

        # Extraer el número de capítulo del nombre del directorio
        chapter_number_match = re.search(r"\d+", chapter_name)
        if not chapter_number_match:
            raise ValueError(f"Nombre de capítulo inválido: {chapter_name}")

        chapter_number = chapter_number_match.group(0)

        # 1. Generar archivo combinado principal
        final_output = os.path.join(output_dir, "resultado_final.txt")
        txt_files = [
            f
            for f in os.listdir(output_dir)
            if f.endswith(".txt")
            and f != "resultado_final.txt"
            and os.path.splitext(f)[0].isdigit()
        ]

        txt_files.sort(key=lambda x: int(os.path.splitext(x)[0]))

        # Procesar páginas
        paginas = []
        for fname in txt_files:
            try:
                with open(os.path.join(output_dir, fname), "r", encoding="utf-8") as f:
                    contenido = f.read().strip()
                if contenido:
                    paginas.append((int(os.path.splitext(fname)[0]), contenido))
            except Exception as e:
                logging.error(f"Error procesando {fname}: {str(e)}")

        # Escribir archivo final
        with open(final_output, "w", encoding="utf-8") as f:
            f.write(f"CAPÍTULO {chapter_number}\n{'='*50}\n\n")
            for i, (num, texto) in enumerate(paginas):
                f.write(f"PÁGINA {num}\n{'-'*50}\n{texto}\n\n")
                if i < len(paginas) - 1:
                    f.write("\n" + "✦" * 75 + "\n\n")

        # 2. Generar grilla de personajes
        with open(final_output, "r", encoding="utf-8") as f:
            full_content = f.read()

        processor = MistralProcessor()
        grid_content = processor.generar_grilla(full_content)
        grid_path = os.path.join(output_dir, "grilla.txt")

        # Formatear para pantallas pequeñas
        with open(grid_path, "w", encoding="utf-8") as f:
            f.write(f"PERSONAJES - CAPÍTULO {chapter_number}\n{'='*50}\n\n")

            max_ancho = 78  # Máximo caracteres por línea
            separador = "-" * 50

            for bloque in grid_content.split("\n\n"):
                lineas = []
                for linea in bloque.split("\n"):
                    if len(linea) > max_ancho:
                        # Dividir línea manteniendo palabras completas
                        partes = []
                        palabras = linea.split()
                        linea_actual = []
                        longitud = 0

                        for palabra in palabras:
                            if longitud + len(palabra) + 1 > max_ancho:
                                partes.append(" ".join(linea_actual))
                                linea_actual = [palabra]
                                longitud = len(palabra)
                            else:
                                linea_actual.append(palabra)
                                longitud += len(palabra) + 1

                        if linea_actual:
                            partes.append(" ".join(linea_actual))
                        lineas.extend(partes)
                    else:
                        lineas.append(linea)

                # Escribir bloque formateado
                f.write("\n".join(lineas))
                f.write("\n\n" + separador + "\n\n")

        # Reporte final
        print(f"\n✅ Archivo principal: {final_output}")
        print(f"📋 Grilla compacta: {grid_path}")
        print(f"📖 Capítulo: {chapter_number}")
        print(f"📃 Páginas procesadas: {len(paginas)}/{len(txt_files)}")

        return True

    except Exception as e:
        logging.error(f"ERROR EN COMBINE_TEXTS: {str(e)}")
        return False
