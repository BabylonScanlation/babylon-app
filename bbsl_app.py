"""
Módulo principal para la aplicación Babylon Scanlation.
Proporciona una interfaz gráfica para acceder a diversas herramientas y gestionar proyectos.
"""

# bibliotecas nativas
import os
import sys
import webbrowser
from PyQt5.QtCore import QTimer, QPropertyAnimation
from PyQt5.QtWidgets import QGraphicsOpacityEffect

# git fetch --all
# git reset --hard origin/main

# bibliotecas no nativas
import cv2
import requests
import time

# pylint: disable=no-name-in-module
from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal, QSharedMemory
from PyQt5.QtGui import QFont, QFontDatabase, QIcon, QImage, QPixmap, QCursor
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer, QMediaPlaylist
from PyQt5.QtWidgets import (
    QApplication, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QScrollArea, QTextEdit, QVBoxLayout, QWidget, QComboBox, QCheckBox, QMessageBox,
    QGroupBox, QSlider, QProgressBar
)

from project_manager import ProjectManager
from tools import ToolsManager
from config import Config, resource_path, global_exception_handler
from options_menu import OptionsMenu


sys.excepthook = global_exception_handler


class ClickableThumbnail(QLabel):  # pylint: disable=too-few-public-methods
    """QLabel personalizado que emite una señal al hacer clic."""

    clicked = pyqtSignal(str)

    def __init__(self, video_id: str, parent=None):
        super().__init__(parent)
        self.video_id = video_id
        self.setCursor(QCursor(Qt.PointingHandCursor)) # type: ignore[attr-defined]

    def mousePressEvent(self, event):  # pylint: disable=invalid-name
        """Maneja el clic y emite la señal."""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.video_id)
        super().mousePressEvent(event)


