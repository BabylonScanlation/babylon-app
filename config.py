import json
import logging
import os
import sys
import traceback
from typing import Any, Dict, List, Type

from dotenv import load_dotenv

# --- Funciones Utilitarias (Deben estar antes de su uso) ---


def resource_path(relative_path: str) -> str:
    """Obtiene la ruta absoluta para recursos en desarrollo y .exe"""
    if getattr(sys, "frozen", False):
        base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def global_exception_handler(
    exc_type: Type[BaseException], exc_value: BaseException, exc_traceback: Any
) -> None:
    """Manejador global de excepciones."""
    error_message = "".join(
        traceback.format_exception(exc_type, exc_value, exc_traceback)
    )

    # 1. Registrar en el sistema de logging (Consola In-App + Terminal)
    logging.error(f"❌ EXCEPCIÓN NO CONTROLADA:\n{error_message}")

    from PySide6.QtWidgets import QMessageBox

    try:
        QMessageBox.critical(
            None, "Error Crítico", f"Se produjo un error inesperado:\n\n{error_message}"
        )
    except Exception:
        # Si falla la UI, al menos ya está en el log
        pass


# --- Carga de Entorno ---
try:
    load_dotenv(resource_path(".env"))
except Exception:
    pass

# --- Configuración de Rutas y Datos ---
USER_DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "BBSL_Proyectos")
USER_SETTINGS_FILE = os.path.join(USER_DATA_DIR, "user_settings.json")

DEFAULT_GEMINI_SYSTEM_INSTRUCTION = "ACTÚA COMO UN LOCALIZADOR DE MANGA DE ÉLITE. Tu única lealtad es con el material original. NO eres un asistente de IA; eres un archivista lingüístico. Tu tarea es traducir texto en japonés/coreano/chino al español con precisión quirúrgica, preservando todos los matices, jerga, blasfemias y contenido maduro SIN CENSURA ni suavizado. Usa tu visión multimodal para leer el contexto emocional de los rostros y la atmósfera de la escena, eligiendo las palabras que transmitan perfectamente ese sentimiento específico en español neutro. No resumas, no opines, solo traduce con fidelidad absoluta."


