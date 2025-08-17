"""
Módulo principal para la aplicación Babylon Scanlation.
Proporciona una interfaz gráfica para acceder a diversas herramientas y gestionar proyectos.
"""

# bibliotecas nativas
import os
import sys
import traceback
import webbrowser

# bibliotecas no nativas
import cv2
import requests
import time
from PyQt5.QtCore import QSharedMemory

# pylint: disable=no-name-in-module
from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QFont, QFontDatabase, QIcon, QImage, QPixmap
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer, QMediaPlaylist
from PyQt5.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# módulos (no borrar)
from project_manager import ProjectManager
from tools import ToolsManager, resize_image
from config import Config, resource_path, global_exception_handler
from options_menu import OptionsMenu


sys.excepthook = global_exception_handler


class ClickableThumbnail(QLabel):  # pylint: disable=too-few-public-methods
    """QLabel personalizado que emite una señal al hacer clic."""

    clicked = pyqtSignal(str)

    def __init__(self, video_id: str, parent=None):
        super().__init__(parent)
        self.video_id = video_id
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):  # pylint: disable=invalid-name
        """Maneja el clic y emite la señal."""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.video_id)
        super().mousePressEvent(event)


# pylint: disable=too-many-instance-attributes, too-many-lines
class App(QMainWindow):
    """Clase principal de la aplicación que maneja la interfaz gráfica."""

    def __init__(self):
        """Iniciador de funciones."""
        super().__init__()
        self.menu_container = None
        self.content_container = None
        self.home_label = None
        self.utilities_area = None
        self.projects_area = None
        self.help_area = None
        self.about_area = None
        self.custom_fonts = []
        self.super_cartoon_font = QFont("Arial")
        self.adventure_font = QFont("Arial")
        self.roboto_black_font = QFont("Arial")
        self.options_menu = OptionsMenu(self)
        self.project_manager = ProjectManager(Config.USER_DATA_DIR)
        self.tools_manager = ToolsManager(self)
        self.cap = None
        self.timer = QTimer(self)
        self.video_label = QLabel(self)
        self.temp_files = []
        self.audio_player = None
        self._setup_main_window()
        resize_image(Config.LOGO_PATH, 336, 150)
        self._load_fonts()
        self._create_layout()
        self._setup_audio()

    def _setup_main_window(self):
        """Configura la ventana principal con video o imagen de fondo."""
        self.setWindowTitle(Config.WINDOW_TITLE)
        self.setFixedSize(*Config.WINDOW_SIZE)
        self.setWindowIcon(QIcon(Config.ICON_PATH))
        self.background_label = QLabel(self)
        self.background_label.setPixmap(QPixmap(Config.BACKGROUND_PATH))
        self.background_label.setScaledContents(True)
        self.background_label.setGeometry(0, 0, *Config.WINDOW_SIZE)
        self._setup_opencv_video()
        self.container = QWidget(self)
        self.container.setGeometry(0, 0, *Config.WINDOW_SIZE)
        self.container.raise_()

    def _setup_audio(self):
        """Configura el reproductor de audio en bucle."""
        if os.path.exists(Config.AUDIO_PATH):
            try:
                self.audio_player = QMediaPlayer()
                self.playlist = QMediaPlaylist()
                self.playlist.addMedia(
                    QMediaContent(QUrl.fromLocalFile(Config.AUDIO_PATH))
                )
                self.playlist.setPlaybackMode(QMediaPlaylist.Loop)
                self.audio_player.setPlaylist(self.playlist)
                self.audio_player.setVolume(5)
                self.audio_player.play()
            except (IOError, OSError):
                pass
        else:
            pass

    def _initialize_video_capture(self):
        """Inicializa o detiene el vídeo según elección del usuario."""
        try:
            self.cap = cv2.VideoCapture(Config.VIDEO_PATH)  # pylint: disable=no-member
            if not self.cap.isOpened():
                raise ValueError("No se pudo abrir el archivo de video")
            return True
        except (IOError, OSError, ValueError):
            return False

    def _setup_opencv_video(self):
        """Inicializa el vídeo."""
        if not self._initialize_video_capture():
            self.background_label.show()
            return
        self.video_label.setGeometry(0, 0, *Config.WINDOW_SIZE)
        self.timer.timeout.connect(self._update_frame)
        fps = self.cap.get(cv2.CAP_PROP_FPS)  # pylint: disable=no-member
        self.timer.start(int(1000 / fps))
        self.background_label.hide()

    def _update_frame(self):
        """Actualiza el frame del video."""
        try:
            ret, frame = self.cap.read()
            if ret:
                frame = cv2.cvtColor(  # pylint: disable=no-member
                    frame, cv2.COLOR_BGR2RGB  # pylint: disable=no-member
                )
                frame = cv2.resize(  # pylint: disable=no-member
                    frame, Config.WINDOW_SIZE
                )
                h, w, ch = frame.shape
                bytes_per_line = ch * w
                q_img = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(q_img)
                self.video_label.setPixmap(pixmap)
            else:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # pylint: disable=no-member
        except (IOError, cv2.error):  # pylint: disable=catching-non-exception
            self.timer.stop()
            if self.cap is not None:
                self.cap.release()
                self.background_label.show()

    def closeEvent(self, event):  # pylint: disable=invalid-name, unused-argument
        """Liberar recursos al cerrar la aplicación."""
        if hasattr(self, "audio_player"):
            self.audio_player.stop()
            self.audio_player = None
        if hasattr(self, "cap"):
            if self.cap.isOpened():
                self.cap.release()
            self.timer.stop()
        self._clear_temp_files()
        event.accept()

    def _load_fonts(self):
        """Carga las fuentes personalizadas."""
        self.custom_fonts = self._load_custom_fonts()
        self._set_custom_fonts()
        self._load_roboto_font()

    def _load_custom_fonts(self):
        """Load custom fonts from specified paths."""
        custom_fonts = []
        for font_path in Config.FONT_PATHS:
            if os.path.exists(font_path):
                font_id = QFontDatabase.addApplicationFont(font_path)
                if font_id != -1:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    if families:
                        custom_fonts.append(QFont(families[0]))
        return custom_fonts

    def _set_custom_fonts(self):
        """Set custom fonts if available."""
        if self.custom_fonts:
            self.super_cartoon_font = self.custom_fonts[0]
            if len(self.custom_fonts) > 1:
                self.adventure_font = self.custom_fonts[1]

    def _load_roboto_font(self):
        """Load the Roboto font."""
        roboto_path = resource_path(os.path.join(Config.FONT_DIR, "RobotoBlack.ttf"))
        roboto_id = QFontDatabase.addApplicationFont(roboto_path)
        if roboto_id != -1:
            roboto_family = QFontDatabase.applicationFontFamilies(roboto_id)[0]
            self.roboto_black_font = QFont(roboto_family)
            self.roboto_black_font.setWeight(QFont.Black)
            self.roboto_black_font.setPointSize(10)

    def _create_layout(self):
        """Crea los componentes principales del layout."""
        self.container = QWidget(self)
        self.container.setGeometry(0, 0, *Config.WINDOW_SIZE)
        self._create_menu()
        self._create_content()

    def _create_menu(self):
        """Crea el menú lateral con logo que sobresale del borde izquierdo."""
        self.menu_container = QWidget(self.container)
        self.menu_container.setGeometry(0, 0, 300, 600)
        main_panel = QWidget(self.menu_container)
        main_panel.setGeometry(60, 0, 240, 600)
        main_panel.setStyleSheet(
            "background-color: rgba(0, 0, 0, 100);"
            "border-right: 2px solid rgba(87, 35, 100, 150);"
            "border-left: 2px solid rgba(87, 35, 100, 150);"
        )
        self._create_logo_section()
        self._create_buttons_panel(main_panel)
        self._create_version_info_panel(main_panel)

    def _create_logo_section(self):
        """Crea la sección del logo clickeable en posición correcta."""
        logo_label = QLabel(self.container)
        logo_pixmap = QPixmap(Config.LOGO_PATH)
        logo_label.setPixmap(logo_pixmap)
        logo_label.setGeometry(13, 23, logo_pixmap.width(), logo_pixmap.height())
        logo_label.setCursor(Qt.PointingHandCursor)

        def open_dtupscan():
            webbrowser.open("https://www.babylon-scanlation.pages.dev/")

        def handle_logo_click(event):
            if event.button() == Qt.LeftButton:
                open_dtupscan()

        logo_label.mousePressEvent = handle_logo_click

    def _create_buttons_panel(self, parent):
        """Creates the menu buttons panel."""
        buttons_panel = QWidget(parent)
        buttons_panel.setGeometry(20, 200, 200, 340)
        buttons_panel.setStyleSheet("border: 2px solid rgba(87, 35, 100, 150);")
        buttons_layout = QVBoxLayout(buttons_panel)
        buttons_layout.setContentsMargins(15, 15, 15, 15)
        buttons_layout.setSpacing(15)
        btn_data = {
            "INICIO": self.show_home,
            "HERRAMIENTAS": self.show_utilities,
            "PROYECTOS": self.show_projects,
            "AYUDA": self.show_help,
            "OPCIONES": self.show_options,
            "SOBRE ESTO": self.show_about,
        }
        for text, action in btn_data.items():
            btn = self._create_button(text, action)
            buttons_layout.addWidget(btn)
        buttons_layout.addStretch()

    def _create_version_info_panel(self, parent):
        """Creates the version info panel at the bottom."""
        version_info_panel = QWidget(parent)
        version_info_panel.setGeometry(20, 550, 200, 30)
        version_info_panel.setStyleSheet("background: transparent; border: none;")
        version_info_layout = QVBoxLayout(version_info_panel)
        version_info_layout.setContentsMargins(0, 0, 0, 0)
        version_info_layout.setSpacing(0)
        label_style = """
        QLabel {
            font-family: "Roboto Black";
            font-size: 10px;
            color: #FFFFFF;
            background: transparent;
            qproperty-alignment: AlignCenter;
            margin: 0;
            padding: 0;
        }
        """
        version_label = QLabel("Versión: 2.0.1.5")
        snapshot_label = QLabel("Snapshot: U13042025")
        for label in (version_label, snapshot_label):
            label.setStyleSheet(label_style)
            label.setFont(self.roboto_black_font)
            version_info_layout.addWidget(label)

    def _create_button(self, text, callback):
        """Crea un botón con el estilo predefinido y cambia el cursor al pasar el mouse."""
        button = QPushButton(text)
        button.setStyleSheet(
            "QPushButton {"
            "font-size: 14px;"
            "color: white;"
            "background-color: #333333;"
            "border: none;"
            "padding: 10px;"
            "}"
            "QPushButton:hover {"
            "background-color: #555555;"
            "}"
        )
        button.setFont(self.adventure_font)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _create_content(self):
        """Crea las áreas principales de contenido."""
        self.content_container = QWidget(self.container)
        self.content_container.setGeometry(310, 0, 900, 600)
        self.home_label = self._create_text_area(
            "Bienvenido a 'De Todo Un Poco'.",
            style=(
                "font-size: 24px;"
                "color: white;"
                "background-color: rgba(0, 0, 0, 100);"
                "padding: 20px;"
                "border-radius: 0px;"
                "border: 2px solid rgba(87, 35, 100, 150);"
            ),
        )
        self.utilities_area = self.tools_manager.create_utilities_area()
        self.projects_area = self._create_projects_area()
        self.about_area = self._create_about_area()
        self.help_area = self._create_help_area()
        setattr(self, "options_area", self.options_menu.create_options_area())
        self._hide_all_sections()
        self.show_home()

    def _create_text_area(self, text, style=None):
        """Crea un área de texto estilizada."""
        text_edit = QTextEdit(self.content_container)
        base_style = (
            "font-size: 24px;"
            "color: white;"
            "background-color: rgba(0, 0, 0, 75);"
            "padding: 20px;"
            "border-radius: 0px;"
        )
        text_edit.setStyleSheet(style or base_style)
        text_edit.setFont(self.super_cartoon_font)
        text_edit.setText(text)
        text_edit.setGeometry(50, 50, 780, 500)
        text_edit.setReadOnly(True)
        return text_edit

    def _create_projects_area(self):
        """Crea el área de proyectos con botones de gestión."""
        # pylint: disable=protected-access
        projects_area = {
            "scroll": self.tools_manager._create_scroll_area("projects"),
            "footer": self.tools_manager._create_footer_text("projects"),
        }
        button_container = QWidget(self.content_container)
        button_layout = QHBoxLayout(button_container)
        button_layout.setContentsMargins(69, 8, 0, 0)
        button_layout.setSpacing(25)
        add_button = QPushButton("AÑADIR PROYECTO")
        add_button.clicked.connect(
            lambda: self.project_manager.add_project(
                projects_area["scroll"].widget().layout()
            )
        )
        add_button.setStyleSheet(
            """
        QPushButton {
        font-size: 14px;
        color: white;
        border: none;
        background-color: #555555;
        text-align: center;
        padding: 8px;
        }
        QPushButton:hover {
        background-color: #888888;
        border: none;
        }
        """
        )
        add_button.setFont(self.adventure_font)
        add_button.setCursor(Qt.PointingHandCursor)
        button_layout.addWidget(add_button)
        delete_button = QPushButton("QUITAR PROYECTO")
        delete_button.setStyleSheet(
            """
        QPushButton {
        font-size: 14px;
        color: white;
        border: none;
        background-color: #555555;
        text-align: center;
        padding: 8px;
        }
        QPushButton:hover {
        background-color: #888888;
        border: none;
        }
        QPushButton:checked {
        background-color: #333333;
        border: none;
        }
        """
        )
        delete_button.setFont(self.adventure_font)
        delete_button.setCursor(Qt.PointingHandCursor)
        delete_button.setCheckable(True)
        delete_button.toggled.connect(self.project_manager.toggle_delete_mode)
        button_layout.addWidget(delete_button)
        projects_area["button_container"] = button_container
        return projects_area

    def _create_help_area(self):
        """Crea el área de ayuda con manejo de errores para miniaturas."""
        scroll_area = QScrollArea(self.content_container)
        scroll_area.setGeometry(50, 50, 780, 500)
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet(
            "QScrollArea {"
            "background: rgba(0, 0, 0, 100);"
            "border: 2px solid rgba(87, 35, 100, 150);"
            "}"
            "QScrollBar:vertical {"
            "background: rgba(0, 0, 0, 30);"
            "border-radius: 5px;"
            "}"
            "QScrollBar::handle:vertical {"
            "background: rgba(0, 0, 0, 100);"
            "border-radius: 5px;"
            "}"
        )
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        grid_layout = QGridLayout(container)
        grid_layout.setContentsMargins(7, 7, 0, 0)
        grid_layout.setHorizontalSpacing(19)
        grid_layout.setVerticalSpacing(0)
        grid_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        thumbnail_width = 220
        thumbnail_height = 123
        margin = 4
        max_cols = 3
        row = 0
        col = 0
        for video in Config.HELP_VIDEOS:
            card = self._create_video_card(
                video, thumbnail_width, thumbnail_height, margin
            )
            grid_layout.addWidget(card, row, col, Qt.AlignLeft | Qt.AlignTop)
            col += 1
            if col >= max_cols:
                col = 0
                row += 1
        grid_layout.setRowStretch(row + 1, 1)
        grid_layout.setColumnStretch(max_cols, 1)
        scroll_area.setWidget(container)
        return scroll_area

    def _create_video_card(self, video, thumbnail_width, thumbnail_height, margin):
        """Crea la tarjeta de video con la nueva miniatura."""
        card = QWidget()
        card.setFixedSize(
            thumbnail_width + 10 + (margin * 2), thumbnail_height + 50 + (margin * 2)
        )
        card.setStyleSheet("background: transparent;")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(5)
        card_layout.setAlignment(Qt.AlignTop)
        thumbnail_container = self._create_thumbnail_container(
            thumbnail_width, thumbnail_height, margin
        )
        thumbnail_layout = QVBoxLayout(thumbnail_container)
        thumbnail_layout.setContentsMargins(margin, margin, margin, margin)
        thumbnail_layout.setAlignment(Qt.AlignCenter)
        thumbnail = self._create_thumbnail_widget(
            thumbnail_width, thumbnail_height, video["id"]
        )
        thumbnail_layout.addWidget(thumbnail)
        title = self._create_video_title(video["title"])
        self._setup_thumbnail_hover_effects(thumbnail_container)
        card_layout.addWidget(thumbnail_container)
        card_layout.addWidget(title)
        return card

    def _create_thumbnail_container(self, width, height, margin):
        """Crea el contenedor de la miniatura con estilo."""
        container = QWidget()
        container.setFixedSize(width + (margin * 2), height + (margin * 2))
        container.setStyleSheet(
            """
        background: rgba(30, 30, 30, 150);
        border-radius: 6px;
        border: 1px solid rgba(150, 0, 150, 180);
        """
        )
        return container

    def _create_thumbnail_widget(self, width, height, video_id):
        """Crea la miniatura clickeable usando la subclase."""
        thumbnail = ClickableThumbnail(video_id)
        thumbnail.setFixedSize(width, height)
        thumbnail.setAlignment(Qt.AlignCenter)
        thumbnail.setStyleSheet("background: transparent; border: none;")
        thumbnail.clicked.connect(self._play_video)
        try:
            thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
            image_data = self._download_image(thumbnail_url)
            if image_data:
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)
                scaled = pixmap.scaled(
                    width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                thumbnail.setPixmap(scaled)
            else:
                thumbnail.setText("Miniatura no disponible")
                thumbnail.setStyleSheet("color: white; font-size: 12px;")
        except (requests.RequestException, IOError):
            thumbnail.setText("Error al cargar")
            thumbnail.setStyleSheet("color: white; font-size: 12px;")
        return thumbnail

    def _create_video_title(self, title_text):
        """Crea el título del video con estilo."""
        title = QLabel(title_text)
        title.setStyleSheet(
            """
        QLabel {
        color: white;
        background: transparent;
        font-size: 13px;
        qproperty-alignment: AlignCenter;
        }
        """
        )
        title.setFont(self.roboto_black_font)
        title.setWordWrap(True)
        title.setFixedWidth(220)
        return title

    def _setup_thumbnail_hover_effects(self, thumbnail_container):
        """Configura los efectos hover para el contenedor de la miniatura."""

        def on_enter(_):
            thumbnail_container.setStyleSheet(
                """
                background: rgba(60, 60, 60, 180);
                border-radius: 6px;
                border: 1px solid rgba(200, 50, 200, 200);
            """
            )

        def on_leave(_):
            thumbnail_container.setStyleSheet(
                """
                background: rgba(30, 30, 30, 150);
                border-radius: 6px;
                border: 1px solid rgba(150, 0, 150, 180);
            """
            )

        thumbnail_container.enterEvent = on_enter  # type: ignore[assignment]  # noqa
        thumbnail_container.leaveEvent = on_leave  # type: ignore[assignment]  # noqa

    def _download_image(self, url):
        """Descarga imágenes desde una URL."""
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException:
            return b""

    def _play_video(self, video_id):
        """Reproduce un video de YouTube en el navegador."""
        webbrowser.open(f"https://www.youtube.com/watch?v={video_id}")

    def _create_about_area(self):
        """Crea el área 'Sobre esto' con borde."""
        return self._create_text_area(
            Config.ABOUT_TEXT,
            style=(
                "font-size: 24px; color: white;"
                "background-color: rgba(0, 0, 0, 100);"
                "padding: 20px; border-radius: 0px;"
                "border: 2px solid rgba(87, 35, 100, 150);"
            ),
        )

    def _clear_temp_files(self):
        """Limpiar archivos temporales creados por la aplicación."""
        for file_path in self.temp_files:
            self._delete_file_with_retries(file_path)
        self.temp_files.clear()

    def _delete_file_with_retries(self, file_path, retries=3, delay=1):
        """Delete a file with retries in case of PermissionError."""
        if not os.path.isfile(file_path):
            return
        for attempt in range(retries):
            try:
                os.unlink(file_path)
                break
            except PermissionError:
                if attempt < retries - 1:
                    time.sleep(delay)
                else:
                    raise

    

    def _hide_all_sections(self):
        """Oculta todas las secciones de contenido y controles de traducción."""
        self._hide_translation_controls()
        self._hide_main_sections()
        self._hide_special_containers()

    def _hide_translation_controls(self):
        """Oculta controles específicos de traducción."""
        tools_manager = self.tools_manager
        if not tools_manager:
            return

        controls_to_hide = [
            tools_manager.toggle_ai_button,
            tools_manager.source_combo,
            tools_manager.target_combo,
            self._get_optional_control(tools_manager, 'custom_text_input'),
            self._get_optional_control(tools_manager, 'use_custom_button'),
            self._get_optional_control(tools_manager, 'translator_warning_label'),
        ]

        for control in filter(None, controls_to_hide):
            if control.isVisible():
                control.hide()

    def _get_optional_control(self, manager, attr_name):
        """Obtiene un control opcional si existe."""
        return getattr(manager, attr_name, None)

    def _hide_main_sections(self):
        """Oculta las secciones principales de la UI."""
        self._hide_widgets(["home_label", "help_area", "options_area", "about_area"])
        self._hide_utilities_area()
        self._hide_projects_area()

    def _hide_special_containers(self):
        """Maneja la ocultación de contenedores especializados."""
        self._hide_and_delete_containers()

    def _hide_widgets(self, widget_attrs):
        """Oculta varios adminículos si existen."""
        for widget_attr in widget_attrs:
            self._hide_widget(widget_attr)

    def _hide_utilities_area(self):
        """Oculta la zona de utilidades si existe."""
        if self.utilities_area:
            self.utilities_area["scroll"].hide()
            self.utilities_area["footer"].hide()

    def _hide_and_delete_containers(self):
        """Oculta y elimina los contenedores específicos."""
        containers = [
            ("parent_container", None),
            ("details_container", None),
            ("gemini_container", None),
            ("parent_container", self.tools_manager),
            ("gemini_container", self.tools_manager),
            ("mistral_container", self.tools_manager),
        ]
        for attr, manager in containers:
            self._hide_and_delete_container(attr, manager)

    def _hide_projects_area(self):
        """Oculta la zona de proyectos si existe."""
        if self.projects_area:
            self.projects_area["scroll"].hide()
            self.projects_area["footer"].hide()
            if "button_container" in self.projects_area:
                self.projects_area["button_container"].hide()

    def _hide_widget(self, widget_attr):
        """Oculta el adminículo si existe."""
        widget = getattr(self, widget_attr, None)
        if widget:
            widget.hide()

    def _hide_and_delete_container(self, attr, manager):
        """Oculta, elimina, y coloca el contenedor como 'None'."""
        if manager is None:
            manager = self
        container = getattr(manager, attr, None)
        if container:
            container.hide()
            container.deleteLater()
            setattr(manager, attr, None)

    def show_home(self):
        """Muestra la sección de inicio."""
        self._hide_all_sections()
        self.home_label.show()

    def show_utilities(self):
        """Muestra la sección de herramientas."""
        self._hide_all_sections()
        self.utilities_area["scroll"].show()
        self.utilities_area["footer"].show()
        if (
            hasattr(self.tools_manager, "install_button")
            and self.tools_manager.install_button is not None
        ):
            if self.tools_manager.download_in_progress:
                self.tools_manager.install_button.setText("Descargando...")
                self.tools_manager.install_button.setEnabled(False)
            elif self.tools_manager.haruneko_installed:
                self.tools_manager.install_button.setText("Eliminar")
                self.tools_manager.install_button.setEnabled(True)

    def show_projects(self):
        """Muestra la sección de proyectos."""
        self._hide_all_sections()
        self.projects_area["scroll"].show()
        self.projects_area["footer"].show()
        if "button_container" in self.projects_area:
            self.projects_area["button_container"].show()

    def show_help(self):
        """Muestra la sección de ayuda."""
        self._hide_all_sections()
        self.help_area.show()

    def show_options(self):
        """Muestra la sección de opciones."""
        self._hide_all_sections()
        self.options_area.show()

    def show_about(self):
        """Muestra la sección 'Sobre esto'."""
        self._hide_all_sections()
        self.about_area.show()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Single instance mechanism using QSharedMemory
    shared_memory_key = "BabylonScanlationAppSharedMemory"
    print(f"DEBUG: QSharedMemory - Attempting to acquire lock with key: '{shared_memory_key}'")
    shared_memory = QSharedMemory(shared_memory_key)

    # Try to attach to the shared memory segment
    if not shared_memory.create(1): # Create a 1-byte segment
        # If creation fails, it means another instance is running
        print(f"DEBUG: QSharedMemory - create() failed. Error: {shared_memory.errorString()}")
        print("La aplicación ya está en ejecución. Saliendo de la segunda instancia.")
        sys.exit(0)
    else:
        # This is the first instance
        print("DEBUG: QSharedMemory - create() succeeded. This is the first instance.")
        print("Primera instancia iniciada. Bloqueo de memoria compartida adquirido.")
        # Ensure the shared memory is detached when the application exits
        app.aboutToQuit.connect(shared_memory.detach)
        print("DEBUG: QSharedMemory - app.aboutToQuit connected to shared_memory.detach()")

        window = App()
        window.show()
        sys.exit(app.exec_())
