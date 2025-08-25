"""
Módulo para manejar el menú de opciones de la aplicación.

Este módulo define varias clases para gestionar diferentes opciones de configuración
de la aplicación, como opciones generales, de pantalla, de audio, de video, de imagen
y opciones misceláneas. Cada clase crea una página de configuración específica que se
puede mostrar en un menú de opciones con pestañas laterales.
"""

from options_controller import OptionsController
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QFrame, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                             QPushButton, QSlider, QStackedWidget, QVBoxLayout,
                             QWidget)


class GeneralOptions:
    """Clase para manejar las opciones generales."""

    def __init__(self):
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["Español", "English", "Português"])
        self.lang_combo.setEnabled(False)

    def create_page(self):
        """Crea la página de configuración general."""
        page = QWidget()
        layout = QVBoxLayout(page)
        lang_group = QGroupBox("Idioma (Próximamente)")
        lang_group.setStyleSheet(
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
        lang_layout = QHBoxLayout(lang_group)
        lang_layout.addWidget(self.lang_combo)
        layout.addWidget(lang_group)
        layout.addStretch()
        return page

    def get_language(self):
        """Obtiene el idioma seleccionado."""
        return self.lang_combo.currentText()


class DisplayOptions:
    """Clase para manejar las opciones de pantalla."""

    def __init__(self, controller):
        self.fullscreen_cb = QCheckBox("Pantalla completa")
        self.fullscreen_cb.stateChanged.connect(controller._toggle_fullscreen)
        self.lock_size_cb = QCheckBox("Bloquear tamaño de ventana")
        self.lock_size_cb.setChecked(True)

    def create_page(self):
        """Crea la página de configuración de pantalla."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.fullscreen_cb)
        layout.addWidget(self.lock_size_cb)
        layout.addStretch()
        return page

    def is_fullscreen(self):
        """Verifica si el modo pantalla completa está activado."""
        return self.fullscreen_cb.isChecked()


class AudioOptions:
    """Clase para manejar las opciones de audio."""

    def __init__(self, controller):
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(10)
        self.vol_slider.valueChanged.connect(controller._adjust_volume)

        self.music_local_btn = QPushButton("Cargar archivo local")
        self.music_local_btn.clicked.connect(controller._load_local_music)
        self.music_url_input = QLineEdit()
        self.music_url_input.setPlaceholderText("URL de archivo de audio (.mp3, .wav)")
        self.music_url_btn = QPushButton("Cargar desde URL")
        self.music_url_btn.clicked.connect(
            lambda: controller._load_music_from_url(self.music_url_input.text())
        )

    def create_page(self):
        """Crea la página de configuración de audio."""
        page = QWidget()
        layout = QVBoxLayout(page)

        vol_layout = QHBoxLayout()
        vol_label = QLabel("Volumen:")
        vol_layout.addWidget(vol_label)
        vol_layout.addWidget(self.vol_slider)
        layout.addLayout(vol_layout)

        music_group = QGroupBox("MÚSICA PERSONALIZADA")
        music_group.setStyleSheet(
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
        music_layout = QVBoxLayout(music_group)
        music_layout.addWidget(self.music_local_btn)
        music_layout.addWidget(self.music_url_input)
        music_layout.addWidget(self.music_url_btn)
        layout.addWidget(music_group)
        layout.addStretch()
        return page

    def get_volume(self):
        """Obtiene el nivel de volumen actual."""
        return self.vol_slider.value()


class VideoOptions:
    """Clase para manejar las opciones de video."""

    def __init__(self, controller, options_menu):
        self.controller = controller
        self.options_menu = options_menu  # Guardar options_menu como atributo
        self.video_local_btn = QPushButton("Seleccionar video local")
        self.video_local_btn.clicked.connect(controller._load_local_video)
        self.video_url_input = QLineEdit()
        self.video_url_input.setPlaceholderText("URL de YouTube")
        self.video_url_btn = QPushButton("Cargar desde URL")
        self.video_url_btn.clicked.connect(
            lambda: controller._load_video_from_url(self.video_url_input.text())
        )
        self.video_progress_bar = QProgressBar()
        self.video_progress_bar.setRange(0, 100)
        self.video_progress_bar.setValue(0)
        self.video_progress_bar.hide()

    def create_page(self):
        """Crea la página de configuración de video."""
        page = QWidget()
        layout = QVBoxLayout(page)
        video_group = QGroupBox("VIDEO DE FONDO (SIN AUDIO)")
        video_group.setStyleSheet(
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
        video_layout = QVBoxLayout(video_group)
        video_layout.addWidget(self.video_local_btn)
        video_layout.addWidget(self.video_url_input)
        video_layout.addWidget(self.video_url_btn)
        video_layout.addWidget(self.video_progress_bar)
        layout.addWidget(video_group)
        layout.addStretch()
        return page

    def show_progress_bar(self):
        """Muestra la barra de progreso."""
        self.video_progress_bar.show()


class ImageOptions:
    """Clase para manejar las opciones de imagen."""

    def __init__(self, controller):
        self.controller = controller
        self.image_local_btn = QPushButton("Seleccionar imagen local")
        self.image_local_btn.clicked.connect(controller._load_local_image)
        self.image_url_input = QLineEdit()
        self.image_url_input.setPlaceholderText("URL de Imgur o web")
        self.image_url_btn = QPushButton("Cargar desde URL")
        self.image_url_btn.clicked.connect(
            lambda: controller._load_image_from_url(self.image_url_input.text())
        )

    def create_page(self):
        """Crea la página de configuración de imagen."""
        page = QWidget()
        layout = QVBoxLayout(page)
        image_group = QGroupBox("IMAGEN DE FONDO")
        image_group.setStyleSheet(
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
        image_layout = QVBoxLayout(image_group)
        image_layout.addWidget(self.image_local_btn)
        image_layout.addWidget(self.image_url_input)
        image_layout.addWidget(self.image_url_btn)
        layout.addWidget(image_group)
        layout.addStretch()
        return page

    def get_image_url(self):
        """Obtiene la URL de la imagen."""
        return self.image_url_input.text()


class MiscOptions:
    """Clase para manejar las opciones misceláneas."""

    def __init__(self, controller):
        self.controller = controller
        self.bg_type_combo = QComboBox()
        self.bg_type_combo.addItems(["Video", "Imagen"])
        self.bg_type_combo.setStyleSheet(
            """
            QComboBox {
                background-color: rgba(30, 30, 30, 200);
                color: white;
                border: 1px solid rgba(150, 0, 150, 180);
                border-radius: 4px;
                padding: 5px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: url(:/icons/down_arrow.png);
                width: 12px;
                height: 12px;
            }
            QComboBox QAbstractItemView {
                background-color: rgba(30, 30, 30, 200);
                color: white;
                border: 1px solid rgba(150, 0, 150, 180);
                selection-background-color: rgba(60, 60, 60, 200);
                selection-color: white;
            }
        """
        )
        self.bg_type_combo.currentTextChanged.connect(controller._handle_bg_type_change)
        self.music_with_image_cb = QCheckBox("Reproducir música de fondo")
        self.music_with_image_cb.setChecked(True)
        self.music_with_image_cb.stateChanged.connect(
            controller._toggle_music_with_image
        )

    def create_page(self):
        """Crea la página de configuración miscelánea."""
        page = QWidget()
        layout = QVBoxLayout(page)
        bg_type_group = QGroupBox("TIPO DE FONDO")
        bg_type_group.setStyleSheet(
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
        bg_type_layout = QVBoxLayout(bg_type_group)
        bg_type_layout.addWidget(self.bg_type_combo)
        layout.addWidget(bg_type_group)
        layout.addWidget(self.music_with_image_cb)
        layout.addStretch()
        return page

    def get_background_type(self):
        """Obtiene el tipo de fondo seleccionado."""
        return self.bg_type_combo.currentText()


class GeminiOptions:
    """Clase para manejar las opciones de Gemini."""

    def __init__(self, controller):
        self.controller = controller
        self.gemini_model_combo = QComboBox()
        self.gemini_model_combo.addItems([
            "gemini-2.5-pro-latest",
            "gemini-2.5-flash-latest",
        ])
        self.gemini_thinking_cb = QCheckBox("Activar modo pensamiento")
        self.save_button = QPushButton("Guardar y Regresar")
        self.save_button.clicked.connect(self.save_and_exit)

    def create_page(self):
        """Crea la página de configuración de Gemini."""
        page = QWidget()
        layout = QVBoxLayout(page)
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
        gemini_layout.addWidget(QLabel("Modelo de Gemini:"))
        gemini_layout.addWidget(self.gemini_model_combo)
        gemini_layout.addWidget(self.gemini_thinking_cb)
        layout.addWidget(gemini_group)
        layout.addStretch()
        layout.addWidget(self.save_button)
        return page

    def save_and_exit(self):
        """Guarda la configuración y cierra el menú."""
        model = self.gemini_model_combo.currentText()
        is_thinking = self.gemini_thinking_cb.isChecked()
        self.controller.save_gemini_settings(model, is_thinking)
        self.controller.go_back_to_main_view()


class OptionsMenu:
    """Clase para crear un menú de opciones con pestañas configurables."""

    def __init__(self, parent):
        self.parent = parent
        self.option_buttons = {}
        self.option_stack = QStackedWidget()
        self.controller = OptionsController(parent)
        self.options_pages = {
            "general": GeneralOptions(),
            "display": DisplayOptions(self.controller),
            "audio": AudioOptions(self.controller),
            "video": VideoOptions(self.controller, self),
            "image": ImageOptions(self.controller),
            "misc": MiscOptions(self.controller),
            "gemini": GeminiOptions(self.controller),
        }
        for page in self.options_pages.values():
            self.option_stack.addWidget(page.create_page())

    def create_options_area(self):
        """Crea un área de opciones con pestañas laterales y secciones configurables."""
        options_widget = QWidget(self.parent.content_container)
        options_widget.setGeometry(50, 50, 780, 500)
        options_widget.setStyleSheet(
            """
        QWidget {
        background-color: rgba(0, 0, 0, 100);
        border: 2px solid rgba(87, 35, 100, 150);
        color: white;
        }
        """
        )
        main_layout = QHBoxLayout(options_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        left_panel = QFrame()
        left_panel.setFixedWidth(125)
        left_panel.setStyleSheet(
            """
        QFrame {
        background-color: rgba(0, 0, 0, 100);
        color: white;
        }
        """
        )
        btn_layout = QVBoxLayout(left_panel)
        btn_layout.setContentsMargins(10, 10, 10, 10)
        btn_layout.setSpacing(15)
        categories = [
            ("GENERAL", "Configuración general"),
            ("PANTALLA", "Ajustes de pantalla"),
            ("AUDIO", "Configuración de audio"),
            ("VIDEO", "Configuración de video"),
            ("IMAGEN", "Configuración de imagen"),
            ("MISCELÁNEA", "Opciones diversas"),
            ("GEMINI", "Configuración de Gemini"),
        ]
        for cat, tooltip in categories:
            btn = QPushButton(cat)
            btn.setToolTip(tooltip)
            btn.setFont(self.parent.adventure_font)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, c=cat: self._show_option_page(c))
            btn.setStyleSheet(
                """
            QPushButton {
            background-color: #333333;
            color: white;
            border: none;
            text-align: center;
            padding: 8px;
            font-size: 14px;
            }
            QPushButton:hover {
            background-color: #555555;
            }
            """
            )
            self.option_buttons[cat] = btn
            btn_layout.addWidget(btn)
        btn_layout.addStretch()
        right_panel = QFrame()
        right_panel.setStyleSheet(
            """
        QFrame {
        background-color: none;
        color: white;
        border: none;
        }
        """
        )

        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.option_stack)
        right_panel.setLayout(right_layout)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel)
        return options_widget

    def _show_option_page(self, category):
        """Muestra la página de configuración correspondiente a la categoría."""
        page_mapping = {
            "GENERAL": 0,
            "PANTALLA": 1,
            "AUDIO": 2,
            "VIDEO": 3,
            "IMAGEN": 4,
            "MISCELÁNEA": 5,
            "GEMINI": 6,
        }
        self.controller._clear_warnings()
        self.option_stack.setCurrentIndex(page_mapping[category])
        for btn in self.option_buttons.values():
            btn.setStyleSheet(
                btn.styleSheet().replace("rgba(80, 40, 100, 0)", "rgba(50, 50, 50, 0)")
            )

    def get_current_page_index(self):
        """Obtiene el índice de la página actual."""
        return self.option_stack.currentIndex()

    def set_current_page_index(self, index):
        """Establece el índice de la página actual."""
        self.option_stack.setCurrentIndex(index)