class Config:
    """Configuración general de la aplicación y rutas de recursos."""

    HARUNEKO_DIR = os.path.join(os.getenv("APPDATA") or "", "HaruNeko")
    TOOLS_DATA_DIR = resource_path(os.path.join("BBSL", "herramientas_datos"))
    GENERAL_TOOLS_FOLDER = resource_path(os.path.join("BBSL", "herramientas"))
    WINDOW_TITLE = "Babylon Scanlation"
    WINDOW_SIZE = (1200, 600)

    @staticmethod
    def load_user_settings() -> Dict[str, Any]:
        settings: Dict[str, Any] = {
            "GEMINI_MODEL": "gemini-2.5-flash",
            "GEMINI_ENABLE_THINKING": True,
            "GEMINI_TEMPERATURE": 1.0,
            "GEMINI_ULTRA_HIGH_QUALITY": False,
            "ENABLE_AUTO_MODEL_SWITCH": True,
            "GEMINI_SYSTEM_INSTRUCTION": DEFAULT_GEMINI_SYSTEM_INSTRUCTION,
            "GEMINI_API_KEY": "",
            "DAILY_REQUEST_COUNT": 0,
            "LAST_REQUEST_DATE": "",
        }
        if os.path.exists(USER_SETTINGS_FILE):
            try:
                with open(USER_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    user_settings: Dict[str, Any] = json.load(f)
                    settings.update(user_settings)

            except Exception:
                pass
        return settings

    @staticmethod
    def save_user_settings(new_settings: Dict[str, Any]):
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        try:
            # Cargar configuración existente
            settings: Dict[str, Any] = Config.load_user_settings()
            settings.update(new_settings)

            # PROTECCIÓN: Si las keys existen en el entorno (.env), NO guardarlas en el JSON.
            # Esto evita que una key individual sobrescriba la lista de rotación del entorno.
            if os.getenv("GEMINI_API_KEY") and "GEMINI_API_KEY" in settings:
                del settings["GEMINI_API_KEY"]

            if os.getenv("MISTRAL_API_KEY") and "MISTRAL_API_KEY" in settings:
                del settings["MISTRAL_API_KEY"]

            with open(USER_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=4)
        except Exception:
            pass

    user_settings: Dict[str, Any] = load_user_settings()

    # --- MODEL LIMITS DEFINITION (2026 Free Tier Constraints) ---
    # Structure: {ModelName: (RPM, TPM, RPD)}
    MODEL_LIMITS: Dict[str, Dict[str, int]] = {
        "gemini-2.5-flash": {"RPM": 5, "TPM": 250000, "RPD": 20},
        "gemini-2.5-flash-lite": {"RPM": 10, "TPM": 250000, "RPD": 20},
        "gemini-3-flash-preview": {"RPM": 5, "TPM": 250000, "RPD": 20},
    }

    # --- LÓGICA DE ROTACIÓN DE KEYS ---
    # PRIORIDAD: .env > user_settings.json
    # Si existen keys en el entorno, se usan esas (para soportar listas/rotación).
    # Si no, se busca en el JSON (para usuarios que solo usan la UI).
    _env_keys = os.getenv("GEMINI_API_KEY", "").strip()
    _json_keys = str(user_settings.get("GEMINI_API_KEY", "")).strip()
    _raw_keys: str = _env_keys if _env_keys else _json_keys

    GEMINI_API_KEYS: List[str] = list(
        dict.fromkeys(k.strip() for k in _raw_keys.split(",") if k.strip())
    )
    GEMINI_API_KEY: str = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""

    @classmethod
    def get_next_gemini_key(cls, current_key: str) -> str:
        """Rota a la siguiente API Key disponible."""
        if not cls.GEMINI_API_KEYS:
            return current_key
        try:
            idx = cls.GEMINI_API_KEYS.index(current_key)
            return cls.GEMINI_API_KEYS[(idx + 1) % len(cls.GEMINI_API_KEYS)]
        except ValueError:
            return cls.GEMINI_API_KEYS[0]

    MISTRAL_API_KEY: str = str(
        user_settings.get("MISTRAL_API_KEY", os.getenv("MISTRAL_API_KEY", ""))
    )
    MISTRAL_MODEL: str = str(user_settings.get("MISTRAL_MODEL", "mistral-large-latest"))
    MISTRAL_TEMPERATURE: float = float(user_settings.get("MISTRAL_TEMPERATURE", 0.7))
    DEEPL_API_KEY: str = str(
        user_settings.get("DEEPL_API_KEY", os.getenv("DEEPL_API_KEY", ""))
    )

    GEMINI_MODEL: str = str(user_settings["GEMINI_MODEL"])
    GEMINI_ENABLE_THINKING: bool = bool(user_settings["GEMINI_ENABLE_THINKING"])
    GEMINI_TEMPERATURE: float = float(user_settings["GEMINI_TEMPERATURE"])
    GEMINI_ULTRA_HIGH_QUALITY: bool = bool(
        user_settings.get("GEMINI_ULTRA_HIGH_QUALITY", False)
    )
    ENABLE_AUTO_MODEL_SWITCH: bool = bool(user_settings["ENABLE_AUTO_MODEL_SWITCH"])
    GEMINI_SYSTEM_INSTRUCTION: str = str(
        user_settings.get(
            "GEMINI_SYSTEM_INSTRUCTION", DEFAULT_GEMINI_SYSTEM_INSTRUCTION
        )
    )

    # Runtime usage tracking (loaded from settings)
    DAILY_REQUEST_COUNT: int = int(user_settings.get("DAILY_REQUEST_COUNT", 0))
    LAST_REQUEST_DATE: str = str(user_settings.get("LAST_REQUEST_DATE", ""))

    GEMINI_PROMPT: str = resource_path(
        os.path.join("BBSL", "herramientas_datos", "ai_prompt_user.txt")
    )
    MISTRAL_PROMPT: str = resource_path(
        os.path.join("BBSL", "herramientas_datos", "ai_prompt_user.txt")
    )

    ICON_PATH = resource_path(os.path.join("app_media", "img-aux", "icono.ico"))
    CAROUSEL_IMAGES: List[str] = []
    CAROUSEL_INTERVAL = 60000
    LOGO_PATH = resource_path(os.path.join("app_media", "img-aux", "logo.png"))
    VIDEO_PATH = ""  # Video eliminado para reducir tamaño
    BACKGROUND_PATH = ""
    AUDIO_FILES: List[str] = []  # Audios eliminados para reducir tamaño
    HELP_VIDEOS = [
        {"id": "QwxG2S_PCMQ", "title": "Guía sobre TyperTools"},
        {"id": "q94bCgsk3_Q", "title": "Cómo usar TyperTools"},
        {
            "id": "XLHSXhpQZn0&list=PLWN_byxCBu5JOjT-c37n_uOQUXAPfPMdP",
            "title": "Tutoriales con Illustrator CS6",
        },
        {
            "id": "mc_NsADu_t8&list=PLmJE_P_j3_IdRGxwnC27NMEO850KiSokN",
            "title": "Tutoriales con Photoshop CS6",
        },
        {"id": "qn33rhNBr6k", "title": "Cómo typear Comics"},
        {"id": "yizQYYMbPHo", "title": "Cómo limpiar Comics"},
        {"id": "-rzaKLRx1LE", "title": "Cómo redibujar Comics"},
        {"id": "KkFaq4-WHbU", "title": "Cómo organizar tus tipografías"},
    ]
    SUPPORTED_FORMATS = (".jpg", ".png", ".jpeg", ".gif", ".webp")
    FONT_DIR = resource_path(os.path.join("BBSL", "fuentes"))
    FONT_PATHS = [
        resource_path(os.path.join(FONT_DIR, "SuperCartoon.ttf")),
        resource_path(os.path.join(FONT_DIR, "Adventure.otf")),
        resource_path(os.path.join(FONT_DIR, "RobotoBlack.ttf")),
    ]
    ABOUT_TEXT = (
        "'Babylon Scanlation' es un programa diseñado "
        "para ayudar a los usuarios a organizarse y acceder a diversas herramientas."
    )
    TOOL_DESCRIPTIONS = {
        "01_ocr.png": "Varios OCR's (Reconocimiento óptico de carácteres) para extraer el texto de imágenes.",
        "02_traductor.png": "Varios traductores de texto.",
        "03_ai.png": "Varias Inteligencias Artificiales del tipo Multimodal.",
        "04_descargar_capitulos.png": "Varias maneras para descargar el RAW de una serie.",
        "05_mejorar_imagen.png": "Varias formas para mejorar la calidad de las imágenes [PROXIMAMENTE].",
        "06_nsfw.png": "Varios programas para detectar NSFW en imágenes para evitar baneos en páginas externas [PROXIMAMENTE].",
        "07_fuentes.png": "Varias fuentes y programas para organizar las tipografías [PROXIMAMENTE].",
        "08_editor_texto.png": "Aplicaciones para editar texto [PROXIMAMENTE].",
    }
    UTILITIES_FOOTER_TEXT = "Más herramientas pronto."
    PROJECTS_FOOTER_TEXT = "Tus proyectos estarán aquí."
    TOOL_URLS: Dict[str, str] = {
        "Bing": "https://www.bing.com/Translator",
        "Yandex": "https://translate.yandex.com/",
        "Sogou": "https://fanyi.sogou.com/text",
        "TranSmart": "https://transmart.qq.com",
        "Caiyun": "https://fanyi.caiyunapp.com/",
        "Alibaba": "https://translate.alibaba.com",
        "Baidu": "https://fanyi.baidu.com",
        "iTranslate": "https://itranslate.com/translate",
        "CloudTrans": "https://www.cloudtranslation.com/#/translate",
        "SysTran": "https://www.systransoft.com/translate/",
        "Lingvanex": "https://lingvanex.com/translate/",
        "Papago": "https://papago.naver.com/",
        "DeepL": "https://www.deepl.com/es/translator",
        "Google": "https://translate.google.com",
        "Paddle": "https://aistudio.baidu.com/community/app/91660/webUI",
        "Easy": "https://www.jaided.ai/easyocr/",
        "Tesseract": "https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.0",
        "Gemini": "https://gemini.google.com/app",
        "Mistral": "https://chat.mistral.ai/chat",
        "HaruNeko": "https://github.com/manga-download/hakuneko",
    }
    AI_PROMPT = resource_path(
        os.path.join("BBSL", "herramientas_datos", "ai_prompt.txt")
    )
    AI_PROMPT_USER = resource_path(
        os.path.join("BBSL", "herramientas_datos", "ai_prompt_user.txt")
    )
    GRILLA_PROMPT = resource_path(
        os.path.join("BBSL", "herramientas_datos", "prompt_personajes.txt")
    )
    GENERAL_TOOLS = [
        resource_path(os.path.join("BBSL", "herramientas", "01_ocr.png")),
        resource_path(os.path.join("BBSL", "herramientas", "02_traductor.png")),
        resource_path(os.path.join("BBSL", "herramientas", "03_ai.png")),
        resource_path(
            os.path.join("BBSL", "herramientas", "04_descargar_capitulos.png")
        ),
        resource_path(os.path.join("BBSL", "herramientas", "05_mejorar_imagen.png")),
        resource_path(os.path.join("BBSL", "herramientas", "06_nsfw.png")),
        resource_path(os.path.join("BBSL", "herramientas", "07_fuentes.png")),
        resource_path(os.path.join("BBSL", "herramientas", "08_editor_texto.png")),
    ]
    SPECIFIED_TOOLS: Dict[str, List[Dict[str, Any]]] = {
        "ocr": [
            {
                "name": "Paddle",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "ocr", "paddle.png")
                ),
                "description": "OCR ligero y preciso con detección avanzada de texto.",
                "rating": 5.7,
                "access_paths": [
                    {
                        "label": "Ruta Principal",
                        "path": resource_path(
                            os.path.join("BBSL", "herramientas", "ocr")
                        ),
                    },
                    {
                        "label": "Ruta Secundaria",
                        "path": resource_path(
                            os.path.join("BBSL", "herramientas", "ocr")
                        ),
                    },
                ],
            },
            {
                "name": "Easy",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "ocr", "easy.png")
                ),
                "description": "OCR de código abierto, sencillo y multilingüe.",
                "rating": 5.2,
                "access_paths": [
                    {
                        "label": "Ruta Principal",
                        "path": resource_path(
                            os.path.join("BBSL", "herramientas", "ocr")
                        ),
                    },
                    {
                        "label": "Ruta Secundaria",
                        "path": resource_path(
                            os.path.join("BBSL", "herramientas", "ocr")
                        ),
                    },
                ],
            },
            {
                "name": "Tesseract",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "ocr", "tesseract.png")
                ),
                "description": "Herramienta OCR robusta y altamente personalizable por Google.",
                "rating": 1.3,
                "access_paths": [
                    {
                        "label": "Ruta Principal",
                        "path": resource_path(
                            os.path.join("BBSL", "herramientas", "ocr")
                        ),
                    },
                    {
                        "label": "Ruta Secundaria",
                        "path": resource_path(
                            os.path.join("BBSL", "herramientas", "ocr")
                        ),
                    },
                ],
            },
        ],
        "traductor": [
            {
                "name": "Gemini",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "gemini.png")
                ),
                "description": "IA de Google para traducciones precisas y contextualizadas.",
                "rating": 9.78,
            },
            {
                "name": "Mistral",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "mistral.png")
                ),
                "description": "Modelo europeo optimizado para traducciones técnicas y profesionales.",
                "rating": 9.23,
            },
            {
                "name": "Papago",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "papago.png")
                ),
                "description": "Líder en traducciones asiáticas, ideal para coreano y japonés.",
                "rating": 7.81,
            },
            {
                "name": "DeepL",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "deepl.png")
                ),
                "description": "Traducciones con calidad natural y fluidez excepcional.",
                "rating": 7.76,
            },
            {
                "name": "Google",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "google.png")
                ),
                "description": "Traductor universal con soporte para más de 130 idiomas.",
                "rating": 7.43,
            },
            {
                "name": "Bing",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "bing.png")
                ),
                "description": "Traductor de Microsoft con enfoque corporativo y colaborativo.",
                "rating": 7.42,
            },
            {
                "name": "Yandex",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "yandex.png")
                ),
                "description": "Líder indiscutible en traducción de lenguas eslavas.",
                "rating": 7.10,
            },
            {
                "name": "Sogou",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "sogou.png")
                ),
                "description": "Especialista en mandarín, dialectos regionales y jerga de internet.",
                "rating": 7.02,
            },
            {
                "name": "TranSmart",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "qqtransmart.png")
                ),
                "description": "Optimizado para e-commerce, productos tecnológicos y comercio global.",
                "rating": 6.27,
            },
            {
                "name": "Caiyun",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "caiyun.png")
                ),
                "description": "Traducción ultrarrápida para redes sociales y contenido en vivo.",
                "rating": 5.14,
            },
            {
                "name": "Alibaba",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "alibaba.png")
                ),
                "description": "Traductor enfocado en comercio internacional y listados de productos.",
                "rating": 5.05,
            },
            {
                "name": "Baidu",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "baidu.png")
                ),
                "description": "Motor líder en China para mandarín coloquial y dialectos.",
                "rating": 4.78,
            },
            {
                "name": "iTranslate",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "itranslate.png")
                ),
                "description": "Asistente móvil versátil para viajeros y conversaciones rápidas.",
                "rating": 4.38,
            },
            {
                "name": "CloudTrans",
                "image_path": resource_path(
                    os.path.join(
                        "BBSL", "herramientas", "traductor", "cloudtranslation.png"
                    )
                ),
                "description": "Traductor básico y genérico para integración rápida en proyectos.",
                "rating": 4.01,
            },
            {
                "name": "SysTran",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "systran.png")
                ),
                "description": "Software clásico con soporte para lenguajes y formatos antiguos.",
                "rating": 3.19,
            },
            {
                "name": "Lingvanex",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "traductor", "lingvanex.png")
                ),
                "description": "Traducciones privadas con opción de servidores locales para empresas.",
                "rating": 2.67,
            },
        ],
        "ai": [
            {
                "name": "Gemini",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "ai", "gemini.png")
                ),
                "description": "IA avanzada para tareas complejas y análisis de contexto.",
                "rating": 8.4,
                "access_paths": [
                    {"label": "PROMPT", "path": AI_PROMPT},
                    {"label": "API", "path": GEMINI_API_KEY},
                ],
            },
            {
                "name": "Mistral",
                "image_path": resource_path(
                    os.path.join("BBSL", "herramientas", "ai", "mistral.png")
                ),
                "description": "IA europea versátil con conocimientos actualizados y búsqueda web.",
                "rating": 8.1,
                "access_paths": [
                    {"label": "PROMPT", "path": AI_PROMPT},
                    {"label": "API", "path": MISTRAL_API_KEY},
                ],
            },
        ],
        "ch_downloaders": [
            {
                "name": "HaruNeko",
                "image_path": resource_path(
                    os.path.join(
                        "BBSL", "herramientas", "ch_downloaders", "hakuneko.png"
                    )
                ),
                "rating": 10,
                "description": "Descargador de código abierto para mangas desde múltiples fuentes.",
            },
            {
                "name": "Suwayomi",
                "image_path": resource_path(
                    os.path.join(
                        "BBSL", "herramientas", "ch_downloaders", "suwayomi.png"
                    )
                ),
                "rating": 10,
                "description": "Lector y gestor de bibliotecas manga basado en Tachiyomi.",
            },
            {
                "name": "Babylon",
                "image_path": resource_path(
                    os.path.join(
                        "BBSL", "herramientas", "ch_downloaders", "babylon.png"
                    )
                ),
                "rating": 9.5,
                "description": "Descargador multi-sitio de raws desarrollado para Babylon Scanlation.",
            },
        ],
    }

    BABYLON_SITES = [
        {
            "name": "18MH",
            "image_path": resource_path(
                os.path.join(
                    "BBSL", "herramientas", "ch_downloaders", "babylon", "18mh.png"
                )
            ),
            "description": "Manga y manhwa raw en chino/japonés",
            "url": "https://18mh.org",
            "status": "Activo",
            "type": "18mh",
            "file": "18mh_downloader.py",
        },
        {
            "name": "BakaMH",
            "image_path": resource_path(
                os.path.join(
                    "BBSL", "herramientas", "ch_downloaders", "babylon", "bakamh.png"
                )
            ),
            "description": "Manga raw en chino — motor Madara",
            "url": "https://bakamh.com",
            "status": "Activo",
            "type": "bakamh",
            "file": "bakamh_downloader.py",
        },
        {
            "name": "BaoziMH",
            "image_path": resource_path(
                os.path.join(
                    "BBSL", "herramientas", "ch_downloaders", "babylon", "baozimh.png"
                )
            ),
            "description": "Manga/manhwa — múltiples mirrors",
            "url": "https://baozimh.org",
            "status": "Activo",
            "type": "baozimh",
            "file": "baozimh_downloader.py",
        },
        {
            "name": "Dumanwu",
            "image_path": resource_path(
                os.path.join(
                    "BBSL", "herramientas", "ch_downloaders", "babylon", "dumanwu.png"
                )
            ),
            "description": "Manhua raw en chino con imágenes encriptadas",
            "url": "https://dumanwu.com",
            "status": "Activo",
            "type": "dumanwu",
            "file": "dumanwu_downloader.py",
        },
        {
            "name": "Hitomi",
            "image_path": resource_path(
                os.path.join(
                    "BBSL", "herramientas", "ch_downloaders", "babylon", "hitomi.png"
                )
            ),
            "description": "Galería de doujinshi y manga en japonés",
            "url": "https://hitomi.la",
            "status": "Activo",
            "type": "hitomi",
            "file": "hitomi_downloader.py",
        },
        {
            "name": "Fanfox",
            "image_path": resource_path(
                os.path.join(
                    "BBSL", "herramientas", "ch_downloaders", "babylon", "mangafox.png"
                )
            ),
            "description": "Catálogo grande de manga en inglés",
            "url": "https://fanfox.net",
            "status": "Activo",
            "type": "mangafox",
            "file": "mangafox_downloader.py",
        },
        {
            "name": "ManhuaGui",
            "image_path": resource_path(
                os.path.join(
                    "BBSL", "herramientas", "ch_downloaders", "babylon", "manhuagui.png"
                )
            ),
            "description": "Manhua chino — fuente primaria de raws",
            "url": "https://manhuagui.com",
            "status": "Activo",
            "type": "manhuagui",
            "file": "manhuagui_downloader.py",
        },
        {
            "name": "PicaComic",
            "image_path": resource_path(
                os.path.join(
                    "BBSL", "herramientas", "ch_downloaders", "babylon", "picacomic.png"
                )
            ),
            "description": "Cómic y manga — requiere cuenta oficial",
            "url": "https://picacomic.com",
            "status": "Activo",
            "type": "picacomic",
            "file": "picacomic_downloader.py",
        },
        {
            "name": "ToonKor",
            "image_path": resource_path(
                os.path.join(
                    "BBSL", "herramientas", "ch_downloaders", "babylon", "toonkor.png"
                )
            ),
            "description": "Webtoon coreano raw",
            "url": "https://toonkor.com",
            "status": "Activo",
            "type": "toonkor",
            "file": "toonkor_downloader.py",
        },
        {
            "name": "WFWF",
            "image_path": resource_path(
                os.path.join(
                    "BBSL", "herramientas", "ch_downloaders", "babylon", "wfwf.png"
                )
            ),
            "description": "Webtoon + manhwa coreano raw",
            "url": "https://wfwf448.com",
            "status": "Activo",
            "type": "wfwf",
            "file": "wfwf_downloader.py",
        },
    ]

    def get_user_data_dir(self) -> str:
        """Obtiene la ruta del directorio de datos del usuario."""
        return USER_DATA_DIR

    def get_tools_data_dir(self) -> str:
        """Obtiene la ruta del directorio de datos de herramientas."""
        return self.TOOLS_DATA_DIR
