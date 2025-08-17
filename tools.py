"""
Módulo 'tools.py' dónde esta la lógica que permite cargar los datos de las herramientas.
"""

# bibliotecas nativas
import os
import shutil
import subprocess
import threading
import time
import webbrowser
import traceback

# bibliotecas no nativas
from PIL import Image

# módulos (no tocar)
from app_tools import gemini, mistral, translatorz
from app_tools.haruneko import DownloadThread, HaruNekoManager
from config import Config

# bibliotecas no nativas
# pylint: disable=no-name-in-module
from PyQt5.QtCore import (
    QDir,
    QObject,
    QRunnable,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def resize_image(image_path, width, height):
    """Redimensiona una imagen a las dimensiones especificadas sin perder calidad."""
    try:
        with Image.open(image_path) as img:
            img = img.resize((width, height), Image.LANCZOS)
            img.save(image_path)
    except Exception as e:
        print(f"Error al redimensionar la imagen {image_path}: {e}")


# pylint: disable=too-few-public-methods
class TranslationTask(QRunnable):
    """Maneja tareas de traducción en hilos en segundo plano."""

    def __init__(self, tool, text, source_lang, target_lang):
        super().__init__()
        self.tool = tool
        self.text = text
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.signals = TranslationSignals()

    def run(self):
        """Ejecuta la tarea de traducción en un hilo en segundo plano."""
        try:
            if self.tool["name"] == "Baidu":
                while True:
                    try:
                        translated_text_raw = translatorz.translatorz(
                            self.tool["name"], self.text, self.source_lang, self.target_lang
                        )
                        translated_text = str(translated_text_raw) # Ensure it's a string
                        
                        if "Error en Baidu: Función no certificada o inestable." in translated_text:
                            # Este error específico, reintentar silenciosamente
                            time.sleep(1) # Pequeña pausa antes de reintentar
                            continue # Ir a la siguiente iteración del bucle
                        else:
                            # Éxito o un error diferente, emitir y salir del bucle
                            self.signals.finished.emit(self.tool["name"], translated_text, "")
                            break
                    except Exception as e: # pylint: disable=broad-exception-caught
                        # Capturar cualquier excepción inesperada durante la llamada a Baidu
                        error_msg = f"{str(e)}\n\n{traceback.format_exc()}"
                        self.signals.finished.emit(self.tool["name"], "", error_msg)
                        break # Salir del bucle en caso de error crítico
            else:
                # Lógica existente para otros traductores
                translated_text_raw = translatorz.translatorz(
                    self.tool["name"], self.text, self.source_lang, self.target_lang
                )
                translated_text = str(translated_text_raw) # Ensure it's a string

                if "Idioma escogido a traducir incompatible." in translated_text:
                    self.signals.finished.emit(self.tool["name"], "", translated_text)
                else:
                    self.signals.finished.emit(self.tool["name"], translated_text, "")
        except Exception as e: # pylint: disable=broad-exception-caught
            error_msg = f"{str(e)}\n\n{traceback.format_exc()}"
            self.signals.finished.emit(self.tool["name"], "", error_msg)
            self.signals.error_occurred.emit(self.tool["name"], error_msg)


# pylint: disable=too-few-public-methods
class TranslationSignals(QObject):
    """Define señales para comunicación entre hilos y la GUI."""
    finished = pyqtSignal(str, str, str)
    error_occurred = pyqtSignal(str, str)


# pylint: disable=too-many-instance-attributes, too-many-lines
class ToolsManager(QObject):
    """Gestiona la creación y configuración de herramientas de la aplicación."""
    processing_finished = pyqtSignal(bool)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.processing_finished.connect(self._show_completion_message)
        self.utilities_area = None
        self.parent_container = None
        self.details_container = None
        self.gemini_container = None
        self.mistral_container = None
        self.haruneko_manager = HaruNekoManager(self.app)
        self.input_path = None
        self.output_directory = None
        self.image_to_category = None
        self.cancel_event = None
        self.download_state = "idle"
        self.processing_thread = None
        self.active_threads = []
        self.haruneko_installed = False
        self.haruneko_version = None
        self.install_button = None
        self.download_in_progress = False
        self.download_thread = None
        self.show_ai_tools = False
        self.toggle_ai_button = None
        self.source_combo = None
        self.target_combo = None

    def create_utilities_area(self):
        """Crea el área de herramientas."""
        scroll_area = self._create_scroll_area("utilities")
        self.image_to_category = {
            0: "ocr",
            1: "traductor",
            2: "ai",
            3: "ch_downloaders",
        }
        scroll_content = scroll_area.widget()
        if scroll_content and scroll_content.layout():
            for i in range(scroll_content.layout().count()):
                widget = scroll_content.layout().itemAt(i).widget()
                if widget:
                    category = self.image_to_category.get(i)
                    if category:
                        widget.mousePressEvent = (
                            lambda _, cat=category: self.show_tool_details(cat)
                        )
        self.utilities_area = {
            "scroll": scroll_area,
            "footer": self._create_footer_text("utilities"),
        }
        return self.utilities_area

    def show_tool_details(self, category):
        """Muestra los detalles de las herramientas específicas para una categoría."""
        if self.toggle_ai_button:
            self.toggle_ai_button.hide()
        if self.source_combo:
            self.source_combo.hide()
        if self.target_combo:
            self.target_combo.hide()
        self.utilities_area["scroll"].hide()
        self.utilities_area["footer"].hide()
        if self.parent_container:
            self.parent_container.deleteLater()
            self.parent_container = None
        self.parent_container = QWidget(self.app.content_container)
        self.parent_container.setGeometry(50, 50, 780, 500)
        self.parent_container.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 40);
            border: 1px solid rgba(87, 35, 100, 180);
            """
        )
        if category == "traductor":
            if not self.toggle_ai_button:
                self.toggle_ai_button = QPushButton(
                    "Mostrar IAs", self.app.content_container
                )
                self.toggle_ai_button.setStyleSheet(
                    """
                    QPushButton {
                        font-size: 15px;
                        color: white;
                        background-color: #555555;
                        border: none;
                        padding: 5px;
                    }
                    QPushButton:hover {
                        background-color: #888888;
                    }
                    """
                )
                self.toggle_ai_button.setFont(self.app.adventure_font)
                self.toggle_ai_button.setCursor(Qt.PointingHandCursor)
                self.toggle_ai_button.clicked.connect(self.toggle_ai_tools)
                self.toggle_ai_button.setGeometry(50, 7, 120, 35)
            if not self.source_combo:
                self.source_combo = QComboBox(self.app.content_container)
                langs_origen = [
                    ("Auto", "auto"),
                    ("Chino Tradicional", "zh-TW"),
                    ("Chino Simplificado", "zh-CN"),
                    ("Coreano", "ko"),
                    ("Japonés", "ja"),
                    ("Inglés", "en"),
                    ("Español", "es"),
                ]
                for lang_name, lang_code in langs_origen:
                    self.source_combo.addItem(lang_name, lang_code)
            if not self.target_combo:
                self.target_combo = QComboBox(self.app.content_container)
                langs_destino = [
                    ("Chino Tradicional", "zh-TW"),
                    ("Chino Simplificado", "zh-CN"),
                    ("Coreano", "ko"),
                    ("Japonés", "ja"),
                    ("Inglés", "en"),
                    ("Español", "es"),
                ]
                for lang_name, lang_code in langs_destino:
                    self.target_combo.addItem(lang_name, lang_code)
                combo_style = """
                    QComboBox {
                        background-color: rgba(0,0,0,150);
                        color: white;
                        border: 2px solid #572364;
                        padding: 3px;
                        min-width: 100px;
                    }
                    QComboBox QAbstractItemView {
                        background-color: #2a2a2a;
                        color: white;
                        selection-background-color: #572364;
                    }
                """
                self.source_combo.setStyleSheet(combo_style)
                self.target_combo.setStyleSheet(combo_style)
                self.source_combo.setGeometry(180, 7, 150, 35)
                self.target_combo.setGeometry(340, 7, 150, 35)
                self.target_combo.setCurrentIndex(5)
            if not hasattr(self, "custom_text_input"):
                self.custom_text_input = QLineEdit(self.app.content_container)
                self.custom_text_input.setPlaceholderText("Introduce texto aquí...")
                self.custom_text_input.setStyleSheet(
                    """
                    QLineEdit {
                        font-size: 14px;
                        color: white;
                        background-color: rgba(0, 0, 0, 150);
                        border: 1px solid rgba(87, 35, 100, 180);
                        padding-left: 5px;
                    }
                    QLineEdit::placeholder {
                        color: rgba(255, 255, 255, 150);
                    }
                """
                )
                self.custom_text_input.setFixedSize(280, 35)
                self.custom_text_input.setFont(self.app.roboto_black_font)
            if not hasattr(self, "use_custom_button"):
                self.use_custom_button = QPushButton("USAR", self.app.content_container)
                self.use_custom_button.setStyleSheet(
                    """
                    QPushButton {
                        font-size: 14px;
                        color: white;
                        background-color: #555555;
                        border: none;
                        padding: 5px;
                    }
                    QPushButton:hover {
                        background-color: #888888;
                    }
                """
                )
                self.use_custom_button.setFixedSize(80, 35)
                self.use_custom_button.setFont(self.app.adventure_font)
                self.use_custom_button.setCursor(Qt.PointingHandCursor)
                self.use_custom_button.clicked.connect(
                    lambda: self.ejecutar_traducciones_globales()
                )
            if not hasattr(self, "translator_warning_label"):
                self.translator_warning_label = QLabel(self.app.content_container)
                self.translator_warning_label.setText(
                    "Algunos traductores pueden presentar errores de servidores caídos, "
                    "o no ser compatibles entre los idiomas escogidos para traducir directamente "
                    "o presentar otro error, vuelva a intentarlo luego."
                )
                self.translator_warning_label.setStyleSheet(
                    """
                    QLabel {
                        font-size: 14px;
                        color: #7f7f7f;
                        background-color: rgba(0, 0, 0, 150);
                        border: 1px solid #572364;
                        padding: 2px;
                    }
                """
                )
                self.translator_warning_label.setFont(self.app.roboto_black_font)
                self.translator_warning_label.setWordWrap(True)
                self.translator_warning_label.setFixedSize(780, 40)
                self.translator_warning_label.setAlignment(Qt.AlignCenter)
                self.translator_warning_label.move(50, 555)
                self.translator_warning_label.show()
            else:
                if hasattr(self, "translator_warning_label"):
                    self.translator_warning_label.hide()
                self.source_combo.setGeometry(180, 7, 130, 35)
                self.target_combo.setGeometry(320, 7, 130, 35)
                self.custom_text_input.setGeometry(460, 7, 150, 35)
                self.use_custom_button.setGeometry(750, 7, 80, 35)
                self.toggle_ai_button.show()
                self.source_combo.show()
                self.target_combo.show()
                self.custom_text_input.show()
                self.use_custom_button.show()
            self.source_combo.setGeometry(180, 7, 130, 35)
            self.target_combo.setGeometry(320, 7, 130, 35)
            self.custom_text_input.setGeometry(460, 7, 150, 35)
            self.use_custom_button.setGeometry(750, 7, 80, 35)
            self.toggle_ai_button.show()
            self.source_combo.show()
            self.target_combo.show()
            self.custom_text_input.show()
            self.use_custom_button.show()
            self.translator_warning_label.show()
        scroll_area = QScrollArea(self.parent_container)
        scroll_area.setWidgetResizable(True)
        scroll_area.setGeometry(0, 0, 780, 500)
        details_container = QWidget()
        details_layout = QVBoxLayout(details_container)
        details_layout.setContentsMargins(10, 10, 10, 10)
        details_layout.setSpacing(10)
        tools = Config.SPECIFIED_TOOLS.get(category, [])
        for tool in tools:
            if (
                category == "traductor"
                and tool["name"] in ["Gemini", "Mistral"]
                and not self.show_ai_tools
            ):
                continue
            tool_container = self._create_tool_container(tool, category)
            details_layout.addWidget(tool_container, alignment=Qt.AlignTop)
        scroll_area.setWidget(details_container)
        self.parent_container.show()

    def ejecutar_traducciones_globales(self):
        texto = self.custom_text_input.text().strip()
        if not texto:
            return
        self.use_custom_button.setEnabled(False)
        self.use_custom_button.setText("Traduciendo")
        QApplication.processEvents()
        scroll_area = self.parent_container.findChild(QScrollArea)
        if not scroll_area:
            return
        details_container = scroll_area.widget()
        tool_containers = [
            details_container.layout().itemAt(i).widget()
            for i in range(details_container.layout().count())
            if details_container.layout().itemAt(i).widget().isVisible()
        ]
        tool_containers.reverse()
        self.active_global_threads = len(tool_containers)
        for container in tool_containers:
            input_field = container.findChild(QLineEdit)
            output_field = container.findChild(QTextEdit)
            use_button = container.findChild(QPushButton)
            if not all([input_field, output_field, use_button]):
                continue
            input_field.setText(texto)
            output_field.clear()
            QApplication.processEvents()
            task = self._translate_text(input_field, output_field, container.tool)
            task.signals.finished.connect(self._handle_global_translation_finish)
            task.signals.error_occurred.connect(self._handle_global_translation_error)

    def _handle_global_translation_finish(self, name, result, error):
        self.active_global_threads -= 1
        if self.active_global_threads <= 0:
            self.use_custom_button.setEnabled(True)
            self.use_custom_button.setText("USAR")

    def _handle_global_translation_error(self, tool_name, error_msg):
        self.active_global_threads -= 1
        if self.active_global_threads <= 0:
            self.use_custom_button.setEnabled(True)
            self.use_custom_button.setText("USAR")
            QApplication.processEvents()

    def toggle_ai_tools(self):
        """Alterna la visibilidad de las herramientas de IA en la categoría 'traductor'."""
        self.show_ai_tools = not self.show_ai_tools
        if self.parent_container:
            self.parent_container.deleteLater()
            self.parent_container = None
        self.show_tool_details("traductor")
        self.toggle_ai_button.setText(
            "Ocultar IAs" if self.show_ai_tools else "Mostrar IAs"
        )

    def _create_tool_container(self, tool, category):
        """Crea un contenedor individual para una herramienta."""
        tool_container = QWidget()
        tool_container.setFixedSize(740, 220)
        tool_container.tool = tool
        tool_layout = QHBoxLayout(tool_container)
        tool_layout.setContentsMargins(9, 9, 9, 9)
        tool_layout.setSpacing(10)
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(9, 9, 9, 9)
        left_layout.setSpacing(10)
        description_scroll_area = QScrollArea()
        description_scroll_area.setWidgetResizable(True)
        description_scroll_area.setStyleSheet(
            """
        QScrollArea {
        background: transparent;
        border: none;
        }
        QScrollBar:vertical {
        background: rgba(0, 0, 0, 30);
        border-radius: 2px;
        width: 10px;
        }
        QScrollBar::handle:vertical {
        background: rgba(0, 0, 0, 100);
        border-radius: 2px;
        }
        """
        )
        description_label = QLabel(tool["description"])
        description_label.setStyleSheet(
            """
        font-size: 14px;
        color: white;
        background: transparent;
        qproperty-alignment: AlignLeft;
        padding: 5px;
        """
        )
        description_label.setFont(self.app.roboto_black_font)
        description_label.setWordWrap(True)
        description_scroll_area.setWidget(description_label)
        if category == "ai":
            description_scroll_area.setFixedHeight(70)
        elif category == "traductor":
            description_scroll_area.setFixedHeight(70)
        else:
            description_scroll_area.setFixedHeight(184)

        left_layout.addWidget(description_scroll_area)
        if category == "ai":
            routes_container = QWidget()
            routes_layout = QVBoxLayout(routes_container)
            routes_layout.setContentsMargins(10, 10, 10, 10)
            routes_layout.setSpacing(5)
            config_label = QLabel("CONFIGURACIÓN PREVIA:")
            config_label.setFont(self.app.roboto_black_font)
            config_label.setStyleSheet(
                """
            font-size: 14px;
            color: white;
            background: transparent;
            border: none;
            padding: 0px;
            """
            )
            config_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            routes_layout.addWidget(config_label)
            access_paths = tool.get("access_paths", [])
            for idx, path_info in enumerate(access_paths):
                route_layout = QHBoxLayout()
                route_layout.setContentsMargins(0, 5, 0, 5)
                route_layout.setSpacing(5)
                label_text = ""
                if tool["name"] in ["Gemini", "Mistral"]:
                    label_text = "PROMPT:" if idx == 0 else "API:"
                label = QLabel(label_text)
                label.setFont(self.app.roboto_black_font)
                label.setStyleSheet(
                    """
                font-size: 14px;
                color: white;
                background: transparent;
                border: none;
                padding: 0px;
                """
                )
                route_layout.addWidget(label)
                route_input = QLineEdit(path_info["path"])
                route_input.setReadOnly(True)
                route_input.setStyleSheet(
                    """
                QLineEdit {
                font-size: 12px;
                color: black;
                background-color: white;
                border: none;
                border-radius: 2px;
                padding: 0px;
                }
                """
                )
                route_input.setFont(self.app.roboto_black_font)
                route_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                route_layout.addWidget(route_input)
                if label_text != "API:":
                    browse_button = QPushButton("Examinar")
                    browse_button.setFixedWidth(80)
                    browse_button.clicked.connect(
                        lambda _, input_box=route_input: self.open_path_for_prompt(
                            input_box
                        )
                    )
                    browse_button.setStyleSheet(
                        """
                        QPushButton {
                            font-size: 12px;
                            color: white;
                            background-color: #555555;
                            border: none;
                            padding: 0px;
                        }
                        QPushButton:hover {
                            background-color: #888888;
                        }
                    """
                    )
                    browse_button.setFont(self.app.adventure_font)
                    browse_button.setCursor(Qt.PointingHandCursor)
                    route_layout.addWidget(browse_button)
                routes_layout.addLayout(route_layout)
            left_layout.addWidget(routes_container)
        if category == "traductor":
            input_container = QLineEdit()
            input_container.setPlaceholderText("Introduce el texto a traducir...")
            input_container.setStyleSheet(
                """
                QLineEdit {
                    font-size: 14px;
                    color: white;
                    background-color: rgba(0, 0, 0, 50);
                    border: 1px solid rgba(87, 35, 100, 180);
                    margin-top: 10px;
                    qproperty-alignment: AlignLeft;
                    padding-left: 5px;
                }
                QLineEdit::placeholder {
                    color: rgba(255, 255, 255, 150);
                }
            """
            )
            input_container.setFont(self.app.roboto_black_font)
            input_container.setFixedHeight(55)
            input_container.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            left_layout.addWidget(input_container)
            output_container = QTextEdit()
            output_container.setReadOnly(True)
            output_container.setPlaceholderText("La traducción aparecerá aquí...")
            output_container.setStyleSheet(
                """
                QTextEdit {
                    font-size: 14px;
                    color: white;
                    background-color: rgba(0, 0, 0, 50);
                    border: 1px solid rgba(87, 35, 100, 180);
                    padding: 5px;
                }
                QTextEdit::placeholder {
                    color: rgba(255, 255, 255, 150);
                }
            """
            )
            output_container.setFont(self.app.roboto_black_font)
            output_container.setFixedHeight(45)
            output_container.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            left_layout.addWidget(output_container)
        tool_layout.addWidget(left_container)
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(5)
        right_layout.setAlignment(Qt.AlignCenter)
        rating_label = QLabel(f"{tool['rating']}")
        rating_label.setFixedSize(100, 25)
        rating = tool["rating"]
        if 9 <= rating <= 10:
            bg_color = "rgba(0, 128, 0, 150)"
        elif 7 <= rating < 9:
            bg_color = "rgba(144, 238, 144, 150)"
        elif 5 <= rating < 7:
            bg_color = "rgba(255, 215, 0, 150)"
        elif 3 <= rating < 5:
            bg_color = "rgba(255, 165, 0, 150)"
        else:
            bg_color = "rgba(255, 0, 0, 150)"
        rating_label.setStyleSheet(
            f"""
        background-color: {bg_color};
        border: 1px solid rgba(150, 0, 150, 180);
        border-radius: 2px;
        color: white;
        qproperty-alignment: AlignCenter;
        """
        )
        rating_label.setFont(self.app.roboto_black_font)
        right_layout.addWidget(rating_label, alignment=Qt.AlignCenter)
        name_label = QLabel(tool["name"])
        name_label.setFixedWidth(100)
        name_label.setWordWrap(True)
        name_label.setStyleSheet(
            """
        font-size: 16px;
        color: white;
        background: transparent;
        qproperty-alignment: AlignCenter;
        padding: 1px;
        """
        )
        name_label.setFont(self.app.super_cartoon_font)
        right_layout.addWidget(name_label)

        image_label = QLabel()
        image_label.setFixedSize(80, 80)
        image_label.setAlignment(Qt.AlignCenter)
        image_label.setStyleSheet(
            """
        background-color: rgba(0, 0, 0, 50);
        border: 1px solid rgba(150, 0, 150, 180);
        border-radius: 2px;
        padding: 5px;
        """
        )
        pixmap = QPixmap(tool["image_path"])
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                70, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            image_label.setPixmap(scaled_pixmap)
        else:
            image_label.setText("Imagen no disponible")

        def open_tool_site(_event):
            tool_name = tool["name"]
            if tool_name in Config.TOOL_URLS:
                webbrowser.open(Config.TOOL_URLS[tool_name])
            elif f"{tool_name} (OCR)" in Config.TOOL_URLS:
                webbrowser.open(Config.TOOL_URLS[f"{tool_name} (OCR)"])

        image_label.mousePressEvent = open_tool_site
        image_label.setCursor(Qt.PointingHandCursor)
        right_layout.addWidget(image_label, alignment=Qt.AlignCenter)
        install_button = QPushButton(
            "Descargar" if tool["name"] == "HaruNeko" else "Instalar"
        )
        install_button.setStyleSheet(
            """
        QPushButton {
            font-size: 14px;
            color: white;
            background-color: #555555;
            border: none;
            padding: 1px;
        }
        QPushButton:hover {
            background-color: #888888;
        }
        QPushButton:disabled {
            background-color: #333333;
            color: #888888;
        }
        """
        )
        install_button.setFont(self.app.adventure_font)
        install_button.setCursor(Qt.PointingHandCursor)
        if category == "traductor" and tool["name"] != "Meta":
            install_button.setEnabled(False)
        if tool["name"] == "HaruNeko":
            self.install_button = install_button
            if self.download_state == "downloading":
                self.install_button.setText("Descargando...")
                self.install_button.setEnabled(False)
            elif os.path.exists(os.path.join(Config.HARUNEKO_DIR, "version.txt")):
                self.haruneko_installed = True
                self.haruneko_version = self.haruneko_manager.get_current_version()
                self.install_button.setText("Eliminar")
                self.install_button.clicked.connect(self.uninstall_hakuneko)
            else:
                self.install_button.clicked.connect(self.download_hakuneko)
            self.check_for_updates()
        elif tool["name"] in ["Gemini", "Mistral"]:
            install_button.setEnabled(False)
        right_layout.addWidget(install_button)
        use_button = QPushButton("Usar")
        use_button.setObjectName(f"use_btn_{tool['name']}")
        use_button.setStyleSheet(
            """
        QPushButton {
        font-size: 14px;
        color: white;
        background-color: #555555;
        border: none;
        padding: 1px;
        }
        QPushButton:hover {
        background-color: #888888;
        }
        """
        )
        use_button.setFont(self.app.adventure_font)
        use_button.setCursor(Qt.PointingHandCursor)
        if tool["name"] == "Gemini" and category == "ai":
            use_button.clicked.connect(
                lambda _, cat=category: self._create_gemini_container(cat)
            )
        elif tool["name"] == "Mistral" and category == "ai":
            use_button.clicked.connect(
                lambda _, cat=category: self._create_mistral_container(cat)
            )
        elif tool["name"] == "HaruNeko":
            use_button.clicked.connect(self.start_hakuneko)
        elif category == "traductor":
            input_field = tool_container.findChild(QLineEdit)
            output_field = tool_container.findChild(QTextEdit)
            use_button.clicked.connect(
                lambda: self._translate_text(input_field, output_field, tool)
            )
        right_layout.addWidget(use_button)
        tool_layout.addWidget(right_container)
        return tool_container

    def _translate_text(self, input_container, output_container, tool):
        """Traducción al texto usando todos los traductores.f"""
        try:
            input_text = input_container.text()
            if not input_text.strip():
                return
            use_button = (
                input_container.parent()
                .parent()
                .findChild(QPushButton, f"use_btn_{tool['name']}")
            )
            if use_button:
                use_button.setEnabled(False)
                use_button.setText("Traduciendo...")
            output_container.clear()
            QApplication.processEvents()
            task = TranslationTask(
                tool,
                input_text,
                self.source_combo.currentData(),
                self.target_combo.currentData(),
            )
            task.signals.finished.connect(
                lambda name, result, error: self._handle_translation_result(
                    name, result, error, output_container, use_button
                )
            )
            task.signals.error_occurred.connect(self._handle_critical_error)
            QThreadPool.globalInstance().start(task)
            return task
        except Exception as e:
            print(f"Error en _translate_text: {str(e)}")
            if use_button:
                use_button.setEnabled(True)
                use_button.setText("Usar")
            return None

    def _handle_translation_result(
            self, tool_name, result, error, output_container, use_button
    ):
        try:
            use_button.setEnabled(True)
            use_button.setText("Usar")
            if error:
                if "Idioma escogido a traducir incompatible." in error or "Error en Baidu: Función no certificada o inestable." in error:
                    output_container.setStyleSheet("color: red;")
                    output_container.setText(error)
                elif tool_name == "iTranslate" and "503" in error:
                    error_msg = (
                        f"⚠ Error en iTranslate: Servicio no disponible\n"
                        f"(El servidor está temporalmente fuera de línea)"
                    )
                    output_container.setText(error_msg)
                else:
                    output_container.setText(f"🚫 Error en {tool_name}: {error}")
            else:
                output_container.setStyleSheet("color: white;")
                output_container.setText(result)

            self.active_threads = [t for t in self.active_threads if t.isRunning()]
        except Exception as e:
            print(f"Error en _handle_translation_result: {str(e)}")
            use_button.setEnabled(True)
            use_button.setText("Usar")
            output_container.setText(f"Error al mostrar resultados: {str(e)}")

    def _handle_critical_error(self, tool_name, error_msg):
        """Maneja errores que podrían cerrar la aplicación"""
        print(f"\n🚨 ERROR CRÍTICO en {tool_name} 🚨")
        print("----------------------------------------")
        print(f"Mensaje de error: {error_msg}")
        print("----------------------------------------\n")

    def _create_gemini_container(self, category):
        """Crea un contenedor personalizado para la herramienta Gemini según la categoría."""
        if hasattr(self, "gemini_container") and self.gemini_container:
            self.gemini_container.deleteLater()
            self.gemini_container = None
        self.app._hide_all_sections()
        self.gemini_container = QWidget(self.app.content_container)
        self.gemini_container.setGeometry(50, 50, 780, 500)
        self.gemini_container.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
        """
        )
        main_layout = QVBoxLayout(self.gemini_container)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        top_section = QWidget()
        top_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        top_section.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
            border-radius: 2px;
        """
        )
        top_layout = QVBoxLayout(top_section)
        top_layout.setContentsMargins(10, 10, 10, 10)
        top_layout.setSpacing(10)
        title_label = QLabel("Configuración de Gemini")
        title_label.setStyleSheet(
            """
            font-size: 18px;
            color: white;
            background: transparent;
            qproperty-alignment: AlignCenter;
        """
        )
        title_label.setFont(self.app.super_cartoon_font)
        top_layout.addWidget(title_label)
        gemini_tool = next(
            tool
            for tool in Config.SPECIFIED_TOOLS[category]
            if tool["name"] == "Gemini"
        )
        config_description = gemini_tool.get(
            "config_description", "Descripción no disponible."
        )
        description_label = QTextEdit()
        description_label.setReadOnly(True)
        description_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        description_label.setStyleSheet(
            """
            font-size: 14px;
            color: white;
            background-color: rgba(0, 0, 0, 50);
            border: 1px solid rgba(87, 35, 100, 180);
        """
        )
        description_label.setFont(self.app.roboto_black_font)
        html_description = (
            "<p>" + config_description.replace("\n", "<br>").replace(" ", " ") + "</p>"
        )
        description_label.setHtml(html_description)
        top_layout.addWidget(description_label)
        main_layout.addWidget(top_section, stretch=1)
        custom_section = QWidget()
        custom_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        custom_section.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
            border-radius: 2px;
        """
        )
        bottom_layout = QHBoxLayout(custom_section)
        bottom_layout.setContentsMargins(10, 10, 10, 10)
        bottom_layout.setSpacing(10)
        left_column = QWidget()
        left_column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_column.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
            border-radius: 2px;
        """
        )
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)
        browse_files_button = QPushButton("Examinar Archivos")
        browse_files_button.setStyleSheet(
            """
            QPushButton {
                font-size: 14px;
                color: white;
                background-color: #555555;
                border: none;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
        """
        )
        browse_files_button.setFont(self.app.adventure_font)
        browse_files_button.setCursor(Qt.PointingHandCursor)
        browse_files_button.clicked.connect(self._browse_files)
        browse_folders_button = QPushButton("Examinar Carpetas")
        browse_folders_button.setStyleSheet(
            """
            QPushButton {
                font-size: 14px;
                color: white;
                background-color: #555555;
                border: none;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
        """
        )
        browse_folders_button.setFont(self.app.adventure_font)
        browse_folders_button.setCursor(Qt.PointingHandCursor)
        browse_folders_button.clicked.connect(self._browse_folders)
        browse_layout = QHBoxLayout()
        browse_layout.addWidget(browse_files_button)
        browse_layout.addWidget(browse_folders_button)
        left_layout.addLayout(browse_layout)
        save_button = QPushButton("Guardar Resultados")
        save_button.setStyleSheet(
            """
            QPushButton {
                font-size: 14px;
                color: white;
                background-color: #555555;
                border: none;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
        """
        )
        save_button.setFont(self.app.adventure_font)
        save_button.setCursor(Qt.PointingHandCursor)
        save_button.clicked.connect(self._save_results)
        left_layout.addWidget(save_button)
        bottom_layout.addWidget(left_column, stretch=2)
        right_column = QWidget()
        right_column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_column.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
            border-radius: 2px;
        """
        )
        right_layout = QVBoxLayout(right_column)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(10)
        button_style = """
        QPushButton {
            font-size: 18px;
            color: white;
            background-color: #333333;
            border: none;
            padding: 12px;
        }
        QPushButton:hover {
            background-color: #555555;
        }
        """
        start_button = QPushButton("Iniciar Procesamiento")
        start_button.setStyleSheet(button_style)
        start_button.setFont(self.app.super_cartoon_font)
        start_button.setCursor(Qt.PointingHandCursor)
        start_button.clicked.connect(self._start_gemini_processing)
        right_layout.addWidget(start_button, alignment=Qt.AlignCenter)
        cancel_button = QPushButton("Cancelar")
        cancel_button.setStyleSheet(button_style)
        cancel_button.setFont(self.app.super_cartoon_font)
        cancel_button.setCursor(Qt.PointingHandCursor)
        cancel_button.clicked.connect(self._cancel_gemini_processing)
        right_layout.addWidget(cancel_button, alignment=Qt.AlignCenter)
        bottom_layout.addWidget(right_column, stretch=1)
        main_layout.addWidget(custom_section, stretch=0)
        self.gemini_container.show()

    def _create_mistral_container(self, category):
        """Crea un contenedor personalizado para la herramienta Mistral según la categoría."""
        if hasattr(self, "mistral_container") and self.mistral_container:
            self.mistral_container.deleteLater()
            self.mistral_container = None
        self.app._hide_all_sections()
        self.mistral_container = QWidget(self.app.content_container)
        self.mistral_container.setGeometry(50, 50, 780, 500)
        self.mistral_container.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 2px solid rgba(87, 35, 100, 180);
        """
        )
        main_layout = QVBoxLayout(self.mistral_container)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        top_section = QWidget()
        top_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        top_section.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
            border-radius: 2px;
        """
        )
        top_layout = QVBoxLayout(top_section)
        top_layout.setContentsMargins(10, 10, 10, 10)
        top_layout.setSpacing(10)
        title_label = QLabel("Configuración de Mistral")
        title_label.setStyleSheet(
            """
            font-size: 18px;
            color: white;
            background: transparent;
            qproperty-alignment: AlignCenter;
        """
        )
        title_label.setFont(self.app.super_cartoon_font)
        top_layout.addWidget(title_label)
        mistral_tool = next(
            tool
            for tool in Config.SPECIFIED_TOOLS[category]
            if tool["name"] == "Mistral"
        )
        config_description = mistral_tool.get(
            "config_description", "Descripción no disponible."
        )
        description_label = QTextEdit()
        description_label.setReadOnly(True)
        description_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        description_label.setStyleSheet(
            """
            font-size: 14px;
            color: white;
            background-color: rgba(0, 0, 0, 50);
            border: 1px solid rgba(87, 35, 100, 180);
        """
        )
        description_label.setFont(self.app.roboto_black_font)
        html_description = (
            "<p>" + config_description.replace("\n", "<br>").replace(" ", " ") + "</p>"
        )
        description_label.setHtml(html_description)
        top_layout.addWidget(description_label)
        main_layout.addWidget(top_section, stretch=1)
        custom_section = QWidget()
        custom_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        custom_section.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
            border-radius: 2px;
        """
        )
        bottom_layout = QHBoxLayout(custom_section)
        bottom_layout.setContentsMargins(10, 10, 10, 10)
        bottom_layout.setSpacing(10)
        left_column = QWidget()
        left_column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_column.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
            border-radius: 2px;
        """
        )
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)
        browse_files_button = QPushButton("Examinar Archivos")
        browse_files_button.setStyleSheet(
            """
            QPushButton {
                font-size: 14px;
                color: white;
                background-color: #555555;
                border: none;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
        """
        )
        browse_files_button.setFont(self.app.adventure_font)
        browse_files_button.setCursor(Qt.PointingHandCursor)
        browse_files_button.clicked.connect(self._browse_files)
        browse_folders_button = QPushButton("Examinar Carpetas")
        browse_folders_button.setStyleSheet(
            """
            QPushButton {
                font-size: 14px;
                color: white;
                background-color: #555555;
                border: none;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
        """
        )
        browse_folders_button.setFont(self.app.adventure_font)
        browse_folders_button.setCursor(Qt.PointingHandCursor)
        browse_folders_button.clicked.connect(self._browse_folders)
        browse_layout = QHBoxLayout()
        browse_layout.addWidget(browse_files_button)
        browse_layout.addWidget(browse_folders_button)
        left_layout.addLayout(browse_layout)
        save_button = QPushButton("Guardar Resultados")
        save_button.setStyleSheet(
            """
            QPushButton {
                font-size: 14px;
                color: white;
                background-color: #555555;
                border: none;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
        """
        )
        save_button.setFont(self.app.adventure_font)
        save_button.setCursor(Qt.PointingHandCursor)
        save_button.clicked.connect(self._save_results)
        left_layout.addWidget(save_button)
        bottom_layout.addWidget(left_column, stretch=2)
        right_column = QWidget()
        right_column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_column.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
            border-radius: 2px;
        """
        )
        right_layout = QVBoxLayout(right_column)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(10)
        button_style = """
        QPushButton {
            font-size: 18px;
            color: white;
            background-color: #333333;
            border: none;
            padding: 12px;
        }
        QPushButton:hover {
            background-color: #555555;
        }
        """
        start_button = QPushButton("Iniciar Procesamiento")
        start_button.setStyleSheet(button_style)
        start_button.setFont(self.app.super_cartoon_font)
        start_button.setCursor(Qt.PointingHandCursor)
        start_button.clicked.connect(self._start_mistral_processing)
        right_layout.addWidget(start_button, alignment=Qt.AlignCenter)
        cancel_button = QPushButton("Cancelar")
        cancel_button.setStyleSheet(button_style)
        cancel_button.setFont(self.app.super_cartoon_font)
        cancel_button.setCursor(Qt.PointingHandCursor)
        cancel_button.clicked.connect(self._cancel_mistral_processing)
        right_layout.addWidget(cancel_button, alignment=Qt.AlignCenter)
        bottom_layout.addWidget(right_column, stretch=1)
        main_layout.addWidget(custom_section, stretch=0)
        self.mistral_container.show()

    def _browse_folders(self):
        """Abre un cuadro de diálogo para seleccionar carpetas."""
        folder_dialog = QFileDialog()
        folder_dialog.setFileMode(QFileDialog.Directory)
        folder_dialog.setOption(QFileDialog.ShowDirsOnly, True)
        folder_path = folder_dialog.getExistingDirectory(
            self.app, "Seleccionar Carpeta", QDir.homePath()
        )
        if folder_path:
            self.input_path = folder_path
            print(f"Carpeta seleccionada: {folder_path}")

    def _browse_files(self):
        """Abre un cuadro de diálogo para seleccionar archivos o carpetas."""
        file_dialog = QFileDialog()
        file_dialog.setFileMode(QFileDialog.ExistingFiles)
        file_dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        file_paths, _ = file_dialog.getOpenFileNames(
            self.app, "Seleccionar Archivos", QDir.homePath()
        )
        if file_paths:
            self.input_path = file_paths[0]
            print(f"Archivos seleccionados: {file_paths}")

    def _save_results(self):
        """Guarda los resultados generados por Mistral seleccionando una carpeta de destino."""
        folder_path = QFileDialog.getExistingDirectory(
            self.app, "Seleccionar Carpeta de Destino", QDir.homePath()
        )
        if folder_path:
            self.output_directory = folder_path
            print(f"Resultados se guardarán en: {folder_path}")

    def start_download(self):
        """Inicia el proceso de descarga/configuración con soporte para cancelación."""
        self.cancel_event = threading.Event()
        if self.install_button:
            self.install_button.setEnabled(False)
            self.install_button.setText("Descargando...")
        self.download_in_progress = True
        self.download_state = "downloading"
        self.download_thread = DownloadThread(self.haruneko_manager, self.cancel_event)
        self.download_thread.finished.connect(self.on_download_finished)
        self.download_thread.error.connect(self.on_download_error)
        self.download_thread.start()

    def download_hakuneko(self):
        """Ejecuta el proceso completo de descarga de Hakuneko."""
        self.start_download()

    def update_hakuneko(self):
        """Actualiza la instalación existente de Hakuneko reutilizando la lógica de descarga."""
        self.start_download()

    def on_download_finished(self, success):
        """Maneja la finalización de la descarga actualizando estado y UI según resultado."""
        self.download_in_progress = False
        self.download_state = "finished" if success else "idle"
        if success:
            self.haruneko_installed = True
            self.haruneko_version = self.haruneko_manager.get_current_version()
            if self.install_button:
                self.install_button.setText("Eliminar")
                self.install_button.clicked.disconnect()
                self.install_button.clicked.connect(self.uninstall_hakuneko)
            QMessageBox.information(
                self.app,
                "Descarga Completa",
                "Hakuneko ha sido descargado y descomprimido.",
            )
        else:
            if self.install_button:
                self.install_button.setText("Descargar")
            QMessageBox.critical(self.app, "Error", "No se pudo descargar Hakuneko.")
        if self.install_button:
            self.install_button.setEnabled(True)

    def on_download_error(self, error_msg):
        """Maneja errores durante la descarga mostrando mensaje y reestableciendo UI."""
        self.download_in_progress = False
        QMessageBox.critical(
            self.app, "Error", f"Error durante la descarga: {error_msg}"
        )
        if self.install_button:
            self.install_button.setEnabled(True)
            self.install_button.setText("Descargar")

    def on_download_error(self, error_msg):
        """Actualiza el estado y UI ante errores en el proceso de descarga."""
        self.download_in_progress = False
        self.download_state = "error"
        QMessageBox.critical(
            self.app, "Error", f"Error durante la descarga: {error_msg}"
        )
        if self.install_button:
            self.install_button.setEnabled(True)
            self.install_button.setText("Descargar")

    def uninstall_hakuneko(self):
        """Desinstala HaruNeko eliminando sus archivos y actualizando el estado."""
        if not self.haruneko_installed:
            QMessageBox.warning(self.app, "Advertencia", "HaruNeko no está instalado.")
            return
        try:
            haruneko_dir = Config.HARUNEKO_DIR
            if os.path.exists(haruneko_dir):
                shutil.rmtree(haruneko_dir)
                self.haruneko_installed = False
                self.haruneko_version = None
                if self.install_button:
                    self.install_button.setText("Descargar")
                    self.install_button.clicked.disconnect()
                    self.install_button.clicked.connect(self.download_hakuneko)
                QMessageBox.information(
                    self.app,
                    "Desinstalación Completa",
                    "HaruNeko ha sido desinstalado exitosamente.",
                )
            else:
                QMessageBox.warning(
                    self.app, "Advertencia", "El directorio de HaruNeko no se encontró."
                )
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(
                self.app, "Error", f"No se pudo desinstalar HaruNeko: {e}"
            )
        finally:
            self.check_for_updates()

    def start_hakuneko(self):
        """Inicia el programa HaruNeko si está instalado."""
        if not self.haruneko_installed or not self.haruneko_version:
            QMessageBox.warning(
                self.app,
                "Advertencia",
                "HaruNeko no está instalado. Por favor, descárgalo primero.",
            )
            return
        exe_path = self.haruneko_manager.get_exe_path(self.haruneko_version)
        if exe_path and os.path.exists(exe_path):
            try:
                subprocess.Popen([exe_path])
            except Exception as e:  # pylint: disable=broad-exception-caught
                QMessageBox.critical(
                    self.app, "Error", f"No se pudo iniciar HaruNeko: {e}"
                )
        else:
            QMessageBox.critical(
                self.app, "Error", "No se encontró el ejecutable de HaruNeko."
            )

    def check_for_updates(self):
        """Verifica actualizaciones y actualiza el botón si hay una nueva versión disponible."""
        latest_version = self.haruneko_manager.check_for_updates()
        if (
            latest_version
            and self.haruneko_version
            and latest_version != self.haruneko_version
        ):
            self.install_button.setText("Actualizar")
            self.install_button.clicked.disconnect()
            self.install_button.clicked.connect(self.update_hakuneko)

    def install_hakuneko(self):
        """Inicia la descarga e instalación de Hakuneko."""
        self.download_hakuneko()

    def _start_gemini_processing(self):
        """Inicia Gemini en segundo plano validando rutas y manejando cancelaciones."""
        try:
            if not self.input_path or not self.output_directory:
                raise ValueError(
                    "Las rutas de entrada y salida deben estar configuradas."
                )
            self.cancel_event = threading.Event()
            self.processing_thread = gemini.start_processing_in_background(
                self.input_path,
                self.output_directory,
                self.cancel_event,
                callback=self._handle_processing_finished,
            )
            QMessageBox.information(
                self.app,
                "Procesamiento iniciado",
                "El procesamiento se ha iniciado. Puedes cancelarlo en cualquier momento.",
            )
        except ValueError as e:
            QMessageBox.critical(
                self.app, "Error", f"Error durante el procesamiento: {e}"
            )

    def _cancel_gemini_processing(self):
        """Cancela el procesamiento en curso de Gemini."""
        if hasattr(self, "cancel_event") and self.cancel_event:
            self.cancel_event.set()
            QMessageBox.information(
                self.app,
                "Procesamiento Cancelado",
                "El procesamiento ha sido cancelado.",
            )
        else:
            QMessageBox.warning(
                self.app,
                "Sin Proceso",
                "No hay ningún procesamiento en curso para cancelar.",
            )

    def _start_mistral_processing(self):
        """Inicia este procesamiento en segundo plano validando rutas y manejando cancelaciones."""
        try:
            if not self.input_path or not self.output_directory:
                raise ValueError(
                    "Las rutas de entrada y salida deben estar configuradas."
                )
            self.cancel_event = threading.Event()
            self.processing_thread = mistral.start_processing_in_background(
                self.input_path,
                self.output_directory,
                self.cancel_event,
                callback=self._handle_processing_finished,
            )
            QMessageBox.information(
                self.app,
                "Procesamiento iniciado",
                "El procesamiento se ha iniciado. Puedes cancelarlo en cualquier momento.",
            )
        except ValueError as e:
            QMessageBox.critical(
                self.app, "Error", f"Error durante el procesamiento: {e}"
            )

    def _cancel_mistral_processing(self):
        """Cancela el procesamiento en curso de Mistral."""
        if hasattr(self, "cancel_event") and self.cancel_event:
            self.cancel_event.set()
            QMessageBox.information(
                self.app,
                "Procesamiento Cancelado",
                "El procesamiento ha sido cancelado.",
            )
        else:
            QMessageBox.warning(
                self.app,
                "Sin Proceso",
                "No hay ningún procesamiento en curso para cancelar.",
            )

    def _show_completion_message(self, success):
        """Muestra el mensaje en el hilo principal"""
        QMessageBox.information(
            self.app,
            "Proceso Completado" if success else "Error",
            (
                "Procesamiento finalizado exitosamente!"
                if success
                else "Ocurrió un error durante el procesamiento."
            ),
        )

    def _handle_processing_finished(self, success):
        """Envía el resultado a través de la señal (seguro para hilos)"""
        self.processing_finished.emit(bool(success))

    def open_path_for_prompt(self, text_box):
        """Abre un cuadro de diálogo para seleccionar un archivo .txt y actualiza la ruta."""
        desktop_path = os.path.join(os.getenv("USERPROFILE"), "Desktop")
        file_dialog = QFileDialog()
        file_dialog.setDirectory(desktop_path)
        selected_path, _ = file_dialog.getOpenFileName(
            self.app, "Seleccionar archivo", desktop_path, "Archivos de texto (*.txt)"
        )
        if selected_path:
            text_box.setText(selected_path)
            if "Gemini" in text_box.objectName():
                Config.GEMINI_PROMPT = selected_path
            elif "Mistral" in text_box.objectName():
                Config.MISTRAL_PROMPT = selected_path

    def _create_scroll_area(self, section_type):
        """Crea un área de desplazamiento para herramientas o proyectos."""
        scroll_area = QScrollArea(self.app.content_container)
        scroll_area.setGeometry(50, 50, 780, 500)
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet(
            "background-color: rgba(0, 0, 0, 100);" "border-radius: 0px;"
        )
        image_container = QWidget()
        container_style = """
        background-color: transparent;
        border: 2px solid rgba(87, 35, 100, 180);
        border-radius: 4px;
        padding: 2px;
        """
        image_container.setStyleSheet(container_style)
        image_layout = QGridLayout(image_container)
        if section_type == "utilities":
            folder = Config.GENERAL_TOOLS_FOLDER
            size = (122, 122)
            cols = 6
        elif section_type == "projects":
            folder = None
            size = (141, 212)
            cols = 5
        if folder and os.path.exists(folder):
            self.app.project_manager._load_images_with_descriptions(
                image_layout, folder, size, cols
            )
        scroll_area.setWidget(image_container)
        image_container.adjustSize()
        if image_container.sizeHint().height() <= scroll_area.viewport().height():
            scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return scroll_area

    def _create_footer_text(self, section_type):
        """Crea el texto del pie de página para cada sección."""
        footer_label = QLabel(self.app.content_container)
        footer_label.setText(
            Config.UTILITIES_FOOTER_TEXT
            if section_type == "utilities"
            else Config.PROJECTS_FOOTER_TEXT
        )
        footer_label.setStyleSheet(
            "font-size: 18px;" "color: white;" "background: none;" "padding: 10px;"
        )
        footer_label.setFont(self.app.super_cartoon_font)
        footer_label.setAlignment(Qt.AlignCenter)
        footer_label.setGeometry(50, 500, 800, 150)
        return footer_label
