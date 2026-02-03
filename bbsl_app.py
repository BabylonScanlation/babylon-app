"""Módulo principal para la aplicación Babylon Scanlation.
Proporciona una interfaz gráfica para acceder a diversas herramientas y gestionar proyectos.
"""

import logging
import os
import sys
import webbrowser
import time
from typing import Optional, List, Dict, Any, cast

# bibliotecas no nativas
import cv2
import requests

from PySide6.QtCore import Qt, QUrl, Signal, QSharedMemory, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPixmap, QMouseEvent, QCloseEvent
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea, QTextEdit, QVBoxLayout,
    QWidget, QComboBox, QCheckBox, QLineEdit, QFrame
)

from log_console import LogConsole, init_global_logging
from config import USER_DATA_DIR, Config, resource_path, global_exception_handler
from project_manager import ProjectManager
from tools import ToolsManager
from options_menu import OptionsMenu
from ui_components import ClickableThumbnail
from gemini_config_panel import GeminiConfigPanel
from background_manager import BackgroundManager

# 1. INICIALIZAR LOGGING GLOBAL INMEDIATAMENTE
init_global_logging()

# Silenciar logs ruidosos de librerías externas
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING) 

sys.excepthook = global_exception_handler


# pylint: disable=too-many-instance-attributes, too-many-lines
class App(QMainWindow):
    gemini_config_closed = Signal() # Nueva señal
    """Clase principal de la aplicación que maneja la interfaz gráfica."""

    SHARED_MEMORY_KEY = "BabylonScanlationAppSingleInstance"
    _shared_memory = None

    def _check_single_instance(self) -> bool:
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
        
        self.timer: Optional[QTimer] = None # Inicialización temprana para evitar errores en _check_single_instance

        if not self._check_single_instance():
            logging.info("Ya hay una instancia de la aplicación ejecutándose. Cerrando...")
            if hasattr(self, 'timer') and self.timer is not None and self.timer.isActive():
                self.timer.stop()
            if hasattr(self, 'cap') and self.cap is not None and self.cap.isOpened():
                self.cap.release()

            QApplication.quit()
            sys.exit(0) # Fallback exit

        self.menu_container: Optional[QWidget] = None
        self.content_container: Optional[QWidget] = None
        self.home_label: Optional[QFrame] = None
        self.utilities_area: Optional[Dict[str, Any]] = None
        self.projects_area: Optional[Dict[str, Any]] = None
        self.help_area: Optional[QScrollArea] = None
        self.about_area: Optional[QFrame] = None
        self.options_area: Optional[QWidget] = None
        self.configuration_area: Optional[QWidget] = None # New configuration area
        self.gemini_config_area: Optional[QWidget] = None # Gemini specific configuration area
        self.container: Optional[QWidget] = None
        
        self.background_manager: Optional[BackgroundManager] = None
        
        self.custom_fonts: List[QFont] = []
        self.super_cartoon_font = QFont("Arial")
        self.adventure_font = QFont("Arial")
        self.roboto_black_font = QFont("Arial")
        self.options_menu = OptionsMenu(self)
        self.options_menu.controller.volume_changed.connect(self._update_volume)
        self.options_menu.controller.fullscreen_toggled.connect(self._toggle_fullscreen)
        self.options_menu.controller.background_type_changed.connect(self._handle_bg_type_change)
        self.options_menu.controller.music_toggled.connect(self._toggle_music)
        
        self.project_manager = ProjectManager(USER_DATA_DIR)
        self.tools_manager = ToolsManager(self)
        
        self.temp_files: List[str] = []
        self.audio_player: Optional[QMediaPlayer] = None
        self.audio_output: Optional[QAudioOutput] = None
        self.session_tokens = 0
        
        self._original_gemini_model: str = ""
        self._original_gemini_thinking: bool = False
        self._original_auto_model_switch: bool = False
        self._original_gemini_api_key: str = ""
        
        self.gemini_model_combo: Optional[QComboBox] = None
        self.gemini_thinking_cb: Optional[QCheckBox] = None
        self.auto_switch_checkbox: Optional[QCheckBox] = None
        self.gemini_api_input: Optional[QLineEdit] = None
        self.thinking_checkbox: Optional[QCheckBox] = None

        # --- ESTILO GLOBAL CYBER-GLASS ---
        # self._load_stylesheet() # Moved to main

        self._setup_main_window()
        self._load_fonts()
        self._create_layout()
        self._setup_audio()
        
        # Sincronizar entorno
        self._sync_env_to_config()

    def _sync_env_to_config(self):
        """Si existen claves en .env, forzar su uso en Config."""
        logging.info("--- Sincronización de Credenciales ---")
        env_gemini = os.getenv("GEMINI_API_KEY")
        if env_gemini:
            keys = [k.strip() for k in env_gemini.split(",") if k.strip()]
            Config.GEMINI_API_KEY = keys[0] if keys else ""
            Config.GEMINI_API_KEYS = list(dict.fromkeys(keys))
            logging.info(f"✅ GEMINI: Se cargaron {len(Config.GEMINI_API_KEYS)} claves desde .env")
            for i, k in enumerate(Config.GEMINI_API_KEYS):
                masked = k[:4] + "..." + k[-4:] if len(k) > 8 else "???"
                logging.info(f"   [{i+1}] {masked}")
        else:
            logging.warning("⚠️ GEMINI: No se encontraron claves en .env")

        env_mistral = os.getenv("MISTRAL_API_KEY")
        if env_mistral:
            Config.MISTRAL_API_KEY = env_mistral
            logging.info("✅ MISTRAL: Clave cargada.")
            
        env_deepl = os.getenv("DEEPL_API_KEY")
        if env_deepl:
            Config.DEEPL_API_KEY = env_deepl
            logging.info("✅ DEEPL: Clave cargada.")
        logging.info("--------------------------------------")

    def _setup_main_window(self):
        """Configura la ventana principal con video o imagen de fondo."""
        self.setWindowTitle(Config.WINDOW_TITLE)
        self.setFixedSize(*Config.WINDOW_SIZE)
        self.setWindowIcon(QIcon(Config.ICON_PATH))
        
        # Inicializar gestor de fondo
        self.background_manager = BackgroundManager(self)
        
        # self.container NO se crea aquí, se crea una sola vez en _create_layout o viceversa.
        # Vamos a unificarlo para que se cree SOLO AQUÍ.
        self.container = QWidget(self)
        self.container.setObjectName("MasterContainer")
        self.container.setGeometry(0, 0, *Config.WINDOW_SIZE)
        self.container.setStyleSheet("#MasterContainer { background: transparent; border: none; }")
        self.container.raise_()
        
        # INICIAR VIDEO Y AUDIO
        self.background_manager.start_background("Video")
        self._start_audio_playback()
    
    def _setup_audio(self):
        # Configura el reproductor de audio en bucle con múltiples archivos.
        self.audio_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        if self.audio_player and self.audio_output:
            self.audio_player.setAudioOutput(self.audio_output)
            self.audio_output.setVolume(0.05) # Escala 0.0 a 1.0

        self.playlist_files: List[QUrl] = []
        for audio_file in Config.AUDIO_FILES:
            if os.path.exists(audio_file):
                self.playlist_files.append(QUrl.fromLocalFile(audio_file))

        if self.playlist_files:
            self.current_audio_index = 0
            if self.audio_player:
                self.audio_player.setSource(self.playlist_files[self.current_audio_index])
                self.audio_player.mediaStatusChanged.connect(self._handle_media_status)
                self.audio_player.play() # Iniciar audio automáticamente

    def _handle_media_status(self, status: QMediaPlayer.MediaStatus):
        """Maneja el cambio de estado de los medios para implementar el bucle de la lista."""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.current_audio_index = (self.current_audio_index + 1) % len(self.playlist_files)
            if self.audio_player:
                self.audio_player.setSource(self.playlist_files[self.current_audio_index])
                self.audio_player.play()

    def _start_audio_playback(self):
        """Inicia la reproducción de audio."""
        if self.audio_player and self.playlist_files:
            self.audio_player.play()



    def _initialize_video_capture(self) -> bool:
        """Inicializa o detiene el vídeo según elección del usuario."""
        try:
            self.cap = cv2.VideoCapture(Config.VIDEO_PATH)  # pylint: disable=no-member
            if not self.cap or not self.cap.isOpened():
                raise ValueError("No se pudo abrir el archivo de video")
            return True
        except (IOError, OSError, ValueError):
            return False

    def _setup_opencv_video(self):
        """Inicializa el vídeo."""
        if not self._initialize_video_capture():
            self._start_carousel_fade_in() # If video fails, start fade-in for background
            return
        if self.video_label:
            self.video_label.setGeometry(0, 0, *Config.WINDOW_SIZE)
        if self.timer:
            self.timer.timeout.connect(self._update_frame)
        if self.background_label:
            cast(Any, self).background_label.hide()

    def _start_video_playback(self):
        """Inicia la reproducción de video."""
        if self.cap and self.cap.isOpened() and self.timer:
            fps = cast(Any, self).cap.get(cv2.CAP_PROP_FPS)  # pylint: disable=no-member
            if fps > 0:
                self.timer.start(int(1000 / fps))

    def closeEvent(self, a0: QCloseEvent):  # pylint: disable=invalid-name, unused-argument
        """Liberar recursos al cerrar la aplicación."""
        if self.background_manager:
            self.background_manager.cleanup()
            
        if self.audio_player is not None:
            self.audio_player.stop()
            self.audio_player = None
            
        self._clear_temp_files()

        # Detach from shared memory
        if self._shared_memory and self._shared_memory.isAttached():
            self._shared_memory.detach()

        a0.accept()

    def _load_fonts(self):
        """Carga las fuentes personalizadas."""
        self.custom_fonts = self._load_custom_fonts()
        self._set_custom_fonts()
        self._load_roboto_font()

    def _load_custom_fonts(self) -> List[QFont]:
        """Load custom fonts from specified paths."""
        custom_fonts: List[QFont] = []
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
            self.roboto_black_font.setWeight(QFont.Weight.Black)
            self.roboto_black_font.setPointSize(10)

    def _create_layout(self):
        """Crea los componentes principales del layout usando el contenedor ya existente."""
        # self.container ya fue creado en _setup_main_window, no lo recreamos.
        self._create_menu()
        self._create_content()

    def _create_menu(self):
        """Crea el menú lateral con estilo translúcido moderno."""
        if self.container:
            self.menu_container = QWidget(self.container)
            self.menu_container.setGeometry(0, 0, 300, 600)
            main_panel = QFrame(self.menu_container)
            main_panel.setObjectName("MenuPanel")
            main_panel.setGeometry(60, 0, 240, 600)
            self._create_logo_section()
            self._create_buttons_panel(main_panel)
            self._create_version_info_panel(main_panel)

    def _create_logo_section(self):
        """Crea la sección del logo clickeable en posición correcta."""
        if self.container:
            logo_label = QLabel(self.container)
            logo_pixmap = QPixmap(Config.LOGO_PATH)
            logo_label.setPixmap(logo_pixmap)
            logo_label.setGeometry(13, 23, logo_pixmap.width(), logo_pixmap.height())
            logo_label.setCursor(Qt.CursorShape.PointingHandCursor)

            def open_dtupscan():
                webbrowser.open("https://babylon-scanlation.pages.dev/")

            def handle_logo_click(event: QMouseEvent):
                if event.button() == Qt.MouseButton.LeftButton:
                    open_dtupscan()

            logo_label.mousePressEvent = handle_logo_click

    def _create_buttons_panel(self, parent: QWidget):
        """Creates the menu buttons panel without border."""
        buttons_panel = QWidget(parent)
        buttons_panel.setGeometry(20, 190, 200, 350) # Subido ligeramente para mejor balance
        buttons_panel.setStyleSheet("border: none; background: transparent;")
        buttons_layout = QVBoxLayout(buttons_panel)
        buttons_layout.setContentsMargins(5, 5, 5, 5)
        buttons_layout.setSpacing(10) # Espaciado de 10px para que respire mejor
        btn_data = {
            "INICIO": self.show_home,
            "HERRAMIENTAS": self.show_utilities,
            "PROYECTOS": self.show_projects,
            "AYUDA": self.show_help,
            "OPCIONES": self.show_options,
            "CONSOLA": self.show_console,
            
            "SOBRE ESTO": self.show_about,
        }
        for text, action in btn_data.items():
            btn = self._create_button(text, action)
            buttons_layout.addWidget(btn)
        buttons_layout.addStretch()

    def _create_version_info_panel(self, parent: QWidget):
        """Creates the version info panel at the bottom."""
        version_info_panel = QWidget(parent)
        version_info_panel.setGeometry(20, 550, 200, 30)
        version_info_panel.setStyleSheet("background: transparent; border: none;")
        version_info_layout = QVBoxLayout(version_info_panel)
        version_info_layout.setContentsMargins(0, 0, 0, 0)
        version_info_layout.setSpacing(0)
        label_style = """
        QLabel {
            font-family: \"Roboto Black\";
            font-size: 10px;
            color: #FFFFFF;
            background: transparent;
            qproperty-alignment: AlignCenter;
            margin: 0;
            padding: 0;
        }
        """
        version_label = QLabel("Versión: 2.5")
        snapshot_label = QLabel("Snapshot: U12012026")
        for label in (version_label, snapshot_label):
            label.setStyleSheet(label_style)
            label.setFont(self.roboto_black_font)
            version_info_layout.addWidget(label)

    def _create_button(self, text: str, callback: Any) -> QPushButton:
        """Crea un botón con el estilo moderno Cyber-Glass."""
        button = QPushButton(text)
        # Fallback: Estilo inline directo porque QSS externo está fallando en estos botones específicos
        button.setStyleSheet(
            """
            QPushButton {
                font-size: 13px;
                color: #e0e0e0;
                background-color: rgba(15, 15, 15, 220);
                border: 1px solid rgba(150, 0, 150, 80);
                border-radius: 4px;
                padding: 10px;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background-color: rgba(40, 40, 40, 240);
                border: 1px solid #960096;
                color: white;
            }
            QPushButton:pressed {
                background-color: rgba(60, 60, 60, 250);
            }
            """
        )
        button.setFont(self.adventure_font)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _create_content(self):
        """Crea las áreas principales de contenido. El contenedor es invisible."""
        if self.container:
            self.content_container = QWidget(self.container)
            self.content_container.setGeometry(310, 0, 900, 600)
            self.content_container.setStyleSheet("background: transparent; border: none;") # INVISIBLE
            
            self.home_label = self._create_text_area(
                "BABYLON SCANLATION\n\nEste es el inicio de la aplicación. Aquí puedes acceder a las diferentes secciones desde el menú lateral para gestionar tus proyectos y herramientas.",
                style="""
                    QTextEdit {
                        font-size: 22px; color: white;
                        background-color: rgba(20, 22, 28, 220);
                        padding: 40px; border-radius: 15px;
                        border: 1px solid rgba(157, 70, 255, 0.3);
                    }
                """
            )
            self.utilities_area = self.tools_manager.create_utilities_area()
            self.projects_area = self._create_projects_area()
            self.about_area = self._create_about_area()
            self.help_area = self._create_help_area()
            self.options_area = self.options_menu.create_options_area()
            self.options_area.setParent(self.content_container) # Asegurarse de que sea hijo del content_container
            self.log_console_area = self._create_log_console_area() # Initialize Log Console area
            self.configuration_area = self._create_configuration_area() # Initialize configuration area
            self.gemini_config_area = GeminiConfigPanel(
                self.content_container, 
                self.tools_manager,
                self.super_cartoon_font,
                self.roboto_black_font,
                self.adventure_font
            )
            self.gemini_config_area.closed.connect(self.gemini_config_closed.emit)
            self.gemini_config_area.hide()

            self._hide_all_sections()
            self.show_home()

    def _create_log_console_area(self) -> LogConsole:
        """Crea el área de la consola de logs."""
        console = LogConsole(self.content_container)
        console.setGeometry(50, 50, 780, 500)
        console.hide()
        return console

    def _create_text_area(self, text: str, style: Optional[str] = None) -> QFrame:
        """Crea un área de texto Cyber-Glass garantizando un solo contenedor visible."""
        # Creamos un contenedor que tendrá el borde y el fondo
        frame = QFrame(self.content_container)
        frame.setGeometry(50, 50, 780, 500)
        frame.setObjectName("CyberFrame")
        frame.setStyleSheet(
            """
            #CyberFrame {
                background-color: rgba(20, 22, 28, 220);
                border: 1px solid rgba(157, 70, 255, 0.3);
                border-radius: 15px;
            }
            """
        )
        
        layout = QVBoxLayout(frame)
        text_edit = QTextEdit()
        text_edit.setFrameShape(QFrame.Shape.NoFrame)
        text_edit.setReadOnly(True)
        text_edit.setText(text)
        text_edit.setFont(self.super_cartoon_font)
        # Forzamos transparencia absoluta en el widget de texto
        text_edit.setStyleSheet("background: transparent; border: none; color: white; padding: 20px; font-size: 24px;")
        
        layout.addWidget(text_edit)
        return frame # Retornamos el frame como el objeto de la sección

    def _create_projects_area(self) -> Dict[str, Any]:
        """Crea el área de proyectos con botones de gestión."""
        # pylint: disable=protected-access
        t_manager = cast(Any, self.tools_manager)
        projects_area = {
            "scroll": t_manager._create_scroll_area("projects"),
            "footer": t_manager._create_footer_text("projects"),
        }
        button_container = QWidget(self.content_container)
        button_layout = QHBoxLayout(button_container)
        button_layout.setContentsMargins(69, 8, 0, 0)
        button_layout.setSpacing(25)
        add_button = QPushButton("AÑADIR PROYECTO")
        # Ensure 'scroll' is treated as a widget that has .widget().layout()
        scroll_widget = projects_area["scroll"]
        
        if scroll_widget and isinstance(scroll_widget, QScrollArea) and scroll_widget.widget():
             layout = scroll_widget.widget().layout()
             if isinstance(layout, QGridLayout):
                 add_button.clicked.connect(
                    lambda: self.project_manager.add_project(layout)
                )

        add_button.setFont(self.adventure_font)
        add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        button_layout.addWidget(add_button)
        
        delete_button = QPushButton("QUITAR PROYECTO")
        delete_button.setFont(self.adventure_font)
        delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_button.setCheckable(True)
        delete_button.toggled.connect(self.project_manager.toggle_delete_mode)
        button_layout.addWidget(delete_button)
        projects_area["button_container"] = button_container
        return projects_area

    def _create_help_area(self) -> QScrollArea:
        """Crea el área de ayuda con manejo de errores para miniaturas."""
        scroll_area = QScrollArea(self.content_container)
        scroll_area.setGeometry(50, 50, 780, 500)
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet(
            """
            QScrollArea {
                background: rgba(0, 0, 0, 150);
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
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        grid_layout = QGridLayout(container)
        grid_layout.setContentsMargins(7, 7, 0, 0)
        grid_layout.setHorizontalSpacing(19)
        grid_layout.setVerticalSpacing(0)
        grid_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
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
            grid_layout.addWidget(card, row, col, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            col += 1
            if col >= max_cols:
                col = 0
                row += 1
        grid_layout.setRowStretch(row + 1, 1)
        grid_layout.setColumnStretch(max_cols, 1)
        scroll_area.setWidget(container)
        return scroll_area

    def _create_video_card(self, video: Dict[str, str], thumbnail_width: int, thumbnail_height: int, margin: int) -> QWidget:
        """Crea la tarjeta de video con la nueva miniatura."""
        card = QWidget()
        card.setFixedSize(
            thumbnail_width + 10 + (margin * 2), thumbnail_height + 50 + (margin * 2)
        )
        card.setStyleSheet("background: transparent;")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(5)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        thumbnail_container = self._create_thumbnail_container(
            thumbnail_width, thumbnail_height, margin
        )
        thumbnail_layout = QVBoxLayout(thumbnail_container)
        thumbnail_layout.setContentsMargins(margin, margin, margin, margin)
        thumbnail_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumbnail = self._create_thumbnail_widget(
            thumbnail_width, thumbnail_height, video["id"]
        )
        thumbnail_layout.addWidget(thumbnail)
        title = self._create_video_title(video["title"])
        self._setup_thumbnail_hover_effects(thumbnail_container)
        card_layout.addWidget(thumbnail_container)
        card_layout.addWidget(title)
        return card

    def _create_thumbnail_container(self, width: int, height: int, margin: int) -> QWidget:
        """Crea el contenedor de la miniatura con estilo Cyber-Glass."""
        container = QWidget()
        container.setFixedSize(
            width + (margin * 2), height + (margin * 2)
        )
        container.setStyleSheet(
            """
            background: rgba(20, 20, 20, 200);
            border-radius: 10px;
            border: 1px solid rgba(150, 0, 150, 80);
            """
        )
        return container

    def _create_thumbnail_widget(self, width: int, height: int, video_id: str) -> ClickableThumbnail:
        """Crea la miniatura clickeable usando la subclase."""
        thumbnail = ClickableThumbnail(video_id)
        thumbnail.setFixedSize(width, height)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumbnail.setStyleSheet("background: transparent; border: none;")
        thumbnail.clicked.connect(self._play_video)
        try:
            thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
            image_data = self._download_image(thumbnail_url)
            if image_data:
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)
                scaled = pixmap.scaled(
                    width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                thumbnail.setPixmap(scaled)
            else:
                thumbnail.setText("Miniatura no disponible")
                thumbnail.setStyleSheet("color: white; font-size: 12px;")
        except (requests.RequestException, IOError):
            thumbnail.setText("Error al cargar")
            thumbnail.setStyleSheet("color: white; font-size: 12px;")
        return thumbnail

    def _create_video_title(self, title_text: str) -> QLabel:
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

    def _setup_thumbnail_hover_effects(self, thumbnail_container: QWidget):
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

    def _download_image(self, url: str) -> bytes:
        """Descarga imágenes desde una URL."""
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException:
            return b""

    def _play_video(self, video_id: str):
        """Reproduce un video de YouTube en el navegador."""
        webbrowser.open(f"https://www.youtube.com/watch?v={video_id}")

    def _create_about_area(self) -> QFrame:
        """Crea el área 'Sobre esto' con diseño Cyber-Glass."""
        return self._create_text_area(
            Config.ABOUT_TEXT + "\n\nEste software ha sido desarrollado para centralizar herramientas de scanlation, permitiendo procesos de OCR y traducción mediante diversos servicios de inteligencia artificial y motores tradicionales.",
            style="""
                QTextEdit {
                    font-size: 24px; color: white;
                    background-color: rgba(20, 22, 28, 220);
                    padding: 30px; border-radius: 15px;
                        border: 1px solid rgba(157, 70, 255, 0.3);
                    }
                """
        )

    def _create_configuration_area(self) -> QWidget:
        """Crea el área de configuración con diseño Cyber-Glass."""
        config_area = QWidget(self.content_container)
        config_area.setObjectName("MainConfigPanel")
        config_area.setGeometry(50, 50, 780, 500)
        config_area.setStyleSheet(
            """
            #MainConfigPanel {
                background-color: rgba(0, 0, 0, 150);
                border: 1px solid rgba(150, 0, 150, 100);
                border-radius: 15px;
            }
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
        model_combo.addItems(["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite"])
        model_combo.setCurrentText(Config.GEMINI_MODEL)
        model_combo.setStyleSheet(
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
        model_combo.setFont(self.roboto_black_font)
        model_combo.currentIndexChanged.connect(
            lambda: self._save_gemini_settings(
                model_name=model_combo.currentText(), 
                enable_thinking=self.thinking_checkbox.isChecked() if self.thinking_checkbox else Config.GEMINI_ENABLE_THINKING, 
                enable_auto_switch=self.auto_switch_checkbox.isChecked() if self.auto_switch_checkbox else Config.ENABLE_AUTO_MODEL_SWITCH,
                system_instruction=Config.GEMINI_SYSTEM_INSTRUCTION
            )
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
            lambda: self._save_gemini_settings(
                model_name=model_combo.currentText(), 
                enable_thinking=self.thinking_checkbox.isChecked() if self.thinking_checkbox else Config.GEMINI_ENABLE_THINKING, 
                enable_auto_switch=self.auto_switch_checkbox.isChecked() if self.auto_switch_checkbox else Config.ENABLE_AUTO_MODEL_SWITCH,
                system_instruction=Config.GEMINI_SYSTEM_INSTRUCTION
            )
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

    def _update_volume(self, value: int):
        if hasattr(self, 'audio_output') and self.audio_output:
            self.audio_output.setVolume(value / 100.0)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_music(self, enabled: bool):
        if self.audio_player:
            if enabled:
                self.audio_player.play()
            else:
                self.audio_player.pause()

    def _handle_bg_type_change(self, bg_type: str):
        if self.background_manager:
            self.background_manager.start_background(bg_type)

    def _delete_file_with_retries(self, file_path: str, retries: int = 3, delay: int = 1):
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
        """Oculta controles específicos de traducción y sus paneles."""
        tools_manager = self.tools_manager
        if not tools_manager:
            return

        if hasattr(tools_manager, "header_panel") and tools_manager.header_panel:
            tools_manager.header_panel.hide()
        
        if hasattr(tools_manager, "footer_panel") and tools_manager.footer_panel:
            tools_manager.footer_panel.hide()

        controls_to_hide: List[Any] = [
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

    def _get_optional_control(self, manager: Any, attr_name: str) -> Optional[Any]:
        """Obtiene un control opcional si existe."""
        return getattr(manager, attr_name, None)

    def _hide_main_sections(self):
        """Oculta las secciones principales de la UI."""
        self._hide_widgets(["home_label", "help_area", "options_area", "about_area", "configuration_area", "gemini_config_area", "log_console_area"])
        self._hide_utilities_area()
        self._hide_projects_area()
        # Asegurarse de ocultar contenedores dinámicos de ToolsManager
        if hasattr(self.tools_manager, 'parent_container') and self.tools_manager.parent_container:
            self.tools_manager.parent_container.hide()
        if hasattr(self.tools_manager, 'gemini_container') and self.tools_manager.gemini_container:
            self.tools_manager.gemini_container.hide()
        if hasattr(self.tools_manager, 'mistral_container') and self.tools_manager.mistral_container:
            self.tools_manager.mistral_container.hide()

    def show_gemini_configuration(self):
        """Muestra la sección de configuración de Gemini."""
        self._hide_all_sections()
        if self.gemini_config_area:
            self.gemini_config_area.show()

    def _hide_special_containers(self):
        """Maneja la ocultación de contenedores especializados."""
        self._hide_and_delete_containers()

    def _hide_widgets(self, widget_attrs: List[str]):
        """Oculta varios adminículos si existen."""
        for widget_attr in widget_attrs:
            self._hide_widget(widget_attr)

    def _hide_utilities_area(self):
        """Oculta la zona de utilidades si existe."""
        u_area = self.utilities_area
        if u_area:
            if "scroll" in u_area:
                u_area["scroll"].hide()
            if "footer" in u_area:
                u_area["footer"].hide()

    def _hide_and_delete_containers(self):
        """Oculta y elimina los contenedores específicos, preservando los de IA."""
        self._hide_and_delete_container("parent_container", None)
        self._hide_and_delete_container("details_container", None)
        # Preservar contenedores de IA (solo ocultar)
        self._hide_container_only("gemini_container", None)
        self._hide_and_delete_container("parent_container", self.tools_manager)
        # Preservar contenedores de IA en tools_manager
        self._hide_container_only("gemini_container", self.tools_manager)
        self._hide_container_only("mistral_container", self.tools_manager)

    def _hide_projects_area(self):
        """Oculta la zona de proyectos si existe."""
        p_area = self.projects_area
        if p_area:
            if "scroll" in p_area:
                p_area["scroll"].hide()
            if "footer" in p_area:
                p_area["footer"].hide()
            if "button_container" in p_area:
                p_area["button_container"].hide()

    def _hide_widget(self, widget_attr: str):
        """Oculta el adminículo si existe."""
        widget = getattr(self, widget_attr, None)
        if widget:
            widget.hide()

    def _hide_container_only(self, attr: str, manager: Any):
        """Oculta el contenedor sin eliminarlo para mantener el estado."""
        if manager is None:
            manager = self
        container = getattr(manager, attr, None)
        if container:
            container.hide()

    def _hide_and_delete_container(self, attr: str, manager: Any):
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
        if self.home_label:
            self.home_label.show()

    def show_utilities(self):
        """Muestra la sección de herramientas."""
        self._hide_all_sections()
        u_area = self.utilities_area
        if u_area:
            if "scroll" in u_area:
                u_area["scroll"].show()
            if "footer" in u_area:
                u_area["footer"].show()
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
        p_area = self.projects_area
        if p_area:
            if "scroll" in p_area:
                p_area["scroll"].show()
            if "footer" in p_area:
                p_area["footer"].show()
            if "button_container" in p_area:
                p_area["button_container"].show()

    def show_help(self):
        """Muestra la sección de ayuda."""
        self._hide_all_sections()
        if self.help_area:
            self.help_area.show()

    def show_console(self):
        """Muestra la consola de logs."""
        self._hide_all_sections()
        self.log_console_area.show()

    def show_options(self):
        """Muestra la sección de opciones."""
        self._hide_all_sections()
        if self.options_area:
            self.options_area.show()

    def hide_options_menu(self):
        """Oculta el menú de opciones y vuelve al inicio."""
        self.show_home()

    def show_configuration(self):
        """Muestra la sección de configuración."""
        self._hide_all_sections()
        if not self.configuration_area:
            self.configuration_area = self._create_configuration_area()
        if self.configuration_area:
            self.configuration_area.show()

    def show_about(self):
        """Muestra la sección 'Sobre esto'."""
        self._hide_all_sections()
        if self.about_area is not None:
            self.about_area.show()


if __name__ == "__main__":
    # Silenciar advertencias de bajo nivel (FFmpeg/C++) redirigiendo stderr a null
    # try:
    #     # Abrir el "agujero negro" del sistema (NUL en Windows, /dev/null en Unix)
    #     devnull = os.open(os.devnull, os.O_WRONLY)
    #     # Guardar el stderr original por si acaso (aunque no lo restauraremos)
    #     old_stderr = os.dup(sys.stderr.fileno())
    #     # Redirigir stderr (descriptor 2) a devnull
    #     os.dup2(devnull, sys.stderr.fileno())
    #     # Cerrar el handle auxiliar
    #     os.close(devnull)
    # except Exception as e:
    #     pass # Si falla la redirección, seguimos igual

    app = QApplication(sys.argv)
    
    # Cargar estilos globales
    qss_path = os.path.join("styles", "main.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
            
    # 2. ACTIVAR SEÑALES DE LOGGING (Una vez que existe QApplication)
    from log_console import init_signals
    init_signals()
            
    window = App()
    window.show()
    sys.exit(app.exec())