# pylint: disable=too-many-instance-attributes, too-many-lines
class App(QMainWindow):
    gemini_config_closed = pyqtSignal() # Nueva señal
    """Clase principal de la aplicación que maneja la interfaz gráfica."""

    SHARED_MEMORY_KEY = "BabylonScanlationAppSingleInstance"
    _shared_memory = None

    def _check_single_instance(self):
        """
        Checks if another instance of the application is already running using shared memory.
        Returns True if this is the first instance, False otherwise.
        """
        self._shared_memory = QSharedMemory(self.SHARED_MEMORY_KEY)
        if self._shared_memory.attach():
            # Another instance is already running
            return False
        else:
            if self._shared_memory.create(1): # Create a shared memory segment of 1 byte
                # This is the first instance
                return True
            else:
                # Failed to create shared memory, another instance might be starting
                return False

    def __init__(self):
        """Iniciador de funciones."""
        super().__init__()

        if not self._check_single_instance():
            if hasattr(self, 'timer') and self.timer.isActive():
                self.timer.stop()
            if hasattr(self, 'cap') and self.cap is not None and self.cap.isOpened():
                self.cap.release()

            QApplication.quit()
            sys.exit(0) # Fallback exit

        self.menu_container = None
        self.content_container = None
        self.home_label = None
        self.utilities_area = None
        self.projects_area = None
        self.help_area = None
        self.about_area = None
        self.configuration_area = None # New configuration area
        self.gemini_config_area = None # Gemini specific configuration area
        self.custom_fonts = []
        self.super_cartoon_font = QFont("Arial")
        self.adventure_font = QFont("Arial")
        self.roboto_black_font = QFont("Arial")
        self.options_menu = OptionsMenu(self)
        self.project_manager = ProjectManager(Config.USER_DATA_DIR)
        self.tools_manager = ToolsManager(self)
        self.tools_manager.gemini_processor.set_token_callback(self.update_session_token_count)
        self.cap = None
        self.timer = QTimer(self)
        self.video_label = QLabel(self)
        self.temp_files = []
        self.audio_player = None
        self.session_tokens = 0
        self._setup_main_window()
        self._load_fonts()
        self._create_layout()
        self._setup_audio()

    def showEvent(self, event):
        """Maneja el evento de mostrar la ventana para iniciar la reproducción de medios."""
        super().showEvent(event)
        self._start_audio_playback()
        self._start_video_playback()

    def _setup_main_window(self):
        """Configura la ventana principal con video o imagen de fondo."""
        self.setWindowTitle(Config.WINDOW_TITLE)
        self.setFixedSize(*Config.WINDOW_SIZE)
        self.setWindowIcon(QIcon(Config.ICON_PATH))
        self.background_label = QLabel(self)
        self.background_label.setScaledContents(True)
        self.background_label.setGeometry(0, 0, *Config.WINDOW_SIZE)
        self._setup_carousel() # Call the new carousel setup method
        self._setup_opencv_video()
        self.container = QWidget(self)
        self.container.setGeometry(0, 0, *Config.WINDOW_SIZE)
        self.container.raise_()

    def _setup_audio(self):
        # Configura el reproductor de audio en bucle con múltiples archivos.
        self.audio_player = QMediaPlayer()
        self.playlist = QMediaPlaylist()

        for audio_file in Config.AUDIO_FILES:
            if os.path.exists(audio_file):
                self.playlist.addMedia(QMediaContent(QUrl.fromLocalFile(audio_file)))
            else:
                print(f"Advertencia: Archivo de audio no encontrado: {audio_file}")

        if self.playlist.mediaCount() > 0:
            try:
                self.playlist.setPlaybackMode(QMediaPlaylist.Loop)
                
                for i in range(self.playlist.mediaCount()):
                    media = self.playlist.media(i)
                    if media.isNull():
                        print(f"Advertencia: Medio nulo encontrado en la lista de reproducción en el índice {i}.")
                self.audio_player.setPlaylist(self.playlist)
                self.audio_player.setVolume(5)
            except (IOError, OSError) as e:
                print(f"Error al iniciar la reproducción de audio: {e}")
        else:
            print("No se encontraron archivos de audio válidos para reproducir.")

    def _start_audio_playback(self):
        """Inicia la reproducción de audio."""
        if self.audio_player and self.playlist.mediaCount() > 0:
            self.audio_player.play()

    def _setup_carousel(self):
        """Configura el carrusel de imágenes de fondo."""
        self.carousel_images = [QPixmap(path) for path in Config.CAROUSEL_IMAGES if os.path.exists(path)]
        if not self.carousel_images:
            print("Advertencia: No se encontraron imágenes para el carrusel.")
            return

        self.current_carousel_index = 0
        self.background_label.setPixmap(self.carousel_images[self.current_carousel_index])

        self.opacity_effect = QGraphicsOpacityEffect(self.background_label)
        self.background_label.setGraphicsEffect(self.opacity_effect)

        self.carousel_timer = QTimer(self)
        self.carousel_timer.setInterval(Config.CAROUSEL_INTERVAL)
        self.carousel_timer.timeout.connect(self._next_carousel_image)
        self.carousel_timer.start()

    def _next_carousel_image(self):
        """Cambia a la siguiente imagen del carrusel con una transición de fundido."""
        if not self.carousel_images:
            return

        # Fade out current image
        self.fade_out_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_out_animation.setDuration(1000)
        self.fade_out_animation.setStartValue(1.0)
        self.fade_out_animation.setEndValue(0.0)
        self.fade_out_animation.finished.connect(self._update_carousel_image)
        self.fade_out_animation.start()

    def _update_carousel_image(self):
        """Actualiza la imagen y la desvanece."""
        self.current_carousel_index = (self.current_carousel_index + 1) % len(self.carousel_images)
        self.background_label.setPixmap(self.carousel_images[self.current_carousel_index])

        # Fade in new image
        self.fade_in_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_in_animation.setDuration(1000)
        self.fade_in_animation.setStartValue(0.0)
        self.fade_in_animation.setEndValue(1.0)
        self.fade_in_animation.start()

    def _start_carousel_fade_in(self):
        """Starts the fade-in animation for the carousel background."""
        self.background_label.show() # Ensure the label is visible before fading in
        self.fade_in_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_in_animation.setDuration(1000)
        self.fade_in_animation.setStartValue(0.0)
        self.fade_in_animation.setEndValue(1.0)
        self.fade_in_animation.start()

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
            self._start_carousel_fade_in() # If video fails, start fade-in for background
            return
        self.video_label.setGeometry(0, 0, *Config.WINDOW_SIZE)
        self.timer.timeout.connect(self._update_frame)
        self.background_label.hide()

    def _start_video_playback(self):
        """Inicia la reproducción de video."""
        if self.cap and self.cap.isOpened():
            fps = self.cap.get(cv2.CAP_PROP_FPS)  # pylint: disable=no-member
            self.timer.start(int(1000 / fps))

    def _update_frame(self):
        """Actualiza el frame del video."""
        if self.cap is None or not self.cap.isOpened():
            # If video capture is not open, try to re-initialize it
            self.timer.stop() # Stop the current timer
            if self._initialize_video_capture():
                # If re-initialization successful, restart timer and hide background
                fps = self.cap.get(cv2.CAP_PROP_FPS)
                self.timer.start(int(1000 / fps))
                self.background_label.hide()
            else:
                # If re-initialization failed, show background and return
                self._start_carousel_fade_in() # If re-initialization failed, start fade-in for background
                return

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
                # If ret is False, it could be end of video or a read error.
                # Try to reset to beginning, if that fails, re-initialize.
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # pylint: disable=no-member
                ret_after_reset, _ = self.cap.read() # Try reading after reset
                if not ret_after_reset:
                    # If still no frame after reset, assume stream is broken, re-initialize
                    self.timer.stop()
                    if self.cap is not None:
                        self.cap.release()
                    if self.cap is not None:
                        self.cap.release()
                    self._start_carousel_fade_in()
        except (IOError, cv2.error) as e:
            self.timer.stop()
            if self.cap is not None:
                self.cap.release()
            self._start_carousel_fade_in() # Attempt full re-setup with fade-in

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

        # Detach from shared memory
        if self._shared_memory and self._shared_memory.isAttached():
            self._shared_memory.detach()

        event.accept()

        # Detach from shared memory
        if self._shared_memory and self._shared_memory.isAttached():
            self._shared_memory.detach()

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
            webbrowser.open("https://babylon-scanlation.pages.dev/")

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
        version_label = QLabel("Versión: 2.3.1")
        snapshot_label = QLabel("Snapshot: U27082025")
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
            "Bienvenido al programa 'Babylon Scanlation'.",
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
        self.options_area = self.options_menu.create_options_area()
        self.options_area.setParent(self.content_container) # Asegurarse de que sea hijo del content_container
        self.configuration_area = self._create_configuration_area() # Initialize configuration area
        self.gemini_config_area = self._create_gemini_config_area() # Initialize Gemini specific configuration area
        self.gemini_config_area.setParent(self.content_container) # Ensure it's a child of content_container
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

    def _create_configuration_area(self):
        """Crea el área de configuración para Gemini y otras opciones."""
        config_area = QWidget(self.content_container)
        config_area.setGeometry(50, 50, 780, 500)
        config_area.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
            """
        )
        main_layout = QVBoxLayout(config_area)
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
        title_label.setFont(self.super_cartoon_font)
        main_layout.addWidget(title_label)

        # Model Selection
        model_layout = QHBoxLayout()
        model_label = QLabel("Modelo Gemini:")
        model_label.setStyleSheet("color: white;")
        model_label.setFont(self.roboto_black_font)
        model_combo = QComboBox()
        model_combo.addItems(["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"])
        model_combo.setCurrentText(Config.GEMINI_MODEL)
        model_combo.setStyleSheet(
            """
            QComboBox {
                background-color: rgba(0,0,0,150);
                color: white;
                border: 2px solid #572364;
                padding: 3px;
                min-width: 100px;
                selection-color: white; /* Añadido */
            }
            QComboBox QAbstractItemView {
                background-color: #2a2a2a;
                color: white;
                selection-background-color: #572364;
            }
            """
        )
        model_combo.setFont(self.roboto_black_font)
        model_combo.currentIndexChanged.connect(
            lambda: self._save_gemini_settings(model_combo.currentText(), self.thinking_checkbox.isChecked())
        )
        model_layout.addWidget(model_label)
        model_layout.addWidget(model_combo)
        model_layout.addStretch(1)
        main_layout.addLayout(model_layout)

        # Thinking Checkbox
        thinking_layout = QHBoxLayout()
        self.thinking_checkbox = QCheckBox("Activar Pensamiento (Thinking)")
        self.thinking_checkbox.setChecked(Config.GEMINI_ENABLE_THINKING)
        self.thinking_checkbox.setStyleSheet("color: white;")
        self.thinking_checkbox.setFont(self.roboto_black_font)
        self.thinking_checkbox.stateChanged.connect(
            lambda: self._save_gemini_settings(model_combo.currentText(), self.thinking_checkbox.isChecked())
        )
        thinking_layout.addWidget(self.thinking_checkbox)
        thinking_layout.addStretch(1)
        main_layout.addLayout(thinking_layout)

        main_layout.addStretch(1) # Push content to top

        return config_area

    def _clear_temp_files(self):
        """Limpiar archivos temporales creados por la aplicación."""
        for file_path in self.temp_files:
            self._delete_file_with_retries(file_path)
        self.temp_files.clear()

    def _save_gemini_settings(self, model_name, enable_thinking, temperature):
        """Guarda la configuración de Gemini en el archivo de usuario."""
        try:
            settings = {
                "GEMINI_MODEL": model_name,
                "GEMINI_ENABLE_THINKING": enable_thinking,
                "GEMINI_TEMPERATURE": temperature,
            }
            Config._save_user_settings(settings)

            # Update in-memory Config
            Config.GEMINI_MODEL = model_name
            Config.GEMINI_ENABLE_THINKING = enable_thinking
            Config.GEMINI_TEMPERATURE = temperature

            print("✅ Configuración de Gemini guardada correctamente.")
        except Exception as e:
            print(f"❌ Error al guardar la configuración de Gemini: {e}")
            QMessageBox.critical(
                None, "Error", f"Error al guardar la configuración de Gemini: {e}"
            )

    def _cancel_gemini_settings(self):
        """Restaura la configuración de Gemini a los valores originales y oculta la sección."""
        # Restaurar los valores de Config a los originales
        Config.GEMINI_MODEL = self._original_gemini_model
        Config.GEMINI_ENABLE_THINKING = self._original_gemini_thinking
        Config.GEMINI_TEMPERATURE = self._original_gemini_temperature

        # Actualizar la UI para reflejar los valores restaurados (opcional, pero buena práctica)
        self.gemini_model_combo.setCurrentText(Config.GEMINI_MODEL)
        self.gemini_thinking_cb.setChecked(Config.GEMINI_ENABLE_THINKING)
        self.temperature_slider.setValue(int(Config.GEMINI_TEMPERATURE * 100))
        self._update_gemini_temperature_label(int(Config.GEMINI_TEMPERATURE * 100))

        # Ocultar la sección de configuración de Gemini
        self._hide_gemini_config_area()

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
        self._hide_widgets(["home_label", "help_area", "options_area", "about_area", "configuration_area", "gemini_config_area"])
        self._hide_utilities_area()
        self._hide_projects_area()
        if hasattr(self.tools_manager, 'gemini_container') and self.tools_manager.gemini_container:
            self.tools_manager.gemini_container.hide()

    def show_gemini_configuration(self):
        """Muestra la sección de configuración de Gemini."""
        self._hide_all_sections()
        self.gemini_config_area.show()

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

    def show_configuration(self):
        """Muestra la sección de configuración."""
        self._hide_all_sections()
        if not self.configuration_area:
            self.configuration_area = self._create_configuration_area()
        self.configuration_area.show()

    def show_about(self):
        """Muestra la sección 'Sobre esto'."""
        self._hide_all_sections()
        self.about_area.show()


    def show_about(self):
        """Muestra la sección 'Sobre esto'."""
        self._hide_all_sections()
        self.about_area.show()

    def _create_gemini_config_area(self):
        """Crea el área de configuración independiente para Gemini."""
        page = QWidget(self.content_container)
        page.setGeometry(50, 50, 780, 500) # Misma geometría que otras secciones
        page.setStyleSheet(
            """
            background-color: rgba(0, 0, 0, 100);
            border: 1px solid rgba(87, 35, 100, 180);
            """
        )
        layout = QVBoxLayout(page)

        # --- Grupo Principal de Configuración ---
        gemini_group = QGroupBox("Configuración de Gemini")
        gemini_group.setStyleSheet(
            """
            QGroupBox {
                font-size: 15px;
                font-weight: bold;
                color: white;
                background-color: rgba(30, 30, 30, 200);
                border: 1px solid rgba(150, 0, 150, 180);
                border-radius: 5px;
                margin-top: 25px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0px;
                color: white;
                background-color: transparent;
            }
        """
        )
        gemini_layout = QVBoxLayout(gemini_group)

        self._original_gemini_model = Config.GEMINI_MODEL
        self._original_gemini_thinking = Config.GEMINI_ENABLE_THINKING
        self._original_gemini_temperature = Config.GEMINI_TEMPERATURE

        self.gemini_model_combo = QComboBox()
        models = {
            "gemini-2.5-pro": "RPM: 5, TPM: 250,000, RPD: 100",
            "gemini-2.5-flash": "RPM: 10, TPM: 250,000, RPD: 250",
            "gemini-2.5-flash-lite": "RPM: 15, TPM: 250,000, RPD: 1,000"
        }
        for model, tooltip in models.items():
            self.gemini_model_combo.addItem(model)
            self.gemini_model_combo.setItemData(self.gemini_model_combo.count() - 1, tooltip, Qt.ToolTipRole)
        self.gemini_model_combo.setStyleSheet(
            """
            QComboBox {
                background-color: rgba(0,0,0,150);
                color: white;
                border: 2px solid #572364;
                padding: 3px;
                min-width: 100px;
                selection-color: white;
            }
            QComboBox QAbstractItemView {
                background-color: #2a2a2a;
                color: white;
                selection-background-color: #572364;
            }
            """
        )
        self.gemini_model_combo.setFont(self.roboto_black_font)
        self.gemini_model_combo.setCurrentText(Config.GEMINI_MODEL)

        self.gemini_thinking_cb = QCheckBox("Activar modo pensamiento")
        self.gemini_thinking_cb.setStyleSheet("color: white;")
        self.gemini_thinking_cb.setFont(self.roboto_black_font)
        self.gemini_thinking_cb.setChecked(Config.GEMINI_ENABLE_THINKING)

        self.temperature_slider = QSlider(Qt.Horizontal)
        self.temperature_slider.setRange(0, 100)
        self.temperature_slider.setValue(int(Config.GEMINI_TEMPERATURE * 100))
        self.temperature_label = QLabel(f"Temperatura: {Config.GEMINI_TEMPERATURE:.2f}")
        self.temperature_label.setStyleSheet("color: white;")
        self.temperature_label.setFont(self.roboto_black_font)
        self.temperature_slider.valueChanged.connect(self._update_gemini_temperature_label)

        model_label = QLabel("Modelo de Gemini:")
        model_label.setStyleSheet("color: white;")
        model_label.setFont(self.roboto_black_font)
        gemini_layout.addWidget(model_label, alignment=Qt.AlignLeft)
        gemini_layout.addWidget(self.gemini_model_combo)
        gemini_layout.addWidget(self.gemini_thinking_cb)
        gemini_layout.addWidget(self.temperature_label)
        gemini_layout.addWidget(self.temperature_slider)

        # --- Grupo de Límites de API (Informativo) ---
        limits_group = QGroupBox("Límites de API (Informativo)")
        limits_group.setStyleSheet("""
            QGroupBox {
                font-size: 15px;
                font-weight: bold;
                color: white;
                background-color: rgba(30, 30, 30, 100);
                border: 1px solid rgba(150, 0, 150, 180);
                border-radius: 5px;
                margin-top: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding-bottom: 5px;
                color: white;
                background-color: transparent;
            }
        """)
        limits_layout = QVBoxLayout(limits_group)

        self.GEMINI_LIMITS = {
            "gemini-2.5-pro": "Límite: 100 solicitudes por día",
            "gemini-2.5-flash": "Límite: 250 solicitudes por día",
            "gemini-2.5-flash-lite": "Límite: 1,000 solicitudes por día"
        }
        self.GEMINI_TPM_LIMITS = {
            "gemini-2.5-pro": 250000,
            "gemini-2.5-flash": 250000,
            "gemini-2.5-flash-lite": 250000
        }

        self.gemini_limit_label = QLabel()
        self.gemini_limit_label.setStyleSheet("color: white; margin-top: 10px;")
        self.gemini_limit_label.setFont(self.roboto_black_font)
        self.gemini_limit_label.setWordWrap(True)
        limits_layout.addWidget(self.gemini_limit_label)

        self.gcp_quota_button = QPushButton("Consultar mi uso en Google Cloud")
        self.gcp_quota_button.setStyleSheet("""
            QPushButton {
                font-size: 14px;
                color: white;
                background-color: #555555;
                border: none;
                padding: 10px;
                margin-top: 5px;
            }
            QPushButton:hover {
                background-color: #888888;
            }
        """)
        self.gcp_quota_button.setFont(self.adventure_font)
        self.gcp_quota_button.setCursor(Qt.PointingHandCursor)
        self.gcp_quota_button.clicked.connect(self._open_gcp_quota_page)
        limits_layout.addWidget(self.gcp_quota_button)

        # --- Grupo de Uso de Tokens (Sesión Actual) ---
        session_token_group = QGroupBox("Uso de Tokens en esta Sesión")
        session_token_group.setStyleSheet("""
            QGroupBox {
                font-size: 15px;
                font-weight: bold;
                color: white;
                background-color: rgba(30, 30, 30, 100);
                border: 1px solid rgba(150, 0, 150, 180);
                border-radius: 5px;
                margin-top: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding-bottom: 5px;
                color: white;
                background-color: transparent;
            }
        """)
        session_token_layout = QVBoxLayout(session_token_group)
        session_token_layout.setSpacing(10)

        disclaimer_label = QLabel("Nota: Este contador solo refleja el uso en esta sesión y se reinicia con el programa. No representa el límite total de su cuenta.")
        disclaimer_label.setStyleSheet("color: #cccccc; font-style: italic;")
        disclaimer_label.setWordWrap(True)
        session_token_layout.addWidget(disclaimer_label)

        self.session_token_progress_bar = QProgressBar()
        self.session_token_progress_bar.setValue(0)
        self.session_token_progress_bar.setTextVisible(False)
        session_token_layout.addWidget(self.session_token_progress_bar)

        self.session_token_label = QLabel()
        self.session_token_label.setStyleSheet("color: white;")
        self.session_token_label.setFont(self.roboto_black_font)
        session_token_layout.addWidget(self.session_token_label)

        # Añadir los grupos al layout principal de la página
        layout.addWidget(gemini_group)
        layout.addWidget(limits_group)
        layout.addWidget(session_token_group)
        layout.addStretch()

        # Botones de acción en la parte inferior
        self.save_gemini_button = QPushButton("Aplicar")
        self.save_gemini_button.setStyleSheet("""
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
        """)
        self.save_gemini_button.setFont(self.adventure_font)
        self.save_gemini_button.setCursor(Qt.PointingHandCursor)
        self.save_gemini_button.clicked.connect(self._save_gemini_settings_from_ui)

        self.cancel_gemini_button = QPushButton("Cancelar")
        self.cancel_gemini_button.setStyleSheet("""
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
        """)
        self.cancel_gemini_button.setFont(self.adventure_font)
        self.cancel_gemini_button.setCursor(Qt.PointingHandCursor)
        self.cancel_gemini_button.clicked.connect(self._cancel_gemini_settings)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.cancel_gemini_button)
        button_layout.addWidget(self.save_gemini_button)
        layout.addLayout(button_layout)

        page.setLayout(layout)

        # Conectar el cambio de modelo a la actualización de la UI
        self.gemini_model_combo.currentIndexChanged.connect(self._update_model_specific_ui)
        # Actualizar la UI con los valores iniciales
        self._update_model_specific_ui()

        return page

    

    def _update_model_specific_ui(self):
        model = self.gemini_model_combo.currentText()
        
        # Update daily limit label
        daily_limit_text = self.GEMINI_LIMITS.get(model, "Límites diarios no definidos.")
        self.gemini_limit_label.setText(daily_limit_text)

        # Update session token counter UI
        tpm_limit = self.GEMINI_TPM_LIMITS.get(model, 250000) # Default to 250k
        self.session_token_progress_bar.setMaximum(tpm_limit)
        self.session_token_label.setText(f"Tokens en sesión: {self.session_tokens} / {tpm_limit:,}")

    def _open_gcp_quota_page(self):
        webbrowser.open("https://console.cloud.google.com/iam-admin/quotas")

    def update_session_token_count(self, tokens):
        self.session_tokens += tokens
        # Ensure progress bar doesn't exceed max if user continues after warning
        current_max = self.session_token_progress_bar.maximum()
        if self.session_tokens > current_max:
             self.session_token_progress_bar.setValue(current_max)
        else:
             self.session_token_progress_bar.setValue(self.session_tokens)
        self.session_token_label.setText(f"Tokens en sesión: {self.session_tokens} / {current_max:,}")

    def _update_gemini_temperature_label(self, value):
        temp = value / 100.0
        self.temperature_label.setText(f"Temperatura: {temp:.2f}")

    def _save_gemini_settings_from_ui(self):
        """Guarda la configuración de Gemini desde la UI independiente."""
        model = self.gemini_model_combo.currentText()
        is_thinking = self.gemini_thinking_cb.isChecked()
        temperature = self.temperature_slider.value() / 100.0
        self._save_gemini_settings(model, is_thinking, temperature) # Reutilizar el método existente
        self._hide_gemini_config_area()

    def _hide_gemini_config_area(self):
        """Oculta la sección de configuración de Gemini."""
        if hasattr(self, 'gemini_config_area') and self.gemini_config_area:
            self.gemini_config_area.hide()
            self.gemini_config_closed.emit() # Emitir la señal al ocultar


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec_())
