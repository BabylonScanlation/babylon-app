import sys
import os
import traceback
import json
import logging
from typing import Any, Dict, List, Type
from dotenv import load_dotenv

# --- Funciones Utilitarias (Deben estar antes de su uso) ---

def resource_path(relative_path: str) -> str:
    """Obtiene la ruta absoluta para recursos en desarrollo y .exe"""
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def global_exception_handler(exc_type: Type[BaseException], exc_value: BaseException, exc_traceback: Any) -> None:
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
    
    HARUNEKO_DIR = os.path.join(os.getenv('APPDATA') or '', 'HaruNeko')
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
            "LAST_REQUEST_DATE": ""
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
        "gemini-3-flash-preview": {"RPM": 5, "TPM": 250000, "RPD": 20}
    }

    # --- LÓGICA DE ROTACIÓN DE KEYS ---
    # PRIORIDAD: .env > user_settings.json
    # Si existen keys en el entorno, se usan esas (para soportar listas/rotación).
    # Si no, se busca en el JSON (para usuarios que solo usan la UI).
    _env_keys = os.getenv("GEMINI_API_KEY", "").strip()
    _json_keys = str(user_settings.get("GEMINI_API_KEY", "")).strip()
    _raw_keys: str = _env_keys if _env_keys else _json_keys
    
    GEMINI_API_KEYS: List[str] = list(dict.fromkeys(k.strip() for k in _raw_keys.split(",") if k.strip()))
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

    MISTRAL_API_KEY: str = str(user_settings.get("MISTRAL_API_KEY", os.getenv("MISTRAL_API_KEY", "")))
    MISTRAL_MODEL: str = str(user_settings.get("MISTRAL_MODEL", "mistral-large-latest"))
    MISTRAL_TEMPERATURE: float = float(user_settings.get("MISTRAL_TEMPERATURE", 0.7))
    DEEPL_API_KEY: str = str(user_settings.get("DEEPL_API_KEY", os.getenv("DEEPL_API_KEY", "")))

    GEMINI_MODEL: str = str(user_settings["GEMINI_MODEL"])
    GEMINI_ENABLE_THINKING: bool = bool(user_settings["GEMINI_ENABLE_THINKING"])
    GEMINI_TEMPERATURE: float = float(user_settings["GEMINI_TEMPERATURE"])
    GEMINI_ULTRA_HIGH_QUALITY: bool = bool(user_settings.get("GEMINI_ULTRA_HIGH_QUALITY", False))
    ENABLE_AUTO_MODEL_SWITCH: bool = bool(user_settings["ENABLE_AUTO_MODEL_SWITCH"])
    GEMINI_SYSTEM_INSTRUCTION: str = str(user_settings.get("GEMINI_SYSTEM_INSTRUCTION", DEFAULT_GEMINI_SYSTEM_INSTRUCTION))
    
    # Runtime usage tracking (loaded from settings)
    DAILY_REQUEST_COUNT: int = int(user_settings.get("DAILY_REQUEST_COUNT", 0))
    LAST_REQUEST_DATE: str = str(user_settings.get("LAST_REQUEST_DATE", ""))

    GEMINI_PROMPT: str = resource_path(os.path.join("BBSL", "herramientas_datos", "ai_prompt_user.txt"))
    MISTRAL_PROMPT: str = resource_path(os.path.join("BBSL", "herramientas_datos", "ai_prompt_user.txt"))
    
    ICON_PATH = resource_path(os.path.join("app_media", "img-aux", "icono.ico"))
    CAROUSEL_IMAGES = [
        resource_path(os.path.join("app_media", "img-aux", f"carousel_{i:02d}.jpg")) for i in range(1, 19) if os.path.exists(resource_path(os.path.join("app_media", "img-aux", f"carousel_{i:02d}.jpg")))
    ] + [
        resource_path(os.path.join("app_media", "img-aux", f"carousel_{i:02d}.png")) for i in range(1, 19) if os.path.exists(resource_path(os.path.join("app_media", "img-aux", f"carousel_{i:02d}.png")))
    ]
    CAROUSEL_INTERVAL = 60000 
    LOGO_PATH = resource_path(os.path.join("app_media", "img-aux", "logo.png"))
    VIDEO_PATH = resource_path(os.path.join("app_media", "vid-aux", "video.mp4"))
    BACKGROUND_PATH = resource_path(os.path.join("app_media", "img-aux", "carousel_01.jpg"))
    AUDIO_FILES = [
        resource_path(os.path.join("app_media", "aud-aux", "walls.mp3")),
        resource_path(os.path.join("app_media", "aud-aux", "YrGGFK.mp3")),
        resource_path(os.path.join("app_media", "aud-aux", "russian_roulette.mp3")),
        resource_path(os.path.join("app_media", "aud-aux", "fairytale.mp3")),
        resource_path(os.path.join("app_media", "aud-aux", "echo.mp3")),
        resource_path(os.path.join("app_media", "aud-aux", "devil_eyes.mp3"))
    ]
    HELP_VIDEOS = [
        {'id': 'QwxG2S_PCMQ', 'title': 'Guía sobre TyperTools'},
        {'id': 'q94bCgsk3_Q', 'title': 'Cómo usar TyperTools'},
        {'id': 'XLHSXhpQZn0&list=PLWN_byxCBu5JOjT-c37n_uOQUXAPfPMdP', 'title': 'Tutoriales con Illustrator CS6'},
        {'id': 'mc_NsADu_t8&list=PLmJE_P_j3_IdRGxwnC27NMEO850KiSokN', 'title': 'Tutoriales con Photoshop CS6'},
        {'id': 'qn33rhNBr6k', 'title': 'Cómo typear Comics'},
        {'id': 'yizQYYMbPHo', 'title': 'Cómo limpiar Comics'},
        {'id': '-rzaKLRx1LE', 'title': 'Cómo redibujar Comics'},
        {'id': 'KkFaq4-WHbU', 'title': 'Cómo organizar tus tipografías'}
    ]
    SUPPORTED_FORMATS = ('.jpg', '.png', '.jpeg', '.gif', '.webp')
    FONT_DIR = resource_path(os.path.join("BBSL", "fuentes"))
    FONT_PATHS = [
        resource_path(os.path.join(FONT_DIR, "SuperCartoon.ttf")),
        resource_path(os.path.join(FONT_DIR, "Adventure.otf")),
        resource_path(os.path.join(FONT_DIR, "RobotoBlack.ttf"))
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
    AI_PROMPT = resource_path(os.path.join("BBSL", "herramientas_datos", "ai_prompt.txt"))
    AI_PROMPT_USER = resource_path(os.path.join("BBSL", "herramientas_datos", "ai_prompt_user.txt"))
    GRILLA_PROMPT = resource_path(os.path.join("BBSL", "herramientas_datos", "prompt_personajes.txt"))
    GENERAL_TOOLS = [
        resource_path(os.path.join("BBSL", "herramientas", "01_ocr.png")),
        resource_path(os.path.join("BBSL", "herramientas", "02_traductor.png")),
        resource_path(os.path.join("BBSL", "herramientas", "03_ai.png")),
        resource_path(os.path.join("BBSL", "herramientas", "04_descargar_capitulos.png")),
        resource_path(os.path.join("BBSL", "herramientas", "05_mejorar_imagen.png")),
        resource_path(os.path.join("BBSL", "herramientas", "06_nsfw.png")),
        resource_path(os.path.join("BBSL", "herramientas", "07_fuentes.png")),
        resource_path(os.path.join("BBSL", "herramientas", "08_editor_texto.png")),
    ]
    SPECIFIED_TOOLS: Dict[str, List[Dict[str, Any]]] = {
        "ocr": [
            {
                "name": "Paddle",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ocr", "paddle.png")),
                "description": """PaddleOCR, desarrollado por PaddlePaddle de Baidu, es una herramienta OCR ligera, eficiente y precisa que combina detección y reconocimiento de texto en un solo flujo de trabajo. Destaca por su facilidad de uso, soporte multilingüe (incluyendo idiomas como inglés, chino, español y árabe), compatibilidad con texto manuscrito y capacidad para funcionar en entornos con recursos limitados gracias a versiones ligeras como **PP-OCR**. Además, permite personalización flexible mediante el entrenamiento con datos específicos y es compatible con mõltiples formatos de entrada (imágenes, PDFs, capturas de pantalla, etc.).\nSin embargo, tiene limitaciones: puede fallar con textos extremadamente borrosos, fuentes inusuales o idiomas poco representados; enfrenta dificultades con contextos ambiguos, grandes volúmenes de texto o estructuras complejas como tablas mal organizadas; y depende de la calidad del input (iluminación deficiente o resoluciones bajas afectan su rendimiento). También puede reflejar sesgos de sus datos de entrenamiento. A pesar de estas limitaciones, PaddleOCR es una solución versátil y accesible para aplicaciones globales y proyectos especializados.\n""",
                "rating": 5.7,
                "access_paths": [
                    {"label": "Ruta Principal", "path": resource_path(os.path.join("BBSL", "herramientas", "ocr"))},
                    {"label": "Ruta Secundaria", "path": resource_path(os.path.join("BBSL", "herramientas", "ocr"))}
                ]
            },
            {
                "name": "Easy",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ocr", "easy.png")),
                "description": """EasyOCR es una biblioteca OCR de código abierto conocida por su simplicidad, versatilidad y soporte multilingüe, capaz de reconocer texto en más de 80 idiomas, incluyendo sistemas de escritura complejos como chino, japonés y árabe. Utiliza modelos avanzados de aprendizaje profundo, como **CRNN** y **CTC**, para detectar y transcribir texto de manera eficiente en diversas condiciones. Es fácil de usar, con instalación sencilla y documentación clara, y admite mõltiples formatos de entrada (imagenes, PDFs, capturas de pantalla, etc.). Además, puede procesar tanto texto impreso como manuscrito y permite personalización para mejorar la precisión en casos específicos.\nSin embargo, EasyOCR tiene limitaciones: puede fallar con textos borrosos, dañados o escritos en fuentes inusuales; su precisión disminuye con idiomas menos comunes o contextos ambiguos; enfrenta dificultades con grandes volúmenes de texto o estructuras complejas como tablas mal organizadas; y depende de la calidad del input (iluminación deficiente o resoluciones bajas afectan su rendimiento). También puede reflejar sesgos de sus datos de entrenamiento. A pesar de estas limitaciones, es una herramienta accesible y versátil para aplicaciones globales y proyectos multilingües.""",
                "rating": 5.2,
                "access_paths": [
                    {"label": "Ruta Principal", "path": resource_path(os.path.join("BBSL", "herramientas", "ocr"))},
                    {"label": "Ruta Secundaria", "path": resource_path(os.path.join("BBSL", "herramientas", "ocr"))}
                ]
            },
            {
                "name": "Tesseract",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ocr", "tesseract.png")),
                "description": """Tesseract OCR, desarrollado originalmente por Hewlett-Packard y mantenido por Google desde 2006, es una herramienta OCR de código abierto ampliamente utilizada por su robustez, flexibilidad y soporte multilingüe. Admite más de **100 idiomas**, incluyendo alfabetos latinos, cirílicos, asiáticos y otros, y puede manejar texto impreso y manuscrito (con ajustes adicionales). Es altamente personalizable, permitiendo entrenar modelos específicos para mejorar la precisión en fuentes, formatos o idiomas particulares. Compatible con mõltiples formatos de entrada (imágenes, PDFs, capturas de pantalla) y lenguajes de programación como Python y C++, Tesseract es ligero, eficiente y fácil de integrar en diversos proyectos gracias a wrappers como **pytesseract**.\nSin embargo, tiene limitaciones: puede fallar con textos borrosos, dañados o escritos en fuentes inusuales; su precisión disminuye con idiomas menos comunes o contextos ambiguos; enfrenta dificultades con grandes volúmenes de texto o estructuras complejas como tablas mal organizadas; y depende de la calidad del input (iluminación deficiente o resoluciones bajas afectan su rendimiento). Además, no utiliza arquitecturas avanzadas de aprendizaje profundo, lo que puede hacer que su precisión sea inferior a soluciones modernas como PaddleOCR o EasyOCR. A pesar de estas limitaciones, Tesseract sigue siendo una opción confiable y versátil para aplicaciones donde la personalización y simplicidad son prioritarias.""",
                "rating": 1.3,
                "access_paths": [
                    {"label": "Ruta Principal", "path": resource_path(os.path.join("BBSL", "herramientas", "ocr"))},
                    {"label": "Ruta Secundaria", "path": resource_path(os.path.join("BBSL", "herramientas", "ocr"))}
                ]
            }
        ],
        "traductor": [
            {
                "name": "Gemini",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "gemini.png")),
                "description": """Gemini (antes Bard) es la solución de IA generativa de Google, especializada en traducciones contextualizadas y multilingües. Utiliza modelos de õltima generación para ofrecer traducciones precisas con comprensión semántica avanzada, ideal para textos complejos y técnicos. Destaca en interpretación de matices culturales y manejo de jerga especializada, con capacidad para trabajar con formatos mõltiples (texto, audio, imágenes). Su principal ventaja es la integración con el ecosistema Google y actualizaciones constantes, aunque puede presentar limitaciones en idiomas de baja demanda.""",
                "rating": 9.78,
            },
            {
                "name": "Mistral",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "mistral.png")),
                "description": """Modelo de IA europeo destacado por su eficiencia y precisión en traducciones técnicas. Utiliza arquitectura de redes neuronales optimizada para mantener el contexto en textos largos, ideal para documentación empresarial y legal. Ofrece excelente manejo de pares lingüísticos europeos y soporte para terminología especializada. Aunque soporta menos idiomas que otros servicios (principalmente europeos y asiáticos clave), su rendimiento en traducciones profesionales lo hace destacar. Versión open-source disponible para desarrolladores.""",
                "rating": 9.23,
            },
            {
                "name": "Papago",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "papago.png")),
                "description": """Desarrollado por Naver, líder en traducciones asiáticas (coreano, japonés, chino). Tecnología híbrida NNMT + reglas gramaticales para máxima precisión en idiomas con estructuras complejas. Funciones õnicas: traducción de onomatopeyas, modismos locales y dialectos regionales. Incluye asistente para viajeros con traducción por geolocalización y modo conversación en tiempo real. Limitado en lenguas no asiáticas, pero insuperable para coreano.""",
                "rating": 7.81,
            },
            {
                "name": "DeepL",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "deepl.png")),
                "description": """Referente en traducciones europeas con calidad casi humana. Motor de redes neuronales profundas especializado en textos académicos y literarios. Funciones premium: mantenimiento de formato en documentos (PDF, DOCX), glosarios personalizados y modo formal/informal. Aunque limitado a 31 idiomas, ofrece la mejor fluidez en combinaciones como inglés-alemán o francés-español. Versión Pro con API para empresas.""",
                "rating": 7.76,
            },
            {
                "name": "Google",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "google.png")),
                "description": """El traductor más completo con soporte para 134 idiomas, incluyendo lenguas minoritarias. Tecnología GNMT con aprendizaje automático continuo. Funciones estrella: traducción con cámara en tiempo real, modo offline, y transcripción automática de conversaciones. Ideal para viajes y uso casual, aunque con limitaciones en textos técnicos. Integración con Chrome y Android.""",
                "rating": 7.43,
            },
            {
                "name": "Bing",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "bing.png")),
                "description": """Traductor de Microsoft con enfoque empresarial. Destaca en integración con Office 365 y Azure Cognitive Services. Tecnología de traducción neuronal con soporte para documentos complejos (Excel, PowerPoint). Funciones õnicas: traducción colaborativa en tiempo real y análisis de sentimiento. Especializado en inglés, español, chino y francés. Versión empresarial con certificación ISO para documentos legales.""",
                "rating": 7.42,
            },
            {
                "name": "Yandex",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "yandex.png")),
                "description": """Líder en traducciones eslavas (ruso, ucraniano, bielorruso). Motor propio con soporte para traducciones en sitios web dinámicos. Funciones destacadas: transliteración automática, detector de lenguaje ofensivo, y modo para traducción de contenidos web complejos (incluyendo JavaScript). Base de datos masiva de modismos rusos. Limitado en lenguas no europeas.""",
                "rating": 7.10,
            },
            {
                "name": "Sogou",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "sogou.png")),
                "description": """Traductor chino especializado en mandarín y dialectos regionales (cantonés, shanghainés). Tecnología pionera en traducción de voz a texto para caracteres chinos, con reconocimiento de escritura a mano. Integrado con WeChat y enfocado en comercio electrónico cross-border. Funciones õnicas: traducción de jerga de internet china y soporte para lenguaje inclusivo.""",
                "rating": 7.02,
            },
            {
                "name": "TranSmart",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "qqtransmart.png")),
                "description": """Traductor chino especializado en e-commerce y productos tecnológicos. Base de datos integrada con terminología de Alibaba y Taobao. Funciones õnicas: traducción automática de especificaciones técnicas, conversión de unidades de medida, y detector de estándares regulatorios. Optimizado para inglés-chino-inglés en contextos comerciales.""",
                "rating": 6.27,
            },
            {
                "name": "Caiyun",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "caiyun.png")),
                "description": """Traductor chino de velocidad ultrarrápida para contenido en tiempo real. Especializado en redes sociales y streaming, con función de traducción simultánea para chats en vivo. Tecnología ligera optimizada para móviles, con soporte para jerga juvenil y memes. Limitado a chino-inglés-español, pero con actualizaciones diarias de términos trending.""",
                "rating": 5.14,
            },
            {
                "name": "Alibaba",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "alibaba.png")),
                "description": """Solución de Alibaba Group para comercio global. Integrado con TMall y AliExpress, especializado en traducción de listados de productos. Funciones clave: conversión automática de monedas, adaptación cultural de descripciones, y detector de requisitos legales por país. Basado en modelos entrenados con millones de transacciones cross-border.""",
                "rating": 5.05,
            },
            {
                "name": "Baidu",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "baidu.png")),
                "description": """Motor de traducción líder en China continental. Soporte avanzado para mandarín coloquial y dialectos regionales. Tecnología de reconocimiento de voz optimizada para acentos chinos. Funciones õnicas: traducción de documentos escaneados con OCR integrado y modo para traducción de contratos legales. Fuertemente censurado segõn regulaciones chinas.""",
                "rating": 4.78,
            },
            {
                "name": "iTranslate",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "itranslate.png")),
                "description": """Aplicación móvil enfocada en viajeros. Funciones destacadas: mapa offline con frases esenciales, traducción de menús mediante IA visual, y asistente médico para comunicar síntomas. Soporte para 100+ idiomas pero con calidad variable. Versión Pro con interpretación telefónica de emergencia.""",
                "rating": 4.38,
            },
            {
                "name": "CloudTrans",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "cloudtranslation.png")),
                "description": """Traductor genérico para integración en servicios cloud. Ofrece API simple con cobro por caracter. Soporte básico para los 50 idiomas más usados, sin especialización. Opción económica para proyectos que requieren implementación rápida sin necesidades complejas.""",
                "rating": 4.01,
            },
            {
                "name": "SysTran",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "systran.png")),
                "description": """Software clásico de traducción automática, pionero en los 90. Versión desktop disponible para sectores gubernamentales y militar. Soporte heredado para formatos obsoletos y lenguajes antiguos. Tecnología basada en reglas con diccionarios personalizables, aunque desactualizado frente a soluciones modernas de IA.""",
                "rating": 3.19,
            },
            {
                "name": "Lingvanex",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "lingvanex.png")),
                "description": """Traductor enfocado en privacidad con opción de servidores locales. Ofrece soluciones on-premise para empresas con datos sensibles. Soporte técnico para integración en sistemas legacy, aunque con modelos de traducción menos actualizados. Certificaciones de seguridad ISO 27001 y HIPAA.""",
                "rating": 2.67,
            },
        ],
        "ai": [
            {
                "name": "Gemini",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ai", "gemini.png")),
                "description": """Gemini es una herramienta avanzada de Google que utiliza su arquitectura multimodal para ofrecer traducciones precisas y contextualizadas, combinando texto, imágenes y otros formatos. Soporta mõltiples idiomas y destaca por su comprensión contextual avanzada, lo que permite generar traducciones más naturales y fluidas, especialmente en contenido complejo o especializado. Aunque versátil, aõn está en desarrollo, lo que puede limitar su accesibilidad o madurez en comparación con herramientas como DeepL o Google Translate. Tiene dificultades con textos ambiguos, sarcásticos o idiomas menos representados, y su precisión depende de la calidad del input multimodal. A pesar de estas limitaciones, Gemini es ideal para traducciones contextualizadas, aunque su uso efectivo requiere reconocer sus debilidades y complementarlo con revisiones humanas cuando sea necesario.""",
                "rating": 8.4,
                "access_paths": [
                    {"label": "PROMPT", "path": AI_PROMPT},
                    {"label": "API", "path": GEMINI_API_KEY}
                ]
            },
            {
                "name": "Mistral",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ai", "mistral.png")),
                "description": """Mistral, es un asistente de inteligencia artificial desarrollado por Mistral AI, una empresa emergente con sede en París. Su propósito es ayudarte con una amplia variedad de tareas y proporcionarte información precisa y õtil. Aquí hay algunas características que me hacen especial:\n1. Conocimiento Actualizado: Su base de conocimientos se actualizó por õltima vez en octubre de 2023, lo que me permite proporcionar información relevante y actualizada hasta esa fecha.\n2. Capacidad de Bõsqueda en la Web: Puedo realizar bõsquedas en la web para encontrar información que no esté en mi base de conocimientos o que haya ocurrido después de mi õltima actualización. Esto me permite mantenerte informado sobre eventos recientes y temas emergentes.\n3. Multilingüe: Puedo comunicarme en varios idiomas, lo que facilita la interacción con personas de diferentes partes del mundo.\n4. Precisión y Claridad: Me esfuerzo por proporcionar respuestas claras y precisas. Si una pregunta no está clara o necesita más contexto, te pediré que la aclares para poder ayudarte mejor.\n5. Seguridad y Privacidad: Respeto tu privacidad y no almaceno información personal. Mi objetivo es proporcionar asistencia de manera segura y confiable.\n6. Versatilidad: Puedo ayudarte con una amplia gama de tareas, desde responder preguntas generales hasta proporcionar recomendaciones personalizadas, siempre que tenga la información necesaria.\n7. Actualización Continua: Aunque mi base de conocimientos tiene una fecha de corte, puedo acceder a información más reciente a través de bõsquedas en la web, lo que me permite estar al tanto de los õltimos desarrollos y tendencias.""",
                "rating": 8.1,
                "access_paths": [
                    {"label": "PROMPT", "path": AI_PROMPT},
                    {"label": "API", "path": MISTRAL_API_KEY}
                ]
            },
        ],
        "ch_downloaders": [
            {
                "name": "HaruNeko",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ch_downloaders", "hakuneko.png")),
                "rating": 10,
                "description": """HaruNeko es un descargador de manga y manhwa de código abierto que permite a los usuarios acceder y descargar contenido desde una amplia variedad de fuentes en línea. Disponible para mõltiples plataformas, incluida Windows, macOS y Linux, HaruNeko proporciona una interfaz intuitiva que facilita la bõsqueda y organización de mangas. Los usuarios pueden personalizar la calidad y el formato de las descargas, así como gestionar su biblioteca de forma eficiente. Además, HaruNeko destaca por su capacidad de trabajar sin conexión, lo que permite leer contenido descargado sin necesidad de conexión a Internet. Con actualizaciones frecuentes y el apoyo de la comunidad, HaruNeko se ha convertido en una herramienta popular entre los amantes del manga que prefieren tener su colección de forma local y accesible en cualquier momento."""
            },
            {
                "name": "Suwayomi",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ch_downloaders", "suwayomi.png")),
                "rating": 10,
                "description": """Suwayomi es fork de código abierto basado en Tachiyomi, que ofrece una aplicación de lectura de manga y manhwa tanto para dispositivos Android como para PC. Se centra en proporcionar una experiencia personalizable y amigable para los usuarios, con una interfaz que permite modificaciones segõn preferencias individuales. Suwayomi incluye acceso a mõltiples fuentes de manga, lo que facilita la exploración de una amplia variedad de contenido, y ofrece herramientas para la gestión de bibliotecas, permitiendo a los usuarios organizar y seguir su progreso de lectura. Gracias a su naturaleza open source, la comunidad puede contribuir y mejorar la aplicación de manera constante, lo que enriquece la experiencia de lectura."""
            },
        ]
    }

    def get_user_data_dir(self) -> str:
        """Obtiene la ruta del directorio de datos del usuario."""
        return USER_DATA_DIR

    def get_tools_data_dir(self) -> str:
        """Obtiene la ruta del directorio de datos de herramientas."""
        return self.TOOLS_DATA_DIR
