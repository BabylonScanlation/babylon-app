"Módulo 'tools.py' dónde esta la lógica que permite cargar los datos de las herramientas."

# bibliotecas nativas
import logging
import os
import shutil
import subprocess
import threading
import webbrowser
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
    cast,
)

from app_tools import mistral, translatorz

# bibliotecas no nativas
# módulos (no tocar)
from app_tools.gemini import GeminiAPIError, GeminiProcessor
from app_tools.haruneko import DownloadThread, HaruNekoManager
from app_tools.mistral import MistralAPIError
from config import Config
import shiboken6

# bibliotecas no nativas
# pylint: disable=no-name-in-module
from PySide6.QtCore import (
    QDir,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
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
from worker import Worker

if TYPE_CHECKING:
    from bbsl_app import App


# pylint: disable=too-few-public-methods
class TranslationTask(QRunnable):
    """Maneja tareas de traducción en hilos en segundo plano."""

    def __init__(
        self, tool: Dict[str, Any], text: str, source_lang: str, target_lang: str
    ):
        super().__init__()
        self.tool = tool
        self.text = text
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.signals = TranslationSignals()

    def run(self):
        """Ejecuta la tarea de traducción y asegura la respuesta."""
        name = self.tool["name"]
        try:
            # Llamada directa.
            translated_text = translatorz.translatorz(
                name, self.text, self.source_lang, self.target_lang
            )

            if "Idioma escogido a traducir incompatible." in translated_text:
                self.signals.finished.emit(name, "", translated_text)
            else:
                self.signals.finished.emit(name, translated_text, "")

        except Exception as e:
            self.signals.finished.emit(name, "", f"Error crítico: {str(e)}")


# pylint: disable=too-few-public-methods
class TranslationSignals(QObject):
    """Define señales para comunicación entre hilos y la GUI."""

    finished = Signal(str, str, str)
    error_occurred = Signal(str, str)


class ExpandedTextEditorDialog(QDialog):
    """
    Diálogo para editar texto en un área expandida con botones de copiar/pegar/volver.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        initial_text: str = "",
        title: str = "Editar Texto",
        target_widget: Optional[Union[QLineEdit, QTextEdit]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        # self.setGeometry(100, 100, 600, 400) # Original size and position
        self.resize(600, 400)  # Set initial size

        self.target_widget = (
            target_widget  # Reference to the original QLineEdit/QTextEdit
        )

        self.setStyleSheet("""
            QDialog {
                background-color: rgba(10, 10, 10, 230);
                border: 2px solid #960096;
                border-radius: 15px;
            }
            QTextEdit {
                font-size: 14px;
                color: white;
                background-color: rgba(0, 0, 0, 180);
                border: 1px solid #572364;
                border-radius: 8px;
                padding: 10px;
            }
            QPushButton {
                font-size: 13px;
                color: white;
                background-color: rgba(40, 40, 40, 200);
                border: 1px solid #572364;
                border-radius: 5px;
                padding: 8px 15px;
            }
            QPushButton:hover {
                background-color: rgba(150, 0, 150, 40);
                border: 1px solid #960096;
            }
        """)

        main_layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setText(initial_text)
        self.text_edit.setAcceptRichText(False)  # Force plain text pasting
        main_layout.addWidget(self.text_edit)

        button_layout = QHBoxLayout()

        back_button = QPushButton("Volver")
        back_button.clicked.connect(self._on_back_clicked)
        button_layout.addWidget(back_button)

        copy_button = QPushButton("Copiar")
        copy_button.clicked.connect(self._on_copy_clicked)
        button_layout.addWidget(copy_button)

        paste_button = QPushButton("Pegar")
        paste_button.clicked.connect(self._on_paste_clicked)
        button_layout.addWidget(paste_button)

        clear_button = QPushButton("Borrar")
        clear_button.clicked.connect(self._on_clear_clicked)
        button_layout.addWidget(clear_button)

        main_layout.addLayout(button_layout)

        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        screen_geometry = screen.geometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = (screen_geometry.height() - self.height()) // 2
        self.move(x, y)

    def _on_back_clicked(self):
        """Actualiza el widget de destino con el texto edit editado y cierra el diálogo."""
        target = self.target_widget
        if target is not None:
            text = self.text_edit.toPlainText()
            # Usamos hasattr para evitar avisos de Pylance sobre tipos específicos de PySide6
            if hasattr(target, "setPlainText"):
                cast(QTextEdit, target).setPlainText(text)
            elif hasattr(target, "setText"):
                cast(QLineEdit, target).setText(text)
        self.accept()

    def _on_copy_clicked(self):
        """Copies the content of the text editor to the clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(self.text_edit.toPlainText())

    def _on_paste_clicked(self):
        """Pastes the content from the clipboard to the text editor."""
        clipboard = QApplication.clipboard()
        self.text_edit.insertPlainText(clipboard.text())

    # Removed _on_select_all_clicked as per user request
    # def _on_select_all_clicked(self):
    #     """Selects all text in the text editor."""
    #     self.text_edit.selectAll()

    def _on_clear_clicked(self):
        """Clears all text in the text editor."""
        self.text_edit.clear()


# pylint: disable=too-many-instance-attributes, too-many-lines
class ToolsManager(QObject):
    """Gestiona la creación y configuración de herramientas de la aplicación."""

    processing_finished = Signal(str, str)  # Added second str for error_message
    status_update_signal = Signal(
        str
    )  # Señal para actualizaciones de estado desde hilos

    def __init__(self, app: "App"):
        super().__init__()
        self.app = app
        self.processing_finished.connect(self.show_completion_message)
        self.status_update_signal.connect(
            self._update_processing_status
        )  # Conexión segura de hilos
        # Aseguramos que el método existe antes de conectar
        if hasattr(self, "on_gemini_config_closed"):
            self.app.gemini_config_closed.connect(self.on_gemini_config_closed)
        self.utilities_area: Optional[Dict[str, Any]] = None
        self.parent_container: Optional[QWidget] = None
        self.details_container: Optional[QWidget] = None
        self.gemini_container: Optional[QWidget] = None
        self.mistral_container: Optional[QWidget] = None
        self.haruneko_manager = HaruNekoManager(self.app)
        self.input_path: Optional[str] = None
        self.output_directory: Optional[str] = None
        self.image_to_category: Optional[Dict[int, str]] = None
        self.cancel_event: Optional[threading.Event] = None
        self.download_state = "idle"
        self.processing_thread: Optional[threading.Thread] = None
        self.active_threads: List[Any] = []
        self.gemini_processor = GeminiProcessor()
        self.mistral_processor = mistral.MistralProcessor()
        self.haruneko_installed = False
        self.haruneko_version: Optional[str] = None
        self.install_button: Optional[QPushButton] = None
        self.download_in_progress = False
        self.download_thread: Optional[DownloadThread] = None
        self.show_ai_tools = False
        self.toggle_ai_button: Optional[QPushButton] = None
        self.source_combo: Optional[QComboBox] = None
        self.target_combo: Optional[QComboBox] = None
        self.header_panel: Optional[QWidget] = None  # Inicialización explícita
        self.footer_panel: Optional[QWidget] = None  # Inicialización explícita
        self.translator_warning_label: Optional[QLabel] = (
            None  # Inicialización explícita
        )
        self.custom_text_input: Optional[QLineEdit] = None  # Inicialización explícita
        self.use_custom_button: Optional[QPushButton] = None  # Inicialización explícita
        self.selected_files_for_processing: List[str] = []
        self.active_global_threads = 0
        self._current_global_handler: Optional[Callable[[str, str, str], None]] = None
        # Lista para mantener vivas las tareas activas y evitar "Signal source has been deleted"
        self._active_tasks: List[TranslationTask] = []
        # Cache para persistir resultados de traducción si el widget se destruye temporalmente
        self.translation_cache: Dict[str, str] = {}
        self.last_global_text: str = ""

        if not os.path.exists(Config.AI_PROMPT_USER):
            try:
                with open(Config.AI_PROMPT, "r", encoding="utf-8") as f_default:
                    default_prompt = f_default.read()
                with open(Config.AI_PROMPT_USER, "w", encoding="utf-8") as f_user:
                    f_user.write(default_prompt)
            except FileNotFoundError:
                # Create an empty user file if the default is not found
                with open(Config.AI_PROMPT_USER, "w", encoding="utf-8") as f_user:
                    f_user.write("No se pudo encontrar el prompt por defecto.")

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
        content_layout = cast(Any, scroll_content).layout()
        if content_layout:
            for i in range(content_layout.count()):
                item = content_layout.itemAt(i)
                if item:
                    widget = item.widget()
                    if widget is not None:
                        category = (
                            self.image_to_category.get(i)
                            if self.image_to_category
                            else None
                        )
                        if category:
                            # Usamos 'ev' para coincidir con la firma esperada por PySide6
                            def on_mouse_press(ev: Any, cat: str = category):
                                self.show_tool_details(cat)

                            widget.mousePressEvent = on_mouse_press
        self.utilities_area = {
            "scroll": scroll_area,
            "footer": self._create_footer_text("utilities"),
        }
        return self.utilities_area

    def show_tool_details(self, category: str):
        """Muestra los detalles de las herramientas específicas para una categoría."""
        if self.toggle_ai_button:
            self.toggle_ai_button.hide()
        if self.source_combo:
            self.source_combo.hide()
        if self.target_combo:
            self.target_combo.hide()

        if self.utilities_area is not None:
            if "scroll" in self.utilities_area:
                cast(QWidget, self.utilities_area["scroll"]).hide()
            if "footer" in self.utilities_area:
                cast(QWidget, self.utilities_area["footer"]).hide()

        # Si ya existe un contenedor para esta categoría, simplemente lo mostramos
        if self.parent_container:
            # Si el contenedor actual es de una categoría diferente, lo borramos
            # Pero si es la misma, lo reutilizamos para no matar las tareas en curso
            current_cat = getattr(self.parent_container, "category", None)
            if current_cat == category:
                self.parent_container.show()
                self.parent_container.raise_()
                if category == "traductor":
                    if self.header_panel:
                        self.header_panel.show()
                        self.header_panel.raise_()
                        # Asegurar que los hijos del header también se muestren
                        for child in self.header_panel.findChildren(QWidget):
                            child.show()
                    if self.footer_panel:
                        self.footer_panel.show()
                        self.footer_panel.raise_()
                return
            else:
                self.parent_container.deleteLater()
                self.parent_container = None

        self.parent_container = QWidget(self.app.content_container)
        setattr(self.parent_container, "category", category)
        self.parent_container.setGeometry(50, 50, 780, 500)
        self.parent_container.setStyleSheet(
            """
            QWidget {
                background-color: rgba(0, 0, 0, 150);
                border: 1px solid rgba(150, 0, 150, 100);
                border-radius: 15px;
            }
            """
        )
        if category == "traductor":
            # --- PANEL DE CABECERA (Header) ---
            if not hasattr(self, "header_panel") or self.header_panel is None:
                self.header_panel = QWidget(self.app.content_container)
                self.header_panel.setGeometry(50, 5, 780, 40)
                self.header_panel.setStyleSheet("""
                    background-color: rgba(20, 20, 20, 180);
                    border: 1px solid rgba(150, 0, 150, 100);
                    border-radius: 8px;
                """)
            self.header_panel.show()
            self.header_panel.raise_()

            if not self.toggle_ai_button:
                self.toggle_ai_button = QPushButton("IAs", self.header_panel)
                self.toggle_ai_button.setFont(self.app.adventure_font)
                self.toggle_ai_button.setCursor(Qt.CursorShape.PointingHandCursor)
                self.toggle_ai_button.clicked.connect(self.toggle_ai_tools)
                self.toggle_ai_button.setGeometry(5, 2, 110, 35)

            if not self.source_combo:
                self.source_combo = QComboBox(self.header_panel)
                # ... (resto de configuración del combo igual)
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
                self.source_combo.setGeometry(120, 2, 120, 35)

            if not self.target_combo:
                self.target_combo = QComboBox(self.header_panel)
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
                self.target_combo.setGeometry(245, 2, 120, 35)
                self.target_combo.setCurrentIndex(5)

            if self.custom_text_input is None:
                self.custom_text_input = QLineEdit(self.header_panel)
                self.custom_text_input.setPlaceholderText("Introduce texto aquí...")
                self.custom_text_input.setFixedSize(325, 35)
                self.custom_text_input.setFont(self.app.roboto_black_font)
                # Restaurar texto previo si existe
                if self.last_global_text:
                    self.custom_text_input.setText(self.last_global_text)
                
                # Cast para asegurar que Pylance no se queje del tipo opcional
                self.custom_text_input.mousePressEvent = ( # type: ignore
                    lambda a0: self.open_expanded_editor(
                        cast(QLineEdit, self.custom_text_input), "Editar Texto Global"
                    )
                )
                self.custom_text_input.setGeometry(370, 2, 325, 35)

            if self.use_custom_button is None:
                self.use_custom_button = QPushButton("USAR", self.header_panel)
                self.use_custom_button.setFixedSize(75, 35)
                self.use_custom_button.setFont(self.app.adventure_font)
                self.use_custom_button.setCursor(Qt.CursorShape.PointingHandCursor)
                
                # Restaurar estado si hay hilos activos
                if self.active_global_threads > 0:
                    self.use_custom_button.setEnabled(False)
                    self.use_custom_button.setText("Traduciendo")
                
                self.use_custom_button.clicked.connect(
                    lambda: self.ejecutar_traducciones_globales()
                )
                self.use_custom_button.setGeometry(700, 2, 75, 35)

            # --- PANEL DE PIE DE PÁGINA (Warning) ---
            if not hasattr(self, "footer_panel") or self.footer_panel is None:
                self.footer_panel = QWidget(self.app.content_container)
                self.footer_panel.setGeometry(50, 555, 780, 40)
                self.footer_panel.setStyleSheet("""
                    background-color: rgba(20, 20, 20, 150);
                    border: 1px solid rgba(150, 0, 150, 50);
                    border-radius: 8px;
                """)
                self.translator_warning_label = QLabel(self.footer_panel)
                self.translator_warning_label.setText(
                    "Algunos traductores pueden presentar errores temporales o incompatibilidades. Reintente si es necesario."
                )
                self.translator_warning_label.setStyleSheet(
                    "color: #7f7f7f; border: none; background: transparent;"
                )
                self.translator_warning_label.setFont(self.app.roboto_black_font)
                self.translator_warning_label.setWordWrap(True)
                self.translator_warning_label.setFixedSize(770, 35)
                self.translator_warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.translator_warning_label.move(5, 2)

            self.footer_panel.show()
            self.footer_panel.raise_()

            # Forzar visibilidad
            controls: List[Optional[QWidget]] = [
                self.toggle_ai_button,
                self.source_combo,
                self.target_combo,
                self.custom_text_input,
                self.use_custom_button,
            ]
            for ctrl in controls:
                if ctrl:
                    ctrl.show()

        scroll_area = (
            QScrollArea(self.parent_container)
            if self.parent_container
            else QScrollArea()
        )
        scroll_area.setWidgetResizable(True)
        scroll_area.setGeometry(0, 0, 780, 500)
        scroll_area.setStyleSheet(
            "background: transparent; border: none;"
        )  # Quitar bordes internos
        details_container = QWidget()
        details_container.setStyleSheet("background: transparent; border: none;")
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
            details_layout.addWidget(
                tool_container, alignment=Qt.AlignmentFlag.AlignTop
            )

        scroll_area.setWidget(details_container)
        if self.parent_container:
            self.parent_container.show()

    def ejecutar_traducciones_globales(self):
        if self.custom_text_input is None or self.use_custom_button is None:
            return
        texto = self.custom_text_input.text().strip()
        if not texto:
            return
        
        self.last_global_text = texto # Guardar para persistencia
        self.use_custom_button.setEnabled(False)
        self.use_custom_button.setText("Traduciendo")
        QApplication.processEvents()

        # Reducimos a 4 hilos para evitar que las APIs nos bloqueen
        QThreadPool.globalInstance().setMaxThreadCount(4)

        # self.parent_container is already checked at the start of show_tool_details
        # and initialized if needed, but we can just use it safely here if we trust the logic.
        # Pylance thinks it's already a QWidget here.
        scroll_area = (
            self.parent_container.findChild(QScrollArea)
            if self.parent_container
            else None
        )
        if not scroll_area:
            return
        details_container = cast(Optional[QWidget], scroll_area.widget())
        if details_container is None:
            return

        layout = details_container.layout()
        if layout is None:
            return

        tool_containers: List[QWidget] = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item:
                widget = item.widget()
                if widget and widget.isVisible():
                    tool_containers.append(widget)

        # Ordenar contenedores por puntuación (rating) de mayor a menor
        tool_containers.sort(
            key=lambda x: cast(Any, x).tool.get("rating", 0), reverse=True
        )

        self.active_global_threads = len(tool_containers)
        self._current_global_handler = self._handle_global_translation_finish
        
        # Limpiar caché para los traductores que vamos a ejecutar ahora
        for container in tool_containers:
            t_name = cast(Any, container).tool.get("name")
            if t_name in self.translation_cache:
                del self.translation_cache[t_name]

        if self.active_global_threads <= 0:
            self.use_custom_button.setEnabled(True)
            self.use_custom_button.setText("USAR")
            self._current_global_handler = None
            return

        for container in tool_containers:
            input_field = container.findChild(QLineEdit)
            output_field = container.findChild(QTextEdit)
            use_button = container.findChild(QPushButton)

            if (
                not isinstance(input_field, QLineEdit)
                or not isinstance(output_field, QTextEdit)
                or not isinstance(use_button, QPushButton)
            ):
                self.active_global_threads -= 1
                if self.active_global_threads <= 0:
                    self.use_custom_button.setEnabled(True)
                    self.use_custom_button.setText("USAR")
                continue

            input_field.setText(texto)
            output_field.clear()
            QApplication.processEvents()
            task = self._translate_text(
                input_field, output_field, cast(Any, container).tool
            )
            if not task:
                self.active_global_threads -= 1
                if self.active_global_threads <= 0:
                    self.use_custom_button.setEnabled(True)
                    self.use_custom_button.setText("USAR")

        # Limpiar el manejador después de iniciar todas las tareas
        self._current_global_handler = None

    def _handle_global_translation_finish(self, name: str, result: str, error: str):
        self.active_global_threads -= 1
        if self.active_global_threads <= 0 and self.use_custom_button is not None:
            self.use_custom_button.setEnabled(True)
            self.use_custom_button.setText("USAR")

    def _handle_global_translation_error(self, tool_name: str, error_msg: str):
        self.active_global_threads -= 1
        if self.active_global_threads <= 0 and self.use_custom_button is not None:
            self.use_custom_button.setEnabled(True)
            self.use_custom_button.setText("USAR")
            QApplication.processEvents()

    def toggle_ai_tools(self):
        """Alterna la visibilidad de las herramientas de IA en la categoría 'traductor'."""
        self.show_ai_tools = not self.show_ai_tools
        if self.parent_container:
            # Forzamos eliminación aquí para que se redibuje con el nuevo filtro de IAs
            self.parent_container.deleteLater()
            self.parent_container = None
        
        # También ocultamos los paneles para que show_tool_details los recree si es necesario
        if self.header_panel: self.header_panel.hide()
        if self.footer_panel: self.footer_panel.hide()
            
        self.show_tool_details("traductor")
        if self.toggle_ai_button is not None:
            self.toggle_ai_button.setText("IAs" if self.show_ai_tools else "IAs")

    def _create_tool_container(self, tool: Dict[str, Any], category: str) -> QWidget:
        """Crea un contenedor individual para una herramienta con diseño moderno."""
        tool_container = QWidget()
        cast(Any, tool_container).tool = tool
        tool_container.setFixedSize(740, 250)
        tool_container.setObjectName("ToolCard")
        tool_container.setStyleSheet(
            """
            #ToolCard {
                background-color: rgba(20, 22, 28, 220);
                border: 1px solid rgba(157, 70, 255, 0.3);
                border-radius: 16px;
            }
            #ToolCard:hover {
                border: 1px solid rgba(157, 70, 255, 0.6);
                background-color: rgba(25, 27, 35, 230);
            }
            """
        )
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
            description_scroll_area.setFixedHeight(100)
        elif category == "traductor":
            description_scroll_area.setFixedHeight(100)
        else:
            description_scroll_area.setFixedHeight(184)

        left_layout.addWidget(description_scroll_area)
        if category == "ai":
            routes_container = QWidget()
            routes_layout = QVBoxLayout(routes_container)
            routes_layout.setContentsMargins(5, 5, 5, 5)
            routes_layout.setSpacing(2)
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
            config_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            )
            routes_layout.addWidget(config_label)
            access_paths = tool.get("access_paths", [])
            for idx, path_info in enumerate(access_paths):
                route_layout = QHBoxLayout()
                route_layout.setContentsMargins(0, 2, 0, 2)
                route_layout.setSpacing(5)
                label_text = ""
                if tool["name"] in ["Gemini", "Mistral"]:
                    label_text = "PROMPT:" if idx == 0 else "API:"
                label = QLabel(label_text)
                label.setFixedWidth(70)
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

                # Usar el valor actual de Config si es una API Key, de lo contrario usar el del path_info
                current_path = path_info["path"]
                if label_text == "API:":
                    if tool["name"] == "Gemini":
                        current_path = Config.GEMINI_API_KEY
                    elif tool["name"] == "Mistral":
                        current_path = Config.MISTRAL_API_KEY

                route_input = QLineEdit(current_path)
                route_input.setReadOnly(False)  # Permitir edición
                route_input.setObjectName(f"route_input_{tool['name']}_{label_text}")
                route_input.setStyleSheet(
                    """
                QLineEdit {
                    font-size: 12px;
                    color: white;
                    background-color: rgba(20, 20, 20, 200);
                    border: 1px solid #572364;
                    border-radius: 4px;
                    padding: 5px;
                }
                QLineEdit:focus {
                    border: 1px solid #960096;
                }
                """
                )

                # Conectar el cambio de texto para actualizar la API Key y guardarla permanentemente
                if label_text == "API:":

                    def save_api_key(text: str, t_name: str = tool["name"]):
                        clean_text = text.strip()
                        if t_name == "Gemini":
                            Config.GEMINI_API_KEY = clean_text
                            Config.save_user_settings({"GEMINI_API_KEY": clean_text})
                        elif t_name == "Mistral":
                            Config.MISTRAL_API_KEY = clean_text
                            Config.save_user_settings({"MISTRAL_API_KEY": clean_text})

                    route_input.textChanged.connect(save_api_key)

                route_input.setFont(self.app.roboto_black_font)
                route_input.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
                )
                route_layout.addWidget(route_input)
                if label_text != "API:":
                    browse_button = QPushButton("Examinar")
                    browse_button.setFixedWidth(80)
                    browse_button.clicked.connect(
                        lambda checked=False,
                        input_box=route_input: self.open_path_for_prompt(input_box)
                    )
                    browse_button.setFont(self.app.adventure_font)
                    browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
                    browse_button.setStyleSheet(
                        "QPushButton { color: white; padding-left: 2px; padding-right: 2px; }"
                    )
                    route_layout.addWidget(browse_button)
                routes_layout.addLayout(route_layout)
            left_layout.addWidget(routes_container)
        if category == "traductor":
            input_container = QLineEdit()
            input_container.setFrame(False)  # Quitar marco cuadrado por defecto
            input_container.setPlaceholderText("Introduce el texto a traducir...")
            input_container.setStyleSheet(
                """
                QLineEdit {
                    font-size: 14px;
                    color: white;
                    background-color: rgba(10, 12, 16, 0.6);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 8px;
                    margin-top: 10px;
                    padding-left: 8px;
                }
                QLineEdit::placeholder {
                    color: rgba(255, 255, 255, 0.3);
                    font-style: italic;
                }
                QLineEdit:focus {
                    border: 1px solid #9d46ff;
                    background-color: rgba(10, 12, 16, 0.8);
                }
            """
            )
            input_container.setFont(self.app.roboto_black_font)
            input_container.setFixedHeight(55)
            # Casting Qt itself to Any resolves issues with dynamic members like AlignLeft
            qt_any = cast(Any, Qt)
            input_container.setAlignment(qt_any.AlignLeft | qt_any.AlignTop)
            # Make input_container clickable to open expanded editor
            input_container.mousePressEvent = lambda a0: self.open_expanded_editor( # type: ignore
                input_container, "Editar Texto de Entrada"
            )
            # Restaurar el texto global si estamos en la vista de traductores
            if category == "traductor" and self.last_global_text:
                input_container.setText(self.last_global_text)
                
            left_layout.addWidget(input_container)

            output_container = QTextEdit()
            output_container.setFrameShape(
                QFrame.Shape.NoFrame
            )  # Quitar marco cuadrado por defecto
            output_container.setReadOnly(False)  # Allow editing
            output_container.setPlaceholderText("La traducción aparecerá aquí...")
            output_container.setStyleSheet(
                """
                QTextEdit {
                    font-size: 14px;
                    color: white;
                    background-color: rgba(10, 12, 16, 0.6);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 8px;
                    padding: 8px;
                }
                QTextEdit::placeholder {
                    color: rgba(255, 255, 255, 0.3);
                    font-style: italic;
                }
                QTextEdit:focus {
                    border: 1px solid #9d46ff;
                    background-color: rgba(10, 12, 16, 0.8);
                }
            """
            )
            output_container.setFont(self.app.roboto_black_font)
            output_container.setFixedHeight(45)
            output_container.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            )
            # Make output_container clickable to open expanded editor
            output_container.mousePressEvent = lambda a0: self.open_expanded_editor( # type: ignore
                output_container, "Editar Traducción"
            )
            
            # Restaurar desde caché si existe un resultado previo
            if tool["name"] in self.translation_cache:
                output_container.setText(self.translation_cache[tool["name"]])
                
            left_layout.addWidget(output_container)

        tool_layout.addWidget(left_container)

        # Elementos de la derecha añadidos directamente al layout principal
        right_side_layout = QVBoxLayout()
        right_side_layout.setContentsMargins(5, 5, 5, 5)
        right_side_layout.setSpacing(5)
        right_side_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Puntuación (Chip de Neón)
        rating_label = QLabel(f"{tool['rating']}")
        rating_label.setFixedSize(80, 24)
        rating = tool["rating"]
        # Colores más vibrantes para el fondo del chip
        if rating >= 9:
            bg_color = "rgba(0, 255, 128, 0.2)"
            border_color = "#00ff80"
        elif rating >= 5:
            bg_color = "rgba(255, 165, 0, 0.2)"
            border_color = "#ffaa00"
        else:
            bg_color = "rgba(255, 50, 50, 0.2)"
            border_color = "#ff3333"

        qt_any = cast(Any, Qt)
        rating_label.setStyleSheet(
            f"""
            background-color: {bg_color};
            border: 1px solid {border_color};
            border-radius: 12px;
            color: white;
            font-weight: bold;
            qproperty-alignment: AlignCenter;
            """
        )
        rating_label.setFont(self.app.roboto_black_font)
        right_side_layout.addWidget(rating_label, alignment=qt_any.AlignCenter)

        # Nombre
        name_label = QLabel(tool["name"])
        name_label.setStyleSheet(
            "font-size: 16px; color: white; border: none; qproperty-alignment: AlignCenter;"
        )
        name_label.setFont(self.app.super_cartoon_font)
        right_side_layout.addWidget(name_label)

        # Imagen (Icono Circular)
        image_label = QLabel()
        image_label.setFixedSize(80, 80)
        image_label.setAlignment(qt_any.AlignCenter)
        image_label.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 0.6);
            border: 2px solid rgba(157, 70, 255, 0.5);
            border-radius: 40px; /* Circular */
            """
        )

        # OPTIMIZACIÓN: Cargar la imagen ya escalada desde disco para ahorrar RAM y CPU
        from PySide6.QtGui import QImageReader

        reader = QImageReader(tool["image_path"])
        orig_size = reader.size()
        if orig_size.isValid():
            # Escalar manteniendo el aspecto original dentro de un máximo de 50x50
            orig_size.scale(50, 50, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(orig_size)
        pixmap = QPixmap.fromImage(reader.read())

        if not pixmap.isNull():
            image_label.setPixmap(pixmap)

        def open_tool_site(_event: Any):
            if tool["name"] in Config.TOOL_URLS:
                webbrowser.open(Config.TOOL_URLS[tool["name"]])

        image_label.mousePressEvent = open_tool_site # type: ignore
        image_label.setCursor(qt_any.PointingHandCursor)
        right_side_layout.addWidget(image_label, alignment=qt_any.AlignCenter)

        # Contenedor para botones (UNICO CONTENEDOR VISIBLE)
        buttons_container = QWidget()
        buttons_container.setObjectName("ButtonsBox")
        buttons_container.setStyleSheet(
            """
            #ButtonsBox {
                background-color: transparent;
                border: none;
            }
            """
        )
        buttons_layout = QVBoxLayout(buttons_container)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(8)
        buttons_layout.setAlignment(qt_any.AlignCenter)

        # Botón Instalar
        install_button = QPushButton(
            "Descargar" if tool["name"] == "HaruNeko" else "Instalar"
        )
        install_button.setFixedSize(110, 28)
        install_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(30, 30, 30, 200);
                color: #e0e0e0;
                border: 1px solid rgba(150, 0, 150, 100);
                border-radius: 4px;
                padding: 2px;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 220);
                border: 1px solid #960096;
                color: white;
            }
            QPushButton:disabled {
                background-color: rgba(10, 10, 10, 150);
                color: rgba(150, 150, 150, 100);
                border: 1px solid rgba(150, 0, 150, 30);
            }
        """)

        # Lógica de visibilidad: Solo mostrar si es HaruNeko o requiere instalación real
        if category in ["traductor", "ai"] and tool["name"] != "HaruNeko":
            install_button.hide()

        if tool["name"] == "HaruNeko":
            self.install_button = install_button
            # Configurar el estado inicial del botón según si ya existe la carpeta
            hakuneko_path = os.path.join(os.getcwd(), "app_tools", "HaruNeko")
            if os.path.exists(hakuneko_path):
                install_button.setText("Instalado")
                install_button.setEnabled(False)
            else:
                install_button.clicked.connect(self.download_hakuneko)
        elif tool["name"] in ["Gemini", "Mistral"]:
            install_button.setEnabled(False)

        buttons_layout.addWidget(install_button, alignment=qt_any.AlignCenter)

        # Botón Usar
        use_button = QPushButton("Usar")
        use_button.setFixedSize(110, 32)  # Un poco más alto
        use_button.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #00c6ff, stop:1 #0072ff); /* Cian a Azul eléctrico */
                color: white;
                border: 1px solid #00ffff;
                border-radius: 16px; /* Pastilla completa */
                font-weight: 900;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #55d4ff, stop:1 #2b8cff);
                border: 1px solid white;
                box-shadow: 0 0 10px #00c6ff;
            }
            QPushButton:pressed {
                background-color: #005bb5;
                margin-top: 2px;
            }
            QPushButton:disabled {
                background: transparent;
                color: rgba(255, 255, 255, 0.2);
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
        """)  # Resetear estilo para usar Global pero con padding mínimo
        use_button.setObjectName(f"use_btn_{tool['name']}")
        if tool["name"] == "Gemini" and category == "ai":
            use_button.clicked.connect(
                lambda checked=False, cat=category: self._create_gemini_container(cat)
            )
        elif tool["name"] == "Mistral" and category == "ai":
            use_button.clicked.connect(
                lambda checked=False, cat=category: self._create_mistral_container(cat)
            )
        elif tool["name"] == "HaruNeko":
            use_button.clicked.connect(self.start_hakuneko)
        elif category == "traductor":
            input_field = tool_container.findChild(QLineEdit)
            output_field = tool_container.findChild(QTextEdit)
            if input_field and output_field:
                use_button.clicked.connect(
                    lambda: self._translate_text(input_field, output_field, tool)
                )

        buttons_layout.addWidget(use_button, alignment=qt_any.AlignCenter)
        right_side_layout.addWidget(buttons_container, alignment=qt_any.AlignCenter)

        tool_layout.addLayout(right_side_layout)
        return tool_container

    def _translate_text(
        self,
        input_container: QLineEdit,
        output_container: QTextEdit,
        tool: Dict[str, Any],
    ) -> Optional[TranslationTask]:
        """Traducción al texto usando todos los traductores.f"""
        use_button: Optional[QPushButton] = None
        try:
            input_text = input_container.text()
            if not input_text.strip():
                return None

            parent_w = input_container.parent()
            if parent_w:
                grand_parent = cast(QWidget, parent_w).parent()
                if grand_parent:
                    use_button = cast(QWidget, grand_parent).findChild(
                        QPushButton, f"use_btn_{tool['name']}"
                    )

                if use_button:
                    use_button.setEnabled(False)
                    use_button.setText("Traduciendo...")

                # Limpiar caché previo para este traductor al iniciar uno nuevo
                if tool["name"] in self.translation_cache:
                    del self.translation_cache[tool["name"]]

                output_container.clear()
                QApplication.processEvents()

                s_combo = self.source_combo
                t_combo = self.target_combo
                if not s_combo or not t_combo:
                    if use_button:
                        use_button.setEnabled(True)
                        use_button.setText("Usar")
                    return None

                task = TranslationTask(
                    tool,
                    input_text,
                    s_combo.currentData(),
                    t_combo.currentData(),
                )

                # Mantener viva la tarea para evitar "Signal source has been deleted"
                self._active_tasks.append(task)

                def on_finished(name: str, result: str, error: str):
                    # Eliminar de la lista de tareas activas al terminar
                    if task in self._active_tasks:
                        self._active_tasks.remove(task)
                    self._handle_translation_finish(
                        name, result, error, output_container, use_button
                    )

                # Conectar las señales del trabajador para manejar el resultado
                task.signals.finished.connect(on_finished)

                # Para la traducción global, conectamos aquí también ANTES de empezar
                handler: Optional[Callable[[str, str, str], None]] = (
                    self._current_global_handler
                )
                if handler is not None:
                    task.signals.finished.connect(handler)

                # Envía la tarea al pool de hilos para su ejecución
                QThreadPool.globalInstance().start(task)
                return task
            return None

        except Exception as e:
            error_msg = f"Error al iniciar la traducción: {str(e)}"
            QMessageBox.critical(self.app.content_container, "Error", error_msg)
            if use_button:
                use_button.setEnabled(True)
                use_button.setText("Usar")
            return None

    def _handle_translation_finish(
        self,
        name: str,
        result: str,
        error: str,
        output_container: QTextEdit,
        use_button: Optional[QPushButton],
    ):
        """Maneja el resultado de una traducción una vez que ha finalizado."""
        try:
            # Siempre guardamos el resultado en caché por si el widget se ha destruido
            if not error and result:
                self.translation_cache[name] = result

            # Verificar si los widgets aún existen antes de intentar usarlos
            if not shiboken6.isValid(output_container):
                logging.debug(
                    f"Traducción terminada para '{name}', pero el widget de destino ya no existe. El resultado se guardó en caché."
                )
                return

            if error:
                output_container.setText(f"Error en {name}: {error}")
            else:
                output_container.setText(result)

            if use_button and shiboken6.isValid(use_button):
                use_button.setEnabled(True)
                use_button.setText("USAR")

        except RuntimeError:
            # Captura "Internal C++ object already deleted"
            logging.warning(
                f"Intento de actualizar UI eliminada tras traducción de '{name}'."
            )
        except Exception as e:
            logging.error(f"Error al actualizar UI de traducción: {e}")

    def _handle_translation_result(
        self,
        tool_name: str,
        result: str,
        error: str,
        output_container: QTextEdit,
        use_button: QPushButton,
    ):
        try:
            use_button.setEnabled(True)
            use_button.setText("Usar")
            if error:
                if (
                    "Idioma escogido a traducir incompatible." in error
                    or "Error en Baidu: Función no certificada o inestable." in error
                    or "Papago no está operativo por el momento, se corregira en actualizaciones posteriores."
                    in error
                ):
                    output_container.setStyleSheet("color: red;")
                    output_container.setText(error)
                elif tool_name == "iTranslate" and "503" in error:
                    error_msg = (
                        "Error en iTranslate: Servicio no disponible\n"
                        "(El servidor está temporalmente fuera de línea)"
                    )
                    output_container.setText(error_msg)
                else:
                    output_container.setText(f"Error en {tool_name}: {error}")
            else:
                output_container.setStyleSheet("color: white;")
                output_container.setText(result)

            self.active_threads = [t for t in self.active_threads if t.isRunning()]
        except Exception as e:
            use_button.setEnabled(True)
            use_button.setText("Usar")
            output_container.setText(f"Error al mostrar resultados: {str(e)}")

    def _handle_critical_error(self, tool_name: str, error_msg: str):
        """Maneja errores que podrían cerrar la aplicación"""
        pass

    def _reset_user_prompt(self, prompt_edit: QTextEdit):
        try:
            with open(Config.AI_PROMPT, "r", encoding="utf-8") as f_default:
                default_prompt = f_default.read()
            with open(Config.AI_PROMPT_USER, "w", encoding="utf-8") as f_user:
                f_user.write(default_prompt)
            prompt_edit.setPlainText(default_prompt)
        except Exception as e:
            QMessageBox.critical(
                cast(QWidget, self.app),
                "Error",
                f"No se pudo restablecer el prompt: {e}",
            )

    def _save_user_prompt_to_file(self, prompt_text: str):
        try:
            with open(Config.AI_PROMPT_USER, "w", encoding="utf-8") as f:
                f.write(prompt_text)
        except Exception as e:
            QMessageBox.critical(
                cast(QWidget, self.app), "Error", f"No se pudo guardar el prompt: {e}"
            )

    def _create_gemini_container(self, category: str):
        """Crea el contenedor específico para Gemini."""
        # 1. Ocultar paneles de herramientas anteriores para evitar superposición
        if self.parent_container:
            self.parent_container.hide()

        # 2. Ocultar área de utilidades si está visible
        if self.utilities_area:
            if "scroll" in self.utilities_area:
                scroll_widget = cast(QWidget, self.utilities_area["scroll"])
                if scroll_widget:
                    scroll_widget.hide()
            if "footer" in self.utilities_area:
                footer_widget = cast(QWidget, self.utilities_area["footer"])
                if footer_widget:
                    footer_widget.hide()

        if self.gemini_container:
            self.gemini_container.show()
            self.gemini_container.raise_()
            return

        cast(Any, self.app)._hide_all_sections()
        self.gemini_container = QWidget(self.app.content_container)
        self.gemini_container.setObjectName("GeminiProcessorPanel")
        self.gemini_container.setGeometry(50, 50, 780, 500)
        self.gemini_container.setStyleSheet(
            """
            #GeminiProcessorPanel {
                background-color: rgba(0, 0, 0, 150);
                border: 1px solid rgba(150, 0, 150, 100);
                border-radius: 15px;
            }
            """
        )
        main_layout = QVBoxLayout(self.gemini_container)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
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
        main_layout.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignCenter)

        try:
            with open(Config.AI_PROMPT_USER, "r", encoding="utf-8") as f:
                prompt_text = f.read()
        except FileNotFoundError:
            prompt_text = "No se pudo encontrar el archivo ai_prompt_user.txt."

        prompt_edit = QTextEdit()
        prompt_edit.setPlainText(prompt_text)
        prompt_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        prompt_edit.setStyleSheet(
            """
            QTextEdit {
                font-size: 14px;
                color: white;
                background-color: rgba(0, 0, 0, 100);
                border: 1px solid #572364;
                border-radius: 8px;
                white-space: pre-wrap;
            }
        """
        )
        prompt_edit.setFont(self.app.roboto_black_font)
        main_layout.addWidget(prompt_edit)

        button_layout = QHBoxLayout()

        button_style = """
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

        copy_button = QPushButton("Copiar")
        copy_button.setStyleSheet(button_style)
        copy_button.setFont(self.app.adventure_font)
        copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(prompt_edit.toPlainText())
        )
        button_layout.addWidget(copy_button)

        paste_button = QPushButton("Pegar")
        paste_button.setStyleSheet(button_style)
        paste_button.setFont(self.app.adventure_font)
        paste_button.setCursor(Qt.CursorShape.PointingHandCursor)
        paste_button.clicked.connect(
            lambda: prompt_edit.insertPlainText(QApplication.clipboard().text())
        )
        button_layout.addWidget(paste_button)

        apply_button = QPushButton("Aplicar")
        apply_button.setStyleSheet(button_style)
        apply_button.setFont(self.app.adventure_font)
        apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_button.clicked.connect(
            lambda: self._save_user_prompt_to_file(prompt_edit.toPlainText())
        )
        button_layout.addWidget(apply_button)

        reset_button = QPushButton("Restablecer")
        reset_button.setStyleSheet(button_style)
        reset_button.setFont(self.app.adventure_font)
        reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_button.clicked.connect(lambda: self._reset_user_prompt(prompt_edit))
        button_layout.addWidget(reset_button)

        clear_button = QPushButton("Borrar")
        clear_button.setStyleSheet(button_style)
        clear_button.setFont(self.app.adventure_font)
        clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_button.clicked.connect(prompt_edit.clear)
        button_layout.addWidget(clear_button)

        main_layout.addLayout(button_layout)
        custom_section = QWidget()
        custom_section.setStyleSheet(
            """
            background-color: rgba(20, 20, 20, 100);
            border: 1px solid rgba(150, 0, 150, 50);
            border-radius: 10px;
        """
        )
        bottom_layout = QHBoxLayout(custom_section)
        bottom_layout.setContentsMargins(10, 10, 10, 10)
        bottom_layout.setSpacing(10)

        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)
        browse_files_button = QPushButton("Examinar Archivos")
        # ... (estilos y config de browse_files_button) ...
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
        browse_files_button.setCursor(Qt.CursorShape.PointingHandCursor)
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
        browse_folders_button.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_folders_button.clicked.connect(self._browse_folders)
        # New layout for browse buttons and config button
        file_config_layout = QVBoxLayout()
        file_config_layout.addWidget(browse_files_button)

        config_button = QPushButton("Configuración")
        config_button.setStyleSheet(
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
        config_button.setFont(self.app.adventure_font)
        config_button.setCursor(Qt.CursorShape.PointingHandCursor)
        config_button.clicked.connect(
            self._show_gemini_config_section
        )  # Connect to new method
        file_config_layout.addWidget(config_button)

        folder_save_layout = QVBoxLayout()
        folder_save_layout.addWidget(browse_folders_button)

        # Guardar referencias para deshabilitar botones según el modelo
        self.gemini_browse_files_button = browse_files_button
        self.gemini_browse_folders_button = browse_folders_button

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
        save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        save_button.clicked.connect(self._save_results)
        folder_save_layout.addWidget(save_button)

        # Combine the two new vertical layouts into a horizontal layout
        combined_browse_layout = QHBoxLayout()
        combined_browse_layout.addLayout(file_config_layout)
        combined_browse_layout.addLayout(folder_save_layout)
        left_layout.addLayout(combined_browse_layout)
        bottom_layout.addLayout(left_layout, stretch=2)

        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(10)
        button_style = """
        QPushButton {
            font-size: 18px;
            color: white;
            background-color: rgba(30, 30, 30, 200);
            border: 1px solid rgba(150, 0, 150, 100);
            border-radius: 8px;
            padding: 12px;
            min-width: 180px;
        }
        QPushButton:hover {
            background-color: rgba(50, 50, 50, 220);
            border: 1px solid rgba(200, 0, 200, 150);
        }
        QPushButton:disabled {
            background-color: rgba(20, 20, 20, 150);
            color: rgba(255, 255, 255, 50);
            border: 1px solid rgba(150, 0, 150, 20);
        }
        """
        self.gemini_start_button = QPushButton("Iniciar")  # Guardar referencia
        self.gemini_start_button.setStyleSheet(button_style)
        self.gemini_start_button.setFont(self.app.super_cartoon_font)
        self.gemini_start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gemini_start_button.clicked.connect(self._start_gemini_processing)
        right_layout.addWidget(
            self.gemini_start_button, alignment=Qt.AlignmentFlag.AlignCenter
        )

        self.gemini_cancel_button = QPushButton("Cancelar")  # Guardar referencia
        self.gemini_cancel_button.setStyleSheet(button_style)
        self.gemini_cancel_button.setFont(self.app.super_cartoon_font)
        self.gemini_cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gemini_cancel_button.clicked.connect(self._cancel_gemini_processing)
        self.gemini_cancel_button.setEnabled(False)  # Inicialmente deshabilitado
        right_layout.addWidget(
            self.gemini_cancel_button, alignment=Qt.AlignmentFlag.AlignCenter
        )
        bottom_layout.addLayout(right_layout, stretch=1)
        main_layout.addWidget(custom_section, stretch=0)

        # --- BARRA DE ESTADO DE PROCESO ---
        self.status_label = QLabel("Listo para procesar.")
        self.status_label.setObjectName("GeminiStatusLabel")
        self.status_label.setStyleSheet(
            """
            #GeminiStatusLabel {
                background-color: rgba(0, 0, 0, 100);
                color: #888888;
                border: 1px solid rgba(150, 0, 150, 30);
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
                font-family: "Roboto Black";
            }
            """
        )
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.hide()  # Oculto inicialmente
        main_layout.addWidget(self.status_label)

        # Actualizar estado inicial de botones según el modelo actual
        self.update_gemini_ui_state(Config.GEMINI_MODEL)

        self.gemini_container.show()
        self.gemini_container.raise_()  # Asegurar que esté en la parte superior
        QApplication.processEvents()  # Forzar actualización de la UI

    def _update_processing_status(self, message: str):
        """Actualiza la barra de estado con colores y sonido."""
        # MEDIDA DEFENSIVA BUG UI: Asegurar que el contenedor principal siga visible
        if (
            hasattr(self, "gemini_container")
            and self.gemini_container
            and not self.gemini_container.isVisible()
        ):
            self.gemini_container.show()
            self.gemini_container.raise_()

        if hasattr(self, "status_label") and self.status_label:
            self.status_label.show()
            self.status_label.setText(message)

            # Determinar estilo según el contenido del mensaje
            msg_lower = message.lower()
            if (
                "error" in msg_lower
                or "agotada" in msg_lower
                or "fallaron" in msg_lower
            ):
                # Rojo para errores críticos o agotamiento
                border_color = "#ff0000"
                text_color = "#ffcccc"
                QApplication.beep()  # Alerta sonora
            elif (
                "cambiando" in msg_lower
                or "rotando" in msg_lower
                or "pausa" in msg_lower
            ):
                # Naranja para acciones correctivas automáticas
                border_color = "#ff9900"
                text_color = "#ffeebb"
                QApplication.beep()  # Alerta sonora
            elif "éxito" in msg_lower or "completado" in msg_lower:
                # Verde para éxito
                border_color = "#00ff00"
                text_color = "#ccffcc"
            else:
                # Neutro para info general
                border_color = "rgba(150, 0, 150, 50)"
                text_color = "#cccccc"

            self.status_label.setStyleSheet(
                f"""
                #GeminiStatusLabel {{
                    background-color: rgba(0, 0, 0, 180);
                    color: {text_color};
                    border: 2px solid {border_color};
                    border-radius: 6px;
                    padding: 8px;
                    font-size: 14px;
                    font-weight: bold;
                    font-family: "Roboto Black";
                }}
                """
            )
            QApplication.processEvents()

    def update_gemini_ui_state(self, model_name: str):
        """Habilita o deshabilita botones según el modelo seleccionado."""
        is_image_model = "image" in model_name.lower()

        if (
            hasattr(self, "gemini_browse_folders_button")
            and self.gemini_browse_folders_button
        ):
            self.gemini_browse_folders_button.setEnabled(not is_image_model)
            if is_image_model:
                self.gemini_browse_folders_button.setToolTip(
                    "Los modelos 'image' solo soportan procesamiento de archivos individuales."
                )
            else:
                self.gemini_browse_folders_button.setToolTip("")

    def _create_mistral_container(self, category: str):
        """Crea el contenedor específico para Mistral."""
        if self.mistral_container:
            self.mistral_container.show()
            return

        cast(Any, self.app)._hide_all_sections()
        self.mistral_container = QWidget(self.app.content_container)
        self.mistral_container.setObjectName("MistralProcessorPanel")
        self.mistral_container.setGeometry(50, 50, 780, 500)
        self.mistral_container.setStyleSheet(
            """
            #MistralProcessorPanel {
                background-color: rgba(0, 0, 0, 150);
                border: 1px solid rgba(150, 0, 150, 100);
                border-radius: 15px;
            }
            """
        )
        main_layout = QVBoxLayout(self.mistral_container)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
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
        main_layout.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignCenter)

        try:
            with open(Config.AI_PROMPT_USER, "r", encoding="utf-8") as f:
                prompt_text = f.read()
        except FileNotFoundError:
            prompt_text = "No se pudo encontrar el archivo ai_prompt_user.txt."

        prompt_edit = QTextEdit()
        prompt_edit.setPlainText(prompt_text)
        prompt_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        prompt_edit.setStyleSheet(
            """
            QTextEdit {
                font-size: 14px;
                color: white;
                background-color: rgba(0, 0, 0, 100);
                border: 1px solid #572364;
                border-radius: 8px;
                white-space: pre-wrap;
            }
        """
        )
        prompt_edit.setFont(self.app.roboto_black_font)
        main_layout.addWidget(prompt_edit)

        button_layout = QHBoxLayout()

        button_style = """
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

        copy_button = QPushButton("Copiar")
        copy_button.setStyleSheet(button_style)
        copy_button.setFont(self.app.adventure_font)
        copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(prompt_edit.toPlainText())
        )
        button_layout.addWidget(copy_button)

        paste_button = QPushButton("Pegar")
        paste_button.setStyleSheet(button_style)
        paste_button.setFont(self.app.adventure_font)
        paste_button.setCursor(Qt.CursorShape.PointingHandCursor)
        paste_button.clicked.connect(
            lambda: prompt_edit.insertPlainText(QApplication.clipboard().text())
        )
        button_layout.addWidget(paste_button)

        apply_button = QPushButton("Aplicar")
        apply_button.setStyleSheet(button_style)
        apply_button.setFont(self.app.adventure_font)
        apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_button.clicked.connect(
            lambda: self._save_user_prompt_to_file(prompt_edit.toPlainText())
        )
        button_layout.addWidget(apply_button)

        reset_button = QPushButton("Restablecer")
        reset_button.setStyleSheet(button_style)
        reset_button.setFont(self.app.adventure_font)
        reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_button.clicked.connect(lambda: self._reset_user_prompt(prompt_edit))
        button_layout.addWidget(reset_button)

        clear_button = QPushButton("Borrar")
        clear_button.setStyleSheet(button_style)
        clear_button.setFont(self.app.adventure_font)
        clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_button.clicked.connect(prompt_edit.clear)
        button_layout.addWidget(clear_button)

        main_layout.addLayout(button_layout)
        custom_section = QWidget()
        custom_section.setStyleSheet(
            """
            background-color: rgba(20, 20, 20, 100);
            border: 1px solid rgba(150, 0, 150, 50);
            border-radius: 10px;
        """
        )
        bottom_layout = QHBoxLayout(custom_section)
        bottom_layout.setContentsMargins(10, 10, 10, 10)
        bottom_layout.setSpacing(10)

        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)
        browse_files_button = QPushButton("Examinar Archivos")
        # ... (estilos y config de browse_files_button) ...
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
        browse_files_button.setCursor(Qt.CursorShape.PointingHandCursor)
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
        browse_folders_button.setCursor(Qt.CursorShape.PointingHandCursor)
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
        save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        save_button.clicked.connect(self._save_results)
        left_layout.addWidget(save_button)
        bottom_layout.addLayout(left_layout, stretch=2)

        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(10)
        button_style = """
        QPushButton {
            font-size: 18px;
            color: white;
            background-color: rgba(30, 30, 30, 200);
            border: 1px solid rgba(150, 0, 150, 100);
            border-radius: 8px;
            padding: 12px;
            min-width: 180px;
        }
        QPushButton:hover {
            background-color: rgba(50, 50, 50, 220);
            border: 1px solid rgba(200, 0, 200, 150);
        }
        QPushButton:disabled {
            background-color: rgba(20, 20, 20, 150);
            color: rgba(255, 255, 255, 50);
            border: 1px solid rgba(150, 0, 150, 20);
        }
        """
        self.mistral_start_button = QPushButton(
            "Iniciar Procesamiento"
        )  # Guardar referencia
        self.mistral_start_button.setStyleSheet(button_style)
        self.mistral_start_button.setFont(self.app.super_cartoon_font)
        self.mistral_start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mistral_start_button.clicked.connect(self._start_mistral_processing)
        right_layout.addWidget(
            self.mistral_start_button, alignment=Qt.AlignmentFlag.AlignCenter
        )

        self.mistral_cancel_button = QPushButton("Cancelar")  # Guardar referencia
        self.mistral_cancel_button.setStyleSheet(button_style)
        self.mistral_cancel_button.setFont(self.app.super_cartoon_font)
        self.mistral_cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mistral_cancel_button.clicked.connect(self._cancel_mistral_processing)
        self.mistral_cancel_button.setEnabled(False)  # Inicialmente deshabilitado
        right_layout.addWidget(
            self.mistral_cancel_button, alignment=Qt.AlignmentFlag.AlignCenter
        )
        bottom_layout.addLayout(right_layout, stretch=1)
        main_layout.addWidget(custom_section, stretch=0)
        self.mistral_container.show()

    def _browse_folders(self):
        """Abre un cuadro de diálogo para seleccionar carpetas."""
        folder_dialog = QFileDialog()
        folder_dialog.setFileMode(QFileDialog.FileMode.Directory)
        folder_dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        folder_path = folder_dialog.getExistingDirectory(
            self.app, "Seleccionar Carpeta", QDir.homePath()
        )
        if folder_path:
            self.input_path = folder_path

    def _browse_files(self):
        """Abre un cuadro de diálogo para seleccionar archivos o carpetas."""
        # Verificar si es un modelo de imagen para limitar la selección
        is_image_model = "image" in Config.GEMINI_MODEL.lower()

        file_dialog = QFileDialog()
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        file_dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        file_paths, _ = file_dialog.getOpenFileNames(
            self.app, "Seleccionar Archivos", QDir.homePath()
        )
        if file_paths:
            if is_image_model and len(file_paths) > 1:
                QMessageBox.warning(
                    self.app,
                    "Limitación de Modelo",
                    "Los modelos especialistas en IMAGEN están diseñados para una sola página a la vez (páginas ultra difíciles).\n\nSe ha seleccionado solo el primer archivo.",
                )
                self.selected_files_for_processing = [file_paths[0]]
            else:
                self.selected_files_for_processing = file_paths

            self.input_path = (
                None  # Indicate that a list of files is selected, not a single path
            )

    def _save_results(self):
        """Guarda los resultados generados por Mistral seleccionando una carpeta de destino."""
        folder_path = QFileDialog.getExistingDirectory(
            self.app, "Seleccionar Carpeta de Destino", QDir.homePath()
        )
        if folder_path:
            self.output_directory = folder_path

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

    def on_download_finished(self, success: bool):
        """Maneja la finalización de la descarga actualizando estado y UI según resultado."""
        self.download_in_progress = False
        self.download_state = "finished" if success else "idle"
        if success:
            self.haruneko_installed = True
            self.haruneko_version = self.haruneko_manager.get_current_version()
            if self.install_button is not None:
                self.install_button.setText("Eliminar")
                self.install_button.clicked.disconnect()
                self.install_button.clicked.connect(self.uninstall_hakuneko)
            QMessageBox.information(
                self.app,
                "Descarga Completa",
                "Hakuneko ha sido descargado y descomprimido.",
            )
        else:
            if self.install_button is not None:
                self.install_button.setText("Descargar")
            QMessageBox.critical(self.app, "Error", "No se pudo descargar Hakuneko.")
        if self.install_button is not None:
            self.install_button.setEnabled(True)

    def on_download_error(self, error_msg: str):
        """Actualiza el estado y UI ante errores en el proceso de descarga."""
        self.download_in_progress = False
        self.download_state = "error"
        QMessageBox.critical(
            self.app, "Error", f"Error durante la descarga: {error_msg}"
        )
        if self.install_button is not None:
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
                if self.install_button is not None:
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
            and self.install_button is not None
        ):
            self.install_button.setText("Actualizar")
            self.install_button.clicked.disconnect()
            self.install_button.clicked.connect(self.update_hakuneko)

    def install_hakuneko(self):
        """Inicia la descarga e instalación de Hakuneko."""
        self.download_hakuneko()

    def _start_gemini_processing(self, retry_from_mistral: bool = False):
        """Inicia Gemini en segundo plano usando Worker y ThreadPool."""
        try:
            if not self.output_directory:
                raise ValueError("La ruta de salida debe estar configurada.")

            self.gemini_start_button.setEnabled(False)
            self.gemini_cancel_button.setEnabled(True)
            self.cancel_event = threading.Event()

            # Callback para actualizaciones de estado (thread-safe gracias a señal)
            def status_updater(message: str):
                self.status_update_signal.emit(message)

            self.gemini_processor.set_status_callback(status_updater)

            # Función que se ejecutará en segundo plano
            def processing_task():
                # Wrapper para capturar finalización y pasarla como resultado del Worker
                # En lugar de llamar directamente a _handle_processing_finished (que toca UI),
                # devolvemos el estado para que la señal 'result' lo maneje en el hilo principal.
                final_status = "unknown"
                final_error = None

                # Evento local para esperar a que termine el procesamiento interno
                processing_done = threading.Event()

                def on_finished_callback(status: str, error_msg: Optional[str] = None):
                    nonlocal final_status, final_error
                    final_status = status
                    final_error = error_msg
                    processing_done.set()

                try:
                    if (
                        hasattr(self, "selected_files_for_processing")
                        and self.selected_files_for_processing
                    ):
                        self._process_selected_files_gemini(
                            self.selected_files_for_processing,
                            str(self.output_directory),
                            self.cancel_event,
                            on_finished_callback,
                        )
                        self.selected_files_for_processing = []
                    elif self.input_path:
                        cast(Any, self.gemini_processor).start_processing_in_background(
                            self.input_path,
                            str(self.output_directory),
                            self.cancel_event,
                            callback=on_finished_callback,
                        )
                    else:
                        raise ValueError("Rutas no configuradas.")

                    # Esperar a que el callback se ejecute (sincronizar el hilo worker con el proceso interno)
                    # Esto es necesario porque start_processing_in_background podría lanzar sus propios hilos
                    # si no está diseñado para bloquear. Asumimos que bloquea o usa el callback.
                    # Si start_processing_in_background NO bloquea, necesitaremos esperar al evento.
                    processing_done.wait()
                    return (final_status, final_error)

                except GeminiAPIError as e:
                    if retry_from_mistral:
                        return ("error", f"Ambos modelos fallaron: {e}")
                    else:
                        return ("error_gemini_api", str(e))
                except Exception as e:
                    return ("error", str(e))

            # Configurar Worker
            worker = Worker(processing_task)

            # Conectar resultado (se ejecuta en hilo principal)
            def handle_result(result):
                status, error = result
                self._handle_processing_finished(status, error)

            worker.signals.result.connect(handle_result)

            # Conectar errores no capturados
            def handle_error(err_tuple):
                _, value, _ = err_tuple
                self._handle_processing_finished("error", str(value))

            worker.signals.error.connect(handle_error)

            QThreadPool.globalInstance().start(worker)

            QMessageBox.information(
                self.app,
                "Procesamiento iniciado",
                "El procesamiento se ha iniciado. Puedes cancelarlo en cualquier momento.",
            )

        except ValueError as e:
            QMessageBox.critical(
                self.app, "Error", f"Error durante el procesamiento: {e}"
            )
            if hasattr(self, "gemini_start_button"):
                self.gemini_start_button.setEnabled(True)
            if hasattr(self, "gemini_cancel_button"):
                self.gemini_cancel_button.setEnabled(False)

    def _cancel_gemini_processing(self):
        """Cancela el procesamiento en curso de Gemini."""
        if hasattr(self, "cancel_event") and self.cancel_event:
            self.cancel_event.set()
            self.gemini_cancel_button.setEnabled(
                False
            )  # Deshabilitar botón de cancelación
            QMessageBox.information(
                self.app,
                "Cancelación Solicitada",
                "Se ha solicitado la cancelación. El proceso se detendrá lo antes posible.",
            )
        else:
            QMessageBox.warning(
                self.app,
                "Sin Proceso",
                "No hay ningún procesamiento en curso para cancelar.",
            )

    def _start_mistral_processing(self, retry_from_gemini: bool = False):
        """Inicia Mistral en segundo plano validando rutas y manejando cancelaciones."""
        try:
            if not self.output_directory:  # output_directory is always required
                raise ValueError("La ruta de salida debe estar configurada.")

            # Deshabilitar botón de inicio y habilitar botón de cancelación
            if self.mistral_start_button:
                self.mistral_start_button.setEnabled(False)
            if self.mistral_cancel_button:
                self.mistral_cancel_button.setEnabled(True)
            QApplication.processEvents()  # Actualizar UI inmediatamente

            self.cancel_event = threading.Event()

            def processing_target_mistral():
                try:

                    def on_finished(status: str, error_msg: Optional[str] = None):
                        self._handle_processing_finished(status, error_msg)

                    if (
                        hasattr(self, "selected_files_for_processing")
                        and self.selected_files_for_processing
                    ):
                        # Process selected files
                        self._process_selected_files_mistral(
                            self.selected_files_for_processing,
                            str(self.output_directory),
                            cast(threading.Event, self.cancel_event),
                            on_finished,
                        )
                        # Clear selected files after starting processing
                        self.selected_files_for_processing = []
                    elif self.input_path:
                        # Process input directory
                        cast(
                            Any, self.mistral_processor
                        ).start_processing_in_background(
                            self.input_path,
                            str(self.output_directory),
                            cast(threading.Event, self.cancel_event),
                            callback=on_finished,
                        )
                    else:
                        raise ValueError(
                            "Las rutas de entrada o archivos seleccionados deben estar configurados."
                        )
                except MistralAPIError as e:
                    if retry_from_gemini:
                        # If already retrying from Gemini and Mistral also fails, just show error and stop
                        self._handle_processing_finished(
                            "error", f"Ambos modelos (Gemini y Mistral) fallaron: {e}"
                        )
                    else:
                        self._handle_processing_finished("error_mistral_api", str(e))
                except Exception as e:
                    self._handle_processing_finished("error", str(e))

            self.processing_thread = threading.Thread(target=processing_target_mistral)
            self.processing_thread.start()

            QMessageBox.information(
                cast(QWidget, self.app),
                "Procesamiento iniciado",
                "El procesamiento se ha iniciado. Puedes cancelarlo en cualquier momento.",
            )
            # Mover el QApplication.processEvents() aquí para actualizar UI
            QApplication.processEvents()
        except ValueError as e:
            QMessageBox.critical(
                cast(QWidget, self.app), "Error", f"Error durante el procesamiento: {e}"
            )
            # Asegurar reset de botones en caso de error
            if hasattr(self, "mistral_start_button") and self.mistral_start_button:
                self.mistral_start_button.setEnabled(True)
            if hasattr(self, "mistral_cancel_button") and self.mistral_cancel_button:
                self.mistral_cancel_button.setEnabled(False)

    def _process_selected_files_mistral(
        self,
        file_paths: List[str],
        output_dir: str,
        cancel_event: threading.Event,
        callback: Optional[Callable[[str, Optional[str]], None]] = None,
    ):
        """Procesa una lista de archivos seleccionados para Mistral."""
        success_status = "success"
        error_details = ""
        try:
            for file_path in file_paths:
                if cancel_event.is_set():
                    success_status = "cancelled"
                    break
                # Determine input_base for each file (its parent directory)
                input_base = os.path.dirname(file_path)
                content = self.mistral_processor.process_file(
                    file_path, output_dir, input_base
                )
                if (
                    not content
                ):  # If process_file returns empty string, it indicates an error
                    success_status = "error"
                    error_details = (
                        f"Fallo al procesar archivo: {os.path.basename(file_path)}"
                    )
                    break
        except Exception as e:
            logging.error(
                f"Error procesando archivos seleccionados para Mistral: {str(e)}"
            )
            success_status = "error"
            error_details = str(e)
        finally:
            if callback:
                if success_status == "cancelled":
                    callback("cancelled", None)
                elif success_status == "success":
                    callback("success", None)
                else:
                    callback(
                        "error",
                        error_details
                        or "Error desconocido durante el procesamiento de Mistral.",
                    )

    def _restore_gemini_title(self, label: QLabel):
        pass

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

    def _handle_processing_finished(
        self, status: str, error_message: Optional[str] = None
    ):
        """Envía el resultado a través de la señal (seguro para hilos)"""
        self.processing_finished.emit(status, error_message or "")

    def show_completion_message(self, status: str, error_message: Optional[str] = None):
        """Muestra el mensaje de finalización basado en el estado."""
        if status == "success":
            title = "Proceso Completado"
            message = "Procesamiento finalizado exitosamente!"
            icon = QMessageBox.Icon.Information
        elif status == "cancelled":
            title = "Proceso Cancelado"
            message = "El procesamiento ha sido cancelado por el usuario."
            icon = QMessageBox.Icon.Warning
        elif status == "error_gemini_api":
            title = "Error de Gemini API"
            message = f"La API de Gemini reportó un error crítico y se agotaron los reintentos/keys:\n\n{error_message}"
            icon = QMessageBox.Icon.Critical
        elif status == "error_mistral_api":
            title = "Error de Mistral API"
            message = f"La API de Mistral falló:\n\n{error_message}"
            icon = QMessageBox.Icon.Critical
        else:  # status == "error"
            title = "Error General"

            # Detectar si el status en sí mismo trae el mensaje de error
            if status.startswith("Error:"):
                # Extraer el mensaje del status
                msg_from_status = status.split("Error:", 1)[1].strip()
                message = "Ocurrió un error durante el procesamiento."
                message += f"\n\nDetalles: {msg_from_status}"
            else:
                message = "Ocurrió un error inesperado durante el procesamiento."
                if error_message:
                    message += f"\n\nDetalles: {error_message}"

            icon = QMessageBox.Icon.Critical

        msg_box = QMessageBox(cast(QWidget, self.app))
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.setIcon(icon)
        msg_box.exec()

        # Limpiar la barra de estado al finalizar
        if hasattr(self, "status_label") and self.status_label:
            if status == "success":
                self.status_label.setText("Listo para procesar.")
                self.status_label.setStyleSheet(
                    "background-color: rgba(0, 0, 0, 100); color: #888888; border: 1px solid rgba(150, 0, 150, 30); border-radius: 6px; padding: 10px; font-size: 14px;"
                )
                # Ocultar después de 10 segundos para limpieza visual
                from PySide6.QtCore import QTimer

                QTimer.singleShot(10000, self.status_label.hide)
            else:
                # Si hubo error o cancelación, dejar el mensaje visible para que el usuario pueda leerlo con calma
                pass

        # RESET BUTTON STATES - ADDED FIX
        if hasattr(self, "gemini_start_button"):
            self.gemini_start_button.setEnabled(True)
        if hasattr(self, "gemini_cancel_button"):
            self.gemini_cancel_button.setEnabled(False)

        if hasattr(self, "mistral_start_button"):
            self.mistral_start_button.setEnabled(True)
        if hasattr(self, "mistral_cancel_button"):
            self.mistral_cancel_button.setEnabled(False)

    def _show_model_switch_dialog(self, error_status: str, error_message: str):
        """
        Muestra un diálogo al usuario cuando se produce un error de API,
        ofreciendo la opción de cambiar de modelo o cancelar.
        """
        current_model_type = (
            "Gemini" if error_status == "error_gemini_api" else "Mistral"
        )

        msg_box = QMessageBox(cast(QWidget, self.app))
        msg_box.setWindowTitle(f"Error de {current_model_type} API")
        msg_box.setText(
            f"Se produjo un error con la API de {current_model_type}:\n\n{error_message}\n\n¿Deseas intentar con el otro modelo o cancelar el procesamiento?"
        )

        switch_button = msg_box.addButton(
            "Cambiar a otro modelo", QMessageBox.ButtonRole.AcceptRole
        )
        cancel_button = msg_box.addButton(
            "Cancelar procesamiento", QMessageBox.ButtonRole.RejectRole
        )

        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.exec()

        if msg_box.clickedButton() == switch_button:
            self._switch_model_and_retry(current_model_type)
        elif msg_box.clickedButton() == cancel_button:
            self._reset_processing_buttons()
            QMessageBox.information(
                cast(QWidget, self.app),
                "Proceso Cancelado",
                "El procesamiento ha sido cancelado.",
            )

    def _switch_model_and_retry(self, failed_model_type: str):
        """
        Cambia al modelo alternativo y reintenta el procesamiento.
        """
        new_model_type = "Desconocido"  # Inicialización por defecto
        if failed_model_type == "Gemini":
            new_model_type = "Mistral"
            # Set Mistral as the current model in Config (if applicable)
            # This part needs to be handled carefully, as Config.GEMINI_MODEL is for Gemini
            # and MistralProcessor uses its own self.model.
            # For now, we'll just call the Mistral processing directly.
            self._start_mistral_processing(
                retry_from_gemini=True
            )  # Pass a flag to indicate retry
        elif failed_model_type == "Mistral":
            new_model_type = "Gemini"
            self._start_gemini_processing(
                retry_from_mistral=True
            )  # Pass a flag to indicate retry

        QMessageBox.information(
            cast(QWidget, self.app),
            "Cambiando Modelo",
            f"Intentando procesar con {new_model_type}...",
        )

    def _reset_processing_buttons(self):
        """Resetea el estado de los botones de procesamiento."""
        if hasattr(self, "gemini_start_button"):
            self.gemini_start_button.setEnabled(True)
        if hasattr(self, "gemini_cancel_button"):
            self.gemini_cancel_button.setEnabled(False)

        if hasattr(self, "mistral_start_button"):
            self.mistral_start_button.setEnabled(True)
        if hasattr(self, "mistral_cancel_button"):
            self.mistral_cancel_button.setEnabled(False)

    def on_gemini_config_closed(self):
        """Muestra el contenedor de Gemini cuando la configuración se cierra."""
        if hasattr(self, "gemini_container") and self.gemini_container:
            self.gemini_container.show()
            self.gemini_container.raise_()
            QApplication.processEvents()  # Forzar actualización de la UI
            parent = self.gemini_container.parentWidget()
            if parent:
                parent.raise_()
                QApplication.processEvents()

    def _show_gemini_config_section(self):
        """Muestra la sección de configuración de Gemini."""
        self.app.show_gemini_configuration()

    def _process_selected_files_gemini(
        self,
        file_paths: List[str],
        output_dir: Optional[str],
        cancel_event: threading.Event,
        callback: Optional[Callable[[str, Optional[str]], None]] = None,
    ):
        """Procesa una lista de archivos seleccionados delegando en el procesador especializado."""
        if not output_dir:
            if callback:
                callback("error", "No se ha configurado el directorio de salida.")
            return

        # Delegar en el método de GeminiProcessor que ya tiene lógica de lotes y guardado
        try:
            self.gemini_processor.process_selected_files_gemini(
                file_paths, output_dir, cancel_event, callback
            )
        except Exception as e:
            logging.error(f"Error delegando procesamiento Gemini: {e}")
            if callback:
                callback("error", str(e))

    def open_path_for_prompt(self, text_box: QLineEdit):
        """Abre un cuadro de diálogo para seleccionar un archivo .txt y actualiza la ruta."""
        desktop_path = os.path.join(os.getenv("USERPROFILE", ""), "Desktop")
        file_dialog = QFileDialog()
        file_dialog.setDirectory(desktop_path)
        selected_path, _ = file_dialog.getOpenFileName(
            cast(QWidget, self.app),
            "Seleccionar archivo",
            desktop_path,
            "Archivos de texto (*.txt)",
        )
        if selected_path:
            text_box.setText(selected_path)
            if "Gemini" in text_box.objectName():
                cast(Any, Config).GEMINI_PROMPT = selected_path
            elif "Mistral" in text_box.objectName():
                cast(Any, Config).MISTRAL_PROMPT = selected_path

    def open_expanded_editor(
        self, target_widget: Union[QLineEdit, QTextEdit], title: str
    ):
        """
        Abre el ExpandedTextEditorDialog para el widget objetivo proporcionado.
        """
        initial_text = ""
        if isinstance(target_widget, QLineEdit):
            initial_text = target_widget.text()
        else:  # target_widget debe ser QTextEdit si no es QLineEdit
            initial_text = target_widget.toPlainText()

        dialog = ExpandedTextEditorDialog(
            parent=cast(QWidget, self.app.content_container),
            initial_text=initial_text,
            title=title,
            target_widget=target_widget,
        )
        dialog.exec()

    def _create_scroll_area(self, section_type: str) -> QScrollArea:
        """Crea un área de desplazamiento moderna para herramientas o proyectos."""
        scroll_area = QScrollArea(self.app.content_container)
        scroll_area.setGeometry(50, 50, 780, 500)
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet(
            """
            QScrollArea {
                background-color: rgba(0, 0, 0, 150);
                border: 1px solid rgba(150, 0, 150, 80);
                border-radius: 15px;
            }
            QScrollBar:vertical {
                background: rgba(0, 0, 0, 50);
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: rgba(150, 0, 150, 100);
                border-radius: 5px;
            }
            """
        )
        image_container = QWidget()
        image_container.setStyleSheet("background-color: transparent; border: none;")
        image_layout = QGridLayout(image_container)
        qt_any = cast(Any, Qt)
        image_layout.setAlignment(qt_any.AlignLeft | qt_any.AlignTop)
        image_layout.setSpacing(4)

        folder: Optional[str] = None
        size: Tuple[int, int] = (122, 122)
        cols: int = 6

        if section_type == "utilities":
            folder = Config.GENERAL_TOOLS_FOLDER
            size = (122, 122)
            cols = 6
        elif section_type == "projects":
            folder = None
            size = (141, 212)
            cols = 5

        if folder and os.path.exists(folder):
            # Usamos el método que ahora es público en project_manager
            self.app.project_manager.load_images_with_descriptions(
                image_layout, folder, size, cols
            )
        scroll_area.setWidget(image_container)
        image_container.adjustSize()
        if image_container.sizeHint().height() <= scroll_area.viewport().height():
            scroll_area.setVerticalScrollBarPolicy(qt_any.ScrollBarAlwaysOff)
        else:
            scroll_area.setVerticalScrollBarPolicy(qt_any.ScrollBarAsNeeded)
        return scroll_area

    def _create_footer_text(self, section_type: str) -> QLabel:
        """Crea el texto del pie de página para cada sección."""
        qt_any = cast(Any, Qt)
        footer_label = QLabel(cast(QWidget, self.app.content_container))
        footer_label.setText(
            Config.UTILITIES_FOOTER_TEXT
            if section_type == "utilities"
            else Config.PROJECTS_FOOTER_TEXT
        )
        footer_label.setStyleSheet(
            "font-size: 18px;color: white;background: none;padding: 10px;"
        )
        footer_label.setFont(self.app.super_cartoon_font)
        footer_label.setAlignment(qt_any.AlignCenter)
        footer_label.setGeometry(50, 500, 800, 150)
        return footer_label
