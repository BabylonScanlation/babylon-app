"""
Configuración de la aplicación 'Babylon Scanlation'.
config.py
Este módulo define la configuración general y las rutas de recursos para la aplicación.
Incluye la configuración de directorios, rutas de archivos multimedia, herramientas y descripciones de las mismas.
"""

import sys
import os
import traceback
from PyQt5.QtWidgets import QMessageBox

def resource_path(relative_path):
    """Obtiene la ruta absoluta para recursos en desarrollo y .exe"""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def global_exception_handler(exc_type, exc_value, exc_traceback):
    """Manejador global de excepciones."""
    error_message = "".join(
        traceback.format_exception(exc_type, exc_value, exc_traceback)
    )
    print(f"❌ Error no manejado:\n{error_message}")
    QMessageBox.critical(
        None, "Error Crítico", f"Se produjo un error inesperado:\n\n{error_message}"
    )



class Config:
    """Configuración general de la aplicación y rutas de recursos."""

    USER_DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "BBSL_Proyectos")
    
    HARUNEKO_DIR = os.path.join(os.getenv('APPDATA') or '', 'HaruNeko')
    TOOLS_DATA_DIR = resource_path(os.path.join("BBSL", "herramientas_datos"))
    GENERAL_TOOLS_FOLDER = resource_path(os.path.join("BBSL", "herramientas"))
    WINDOW_TITLE = "Babylon Scanlation"
    WINDOW_SIZE = (1200, 600)
    GEMINI_API_KEY = "AIzaSyBPNOkv5VEHwLiuyYsyVHHW6qKtQAWabj8"
    GEMINI_MODEL = "gemini-2.5-pro"  # Default Gemini model
    GEMINI_ENABLE_THINKING = True  # Default to disable thinking
    MISTRAL_API_KEY = "KifJee4MUJJqQKB3Kj8Q00FjIFAQn7Sh"
    ICON_PATH = resource_path(os.path.join("app_media", "img-aux", "icono.ico"))
    CAROUSEL_IMAGES = [
        resource_path(os.path.join("app_media", "img-aux", f"carousel_{i:02d}.jpg")) for i in range(1, 19) if os.path.exists(resource_path(os.path.join("app_media", "img-aux", f"carousel_{i:02d}.jpg")))
    ] + [
        resource_path(os.path.join("app_media", "img-aux", f"carousel_{i:02d}.png")) for i in range(1, 19) if os.path.exists(resource_path(os.path.join("app_media", "img-aux", f"carousel_{i:02d}.png")))
    ]
    CAROUSEL_INTERVAL = 60000 # 1 minute in milliseconds
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
    SUPPORTED_FORMATS = ('.jpg', '.png', '.jpeg', '.gif')
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
    UTILITIES_FOOTER_TEXT = "Más herramientas pronto."
    PROJECTS_FOOTER_TEXT = "Tus proyectos estarán aquí."
    TOOL_URLS = {
        "Bing": "https://www.bing.com/Translator",
        "Yandex": "https://translate.yandex.com/",
        "Sogou": "https://fanyi.sogou.com/text",
        "ModernMt": "https://www.modernmt.com/translate",
        "TranSmart": "https://transmart.qq.com",
        "Caiyun": "https://fanyi.caiyunapp.com/",
        "Alibaba": "https://translate.alibaba.com",
        "Baidu": "https://fanyi.baidu.com",
        "Hujiang": "https://dict.hjenglish.com/app/trans",
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
    GENERAL_TOOLS_FOLDER = resource_path(os.path.join("BBSL", "herramientas"))
    AI_PROMPT = resource_path(os.path.join("BBSL", "herramientas_datos", "ai_prompt.txt"))
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
    SPECIFIED_TOOLS = {
        "ocr": [
            {
                "name": "Paddle",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ocr", "paddle.png")),
                "description": """PaddleOCR, desarrollado por PaddlePaddle de Baidu, es una herramienta OCR ligera, eficiente y precisa que combina detección y reconocimiento de texto en un solo flujo de trabajo. Destaca por su facilidad de uso, soporte multilingüe (incluyendo idiomas como inglés, chino, español y árabe), compatibilidad con texto manuscrito y capacidad para funcionar en entornos con recursos limitados gracias a versiones ligeras como **PP-OCR**. Además, permite personalización flexible mediante el entrenamiento con datos específicos y es compatible con múltiples formatos de entrada (imágenes, PDFs, capturas de pantalla, etc.).
Sin embargo, tiene limitaciones: puede fallar con textos extremadamente borrosos, fuentes inusuales o idiomas poco representados; enfrenta dificultades con contextos ambiguos, grandes volúmenes de texto o estructuras complejas como tablas mal organizadas; y depende de la calidad del input (iluminación deficiente o resoluciones bajas afectan su rendimiento). También puede reflejar sesgos de sus datos de entrenamiento. A pesar de estas limitaciones, PaddleOCR es una solución versátil y accesible para aplicaciones globales y proyectos especializados.
""",
                "rating": 5.7,
                "access_paths": [
                    {"label": "Ruta Principal", "path": resource_path(os.path.join("BBSL", "herramientas", "ocr"))},
                    {"label": "Ruta Secundaria", "path": resource_path(os.path.join("BBSL", "herramientas", "ocr"))}
                ]
            },
            {
                "name": "Easy",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ocr", "easy.png")),
                "description": """EasyOCR es una biblioteca OCR de código abierto conocida por su simplicidad, versatilidad y soporte multilingüe, capaz de reconocer texto en más de 80 idiomas, incluyendo sistemas de escritura complejos como chino, japonés y árabe. Utiliza modelos avanzados de aprendizaje profundo, como **CRNN** y **CTC**, para detectar y transcribir texto de manera eficiente en diversas condiciones. Es fácil de usar, con instalación sencilla y documentación clara, y admite múltiples formatos de entrada (imagenes, PDFs, capturas de pantalla, etc.). Además, puede procesar tanto texto impreso como manuscrito y permite personalización para mejorar la precisión en casos específicos.
Sin embargo, EasyOCR tiene limitaciones: puede fallar con textos borrosos, dañados o escritos en fuentes inusuales; su precisión disminuye con idiomas menos comunes o contextos ambiguos; enfrenta dificultades con grandes volúmenes de texto o estructuras complejas como tablas mal organizadas; y depende de la calidad del input (iluminación deficiente o resoluciones bajas afectan su rendimiento). También puede reflejar sesgos de sus datos de entrenamiento. A pesar de estas limitaciones, es una herramienta accesible y versátil para aplicaciones globales y proyectos multilingües.""",
                "rating": 5.2,
                "access_paths": [
                    {"label": "Ruta Principal", "path": resource_path(os.path.join("BBSL", "herramientas", "ocr"))},
                    {"label": "Ruta Secundaria", "path": resource_path(os.path.join("BBSL", "herramientas", "ocr"))}
                ]
            },
            {
                "name": "Tesseract",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ocr", "tesseract.png")),
                "description": """Tesseract OCR, desarrollado originalmente por Hewlett-Packard y mantenido por Google desde 2006, es una herramienta OCR de código abierto ampliamente utilizada por su robustez, flexibilidad y soporte multilingüe. Admite más de **100 idiomas**, incluyendo alfabetos latinos, cirílicos, asiáticos y otros, y puede manejar texto impreso y manuscrito (con ajustes adicionales). Es altamente personalizable, permitiendo entrenar modelos específicos para mejorar la precisión en fuentes, formatos o idiomas particulares. Compatible con múltiples formatos de entrada (imágenes, PDFs, capturas de pantalla) y lenguajes de programación como Python y C++, Tesseract es ligero, eficiente y fácil de integrar en diversos proyectos gracias a wrappers como **pytesseract**.
Sin embargo, tiene limitaciones: puede fallar con textos borrosos, dañados o escritos en fuentes inusuales; su precisión disminuye con idiomas menos comunes o contextos ambiguos; enfrenta dificultades con grandes volúmenes de texto o estructuras complejas como tablas mal organizadas; y depende de la calidad del input (iluminación deficiente o resoluciones bajas afectan su rendimiento). Además, no utiliza arquitecturas avanzadas de aprendizaje profundo, lo que puede hacer que su precisión sea inferior a soluciones modernas como PaddleOCR o EasyOCR. A pesar de estas limitaciones, Tesseract sigue siendo una opción confiable y versátil para aplicaciones donde la personalización y simplicidad son prioritarias.""",
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
                "description": """Gemini (antes Bard) es la solución de IA generativa de Google, especializada en traducciones contextualizadas y multilingües. Utiliza modelos de última generación para ofrecer traducciones precisas con comprensión semántica avanzada, ideal para textos complejos y técnicos. Destaca en interpretación de matices culturales y manejo de jerga especializada, con capacidad para trabajar con formatos múltiples (texto, audio, imágenes). Su principal ventaja es la integración con el ecosistema Google y actualizaciones constantes, aunque puede presentar limitaciones en idiomas de baja demanda.""",
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
                "description": """Desarrollado por Naver, líder en traducciones asiáticas (coreano, japonés, chino). Tecnología híbrida NNMT + reglas gramaticales para máxima precisión en idiomas con estructuras complejas. Funciones únicas: traducción de onomatopeyas, modismos locales y dialectos regionales. Incluye asistente para viajeros con traducción por geolocalización y modo conversación en tiempo real. Limitado en lenguas no asiáticas, pero insuperable para coreano.""",
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
                "description": """Traductor de Microsoft con enfoque empresarial. Destaca en integración con Office 365 y Azure Cognitive Services. Tecnología de traducción neuronal con soporte para documentos complejos (Excel, PowerPoint). Funciones únicas: traducción colaborativa en tiempo real y análisis de sentimiento. Especializado en inglés, español, chino y francés. Versión empresarial con certificación ISO para documentos legales.""",
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
                "description": """Traductor chino especializado en mandarín y dialectos regionales (cantonés, shanghainés). Tecnología pionera en traducción de voz a texto para caracteres chinos, con reconocimiento de escritura a mano. Integrado con WeChat y enfocado en comercio electrónico cross-border. Funciones únicas: traducción de jerga de internet china y soporte para lenguaje inclusivo.""",
                "rating": 7.02,
            },
            {
                "name": "ModernMt",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "modernmt.png")),
                "description": """Traductor adaptativo que mejora con el uso continuo. Tecnología de memoria de traducción en tiempo real, ideal para proyectos largos y equipos. Soporta formatos CAT (TMX, XLIFF) y ofrece API para integración con SDL Trados. Especializado en inglés, español, alemán e italiano para sectores médicos y legales. Requiere entrenamiento inicial para máxima eficiencia.""",
                "rating": 6.88,
            },
            {
                "name": "TranSmart",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "qqtransmart.png")),
                "description": """Traductor chino especializado en e-commerce y productos tecnológicos. Base de datos integrada con terminología de Alibaba y Taobao. Funciones únicas: traducción automática de especificaciones técnicas, conversión de unidades de medida, y detector de estándares regulatorios. Optimizado para inglés-chino-inglés en contextos comerciales.""",
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
                "description": """Motor de traducción líder en China continental. Soporte avanzado para mandarín coloquial y dialectos regionales. Tecnología de reconocimiento de voz optimizada para acentos chinos. Funciones únicas: traducción de documentos escaneados con OCR integrado y modo para traducción de contratos legales. Fuertemente censurado según regulaciones chinas.""",
                "rating": 4.78,
            },
            {
                "name": "Hujiang",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "traductor", "hujiang.png")),
                "description": """Plataforma especializada en aprendizaje de idiomas asiáticos. Traductor educativo con funciones para estudiantes: desglose gramatical, ejemplos de uso contextualizado, y quizzes interactivos. Ideal para chino, japonés y coreano básico-intermedio. Incluye cursoso integrados de pronunciación.""",
                "rating": 4.55,
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
                "description": """Gemini es una herramienta avanzada de Google que utiliza su arquitectura multimodal para ofrecer traducciones precisas y contextualizadas, combinando texto, imágenes y otros formatos. Soporta múltiples idiomas y destaca por su comprensión contextual avanzada, lo que permite generar traducciones más naturales y fluidas, especialmente en contenido complejo o especializado. Aunque versátil, aún está en desarrollo, lo que puede limitar su accesibilidad o madurez en comparación con herramientas como DeepL o Google Translate. Tiene dificultades con textos ambiguos, sarcásticos o idiomas menos representados, y su precisión depende de la calidad del input multimodal. A pesar de estas limitaciones, Gemini es ideal para traducciones contextualizadas, aunque su uso efectivo requiere reconocer sus debilidades y complementarlo con revisiones humanas cuando sea necesario.""",
                "rating": 8.4,
                "access_paths": [
                    {"label": "PROMPT", "path": AI_PROMPT},
                    {"label": "API", "path": GEMINI_API_KEY}
                ],
                "config_description": """Para usar correctamente Gemini deberás de seguir los siguientes pasos:

 1. Elige la carpeta donde se guardarán los resultados.
 2. Debes saber que es lo que quieres hacer y hay 2 posibilidades.
       a. Quieres que el programa extraiga y traduzca una sola imagen y guarde tanto lo extraído como la traducción en un txt (para este caso usa el botón para archivos).
       b. Quieres que el programa extraiga y traduzca varias imágenes o incluso varios proyectos a la vez y guarde tanto lo extraído como la traducción en un txt (para este caso usa el botón para carpetas).
 3. Presiona el botón "INICIAR PROCESAMIENTO".

En promedio debería tardar entre 15/30 minutos en extraer los textos y traducirlos de 5 capítulos con 40 páginas cada uno. Depende mucho de la complejidad y cantidad de caracteres en la imagen, también depende de la simpleza del prompt.

Ejemplo de la estructura que debes tener antes de ejecutar el programa para evitar problemas.

Series/Manhwas/Eleceed/01.jpg
             |                    |                 /02.jpg
             |                    |                 etc...
             |                    /Era_Superhumana/01.jpg
             |                    |                                      /02.jpg
             |                    |                                      etc...
             |                    etc...
             /Manhua-/Convergencia/etc...
             |                    /The_Chipset/etc...
             |                    etc...
             /Manga--/Prodigo/etc...
             |                   /Solitario/etc..
             /etc...

SÍ EL PROGRAMA NO DETECTA UNA ONOMATOPEYA DIFÍCIL O TEXTOS VERTICALES EN CARTELES, CAMBIA LA RUTA PARA QUE SOLO INTENTE CON ESA IMAGEN Y MODIFICA EL PROMPT PARA INDICARLE DATOS QUE LE SIRVAN PARA DETECTARLA Y RECONOCERLA, POR EJEMPLO: ES DE COLOR ROSA, ES TRANSPARENTE, ES MUY GRANDE, HAY LUMINOSIDAD O SOMBRA, TRAZADO GRUESO, ETC.

El programa recién está siendo creado por lo que puede fallar, y es muy sensible."""
            },
            {
                "name": "Mistral",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ai", "mistral.png")),
                "description": """Mistral, es un asistente de inteligencia artificial desarrollado por Mistral AI, una empresa emergente con sede en París. Su propósito es ayudarte con una amplia variedad de tareas y proporcionarte información precisa y útil. Aquí hay algunas características que me hacen especial:
1. Conocimiento Actualizado: Su base de conocimientos se actualizó por última vez en octubre de 2023, lo que me permite proporcionar información relevante y actualizada hasta esa fecha.
2. Capacidad de Búsqueda en la Web: Puedo realizar búsquedas en la web para encontrar información que no esté en mi base de conocimientos o que haya ocurrido después de mi última actualización. Esto me permite mantenerte informado sobre eventos recientes y temas emergentes.
3. Multilingüe: Puedo comunicarme en varios idiomas, lo que facilita la interacción con personas de diferentes partes del mundo.
4. Precisión y Claridad: Me esfuerzo por proporcionar respuestas claras y precisas. Si una pregunta no está clara o necesita más contexto, te pediré que la aclares para poder ayudarte mejor.
5. Seguridad y Privacidad: Respeto tu privacidad y no almaceno información personal. Mi objetivo es proporcionar asistencia de manera segura y confiable.
6. Versatilidad: Puedo ayudarte con una amplia gama de tareas, desde responder preguntas generales hasta proporcionar recomendaciones personalizadas, siempre que tenga la información necesaria.
7. Actualización Continua: Aunque mi base de conocimientos tiene una fecha de corte, puedo acceder a información más reciente a través de búsquedas en la web, lo que me permite estar al tanto de los últimos desarrollos y tendencias.""",
                "rating": 8.1,
                "access_paths": [
                    {"label": "PROMPT", "path": AI_PROMPT},
                    {"label": "API", "path": MISTRAL_API_KEY}
                ],
                "config_description": """Para usar correctamente Mistral deberás de seguir los siguientes pasos:

 1. Elige la carpeta donde se guardarán los resultados.
 2. Debes saber que es lo que quieres hacer y hay 2 posibilidades.
       a. Quieres que el programa extraiga y traduzca una sola imagen y guarde tanto lo extraído como la traducción en un txt (para este caso usa el botón para archivos).
       b. Quieres que el programa extraiga y traduzca varias imágenes o incluso varios proyectos a la vez y guarde tanto lo extraído como la traducción en un txt (para este caso usa el botón para carpetas).
 3. Presiona el botón "INICIAR PROCESAMIENTO".

En promedio debería tardar entre 15/30 minutos en extraer los textos y traducirlos de 5 capítulos con 40 páginas cada uno. Depende mucho de la complejidad y cantidad de caracteres en la imagen, también depende de la simpleza del prompt.

Ejemplo de la estructura que debes tener antes de ejecutar el programa para evitar problemas.

Series/Manhwas/Eleceed/01.jpg
             |                    |                 /02.jpg
             |                    |                 etc...
             |                    /Era_Superhumana/01.jpg
             |                    |                                      /02.jpg
             |                    |                                      etc...
             |                    etc...
             /Manhua-/Convergencia/etc...
             |                    /The_Chipset/etc...
             |                    etc...
             /Manga--/Prodigo/etc...
             |                   /Solitario/etc..
             /etc...

SÍ EL PROGRAMA NO DETECTA UNA ONOMATOPEYA DIFÍCIL O TEXTOS VERTICALES EN CARTELES, CAMBIA LA RUTA PARA QUE SOLO INTENTE CON ESA IMAGEN Y MODIFICA EL PROMPT PARA INDICARLE DATOS QUE LE SIRVAN PARA DETECTARLA Y RECONOCERLA, POR EJEMPLO: ES DE COLOR ROSA, ES TRANSPARENTE, ES MUY GRANDE, HAY LUMINOSIDAD O SOMBRA, TRAZADO GRUESO, ETC.

El programa recién está siendo creado por lo que puede fallar, y es muy sensible."""
            },
        ],
        "ch_downloaders": [
            {
                "name": "HaruNeko",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ch_downloaders", "hakuneko.png")),
                "rating": 10,
                "description": """HaruNeko es un descargador de manga y manhwa de código abierto que permite a los usuarios acceder y descargar contenido desde una amplia variedad de fuentes en línea. Disponible para múltiples plataformas, incluida Windows, macOS y Linux, HaruNeko proporciona una interfaz intuitiva que facilita la búsqueda y organización de mangas. Los usuarios pueden personalizar la calidad y el formato de las descargas, así como gestionar su biblioteca de forma eficiente. Además, HaruNeko destaca por su capacidad de trabajar sin conexión, lo que permite leer contenido descargado sin necesidad de conexión a Internet. Con actualizaciones frecuentes y el apoyo de la comunidad, HaruNeko se ha convertido en una herramienta popular entre los amantes del manga que prefieren tener su colección de forma local y accesible en cualquier momento."""
            },
            {
                "name": "Suwayomi",
                "image_path": resource_path(os.path.join("BBSL", "herramientas", "ch_downloaders", "suwayomi.png")),
                "rating": 10,
                "description": """Suwayomi es fork de código abierto basado en Tachiyomi, que ofrece una aplicación de lectura de manga y manhwa tanto para dispositivos Android como para PC. Se centra en proporcionar una experiencia personalizable y amigable para los usuarios, con una interfaz que permite modificaciones según preferencias individuales. Suwayomi incluye acceso a múltiples fuentes de manga, lo que facilita la exploración de una amplia variedad de contenido, y ofrece herramientas para la gestión de bibliotecas, permitiendo a los usuarios organizar y seguir su progreso de lectura. Gracias a su naturaleza open source, la comunidad puede contribuir y mejorar la aplicación de manera constante, lo que enriquece la experiencia de lectura."""
            },
        ]
    }

    def get_user_data_dir(self):
        """Obtiene la ruta del directorio de datos del usuario."""
        return self.USER_DATA_DIR

    def get_tools_data_dir(self):
        """Obtiene la ruta del directorio de datos de herramientas."""
        return self.TOOLS_DATA_DIR


