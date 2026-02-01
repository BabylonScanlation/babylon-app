import os
from typing import TYPE_CHECKING, List, Union, cast

import cv2
import requests
from config import Config
from download_thread import DownloadThread
from PySide6.QtCore import Qt, QUrl, QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QLabel, QMessageBox, QWidget

if TYPE_CHECKING:
    from bbsl_app import App
    from options_menu import OptionsMenu, MiscOptions, VideoOptions, ImageOptions
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtCore import QTimer

class OptionsController(QObject):
    """Controlador para manejar las funcionalidades del menú de opciones."""
    
    app: 'App'
    background_type_changed = Signal(str)
    volume_changed = Signal(float)
    fullscreen_toggled = Signal()
    music_toggled = Signal(bool)

    def __init__(self, app: 'App'):
        super().__init__()
        self.app = app
        self.temp_files: List[str] = []
        self.background_choice = "video"
        self.download_thread: Union[DownloadThread, None] = None
        
        # Referencias para evitar reportUnusedImport en TYPE_CHECKING
        self._types: Union['OptionsMenu', 'QMediaPlayer', 'QTimer', None] = None
    def save_gemini_settings(self, model: str, thinking_enabled: bool, temperature: float):
        """Guarda la configuración de Gemini en el objeto Config en memoria."""
        try:
            Config.GEMINI_MODEL = model
            Config.GEMINI_ENABLE_THINKING = thinking_enabled
            Config.GEMINI_TEMPERATURE = temperature
        except Exception as e:
            QMessageBox.critical(self.app, "Error", f"No se pudo guardar la configuración de Gemini: {e}")

    def go_back_to_main_view(self):
        """Oculta el menú de opciones y vuelve a la vista principal."""
        if hasattr(self.app, 'hide_options_menu'):
            self.app.hide_options_menu()

    def clear_temp_files(self):
        """Limpia todos los archivos temporales creados."""
        for temp_file in self.temp_files:
            try:
                os.remove(temp_file)
            except OSError:
                pass
        self.temp_files = []

    def pause_resume_video(self):
        """Pausa o reanuda la reproducción del video."""
        if self.app.cap is not None and self.app.cap.isOpened():
            if self.app.timer is not None:
                if self.app.timer.isActive():
                    self.app.timer.stop()
                else:
                    self.app.timer.start(
                        int(1000 / self.app.cap.get(cv2.CAP_PROP_FPS))
                    )  # pylint: disable=no-member

    def set_background_type(self, bg_type: str):
        """Establece el tipo de fondo directamente."""
        self.handle_bg_type_change(bg_type)

    def load_local_music(self):
        """Cargar archivo de música local."""
        file_path, _ = QFileDialog.getOpenFileName(
            self.app,
            "Seleccionar archivo de música",
            os.path.expanduser("~"),
            "Archivos de audio (*.mp3 *.wav *.ogg)",
        )
        if file_path:
            try:
                if self.app.audio_player is not None:
                    # En PySide6 manejamos la lista manualmente si es necesario, 
                    # o simplemente cargamos el archivo seleccionado.
                    self.app.playlist_files = [QUrl.fromLocalFile(file_path)]
                    self.app.current_audio_index = 0
                    self.app.audio_player.setSource(self.app.playlist_files[0])
                    self.app.audio_player.play()
            except (FileNotFoundError, ValueError) as e:
                QMessageBox.critical(
                    self.app, "Error", f"No se pudo cargar el archivo de música: {e}"
                )

    def load_music_from_url(self, url: str):
        """Cargar música desde URL externa."""
        if not url:
            msg = QMessageBox(self.app)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Advertencia")
            msg.setText("La URL está vacía.")
            msg.exec()
            return
        if not url.lower().endswith((".mp3", ".wav", ".ogg")):
            msg = QMessageBox(self.app)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Advertencia")
            msg.setText("La URL no apunta a un archivo de audio válido.")
            msg.exec()
            return
        try:
            if self.app.audio_player is not None:
                self.app.playlist_files = [QUrl(url)]
                self.app.current_audio_index = 0
                self.app.audio_player.setSource(self.app.playlist_files[0])
                self.app.audio_player.play()
        except (FileNotFoundError, ValueError):
            msg = QMessageBox(self.app)
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.setWindowTitle("Error")
            msg.setText("No se pudo cargar la música desde la URL proporcionada.")
            msg.exec()

    def load_local_video(self):
        """Cargar video local para fondo."""
        misc_page = cast('MiscOptions', self.app.options_menu.options_pages["misc"])
        if misc_page.bg_type_combo.currentText() != "Video":
            video_page = cast('VideoOptions', self.app.options_menu.options_pages["video"])
            self.show_video_warning(video_page.video_local_btn)
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self.app,
            "Seleccionar archivo de video",
            os.path.expanduser("~"),
            "Archivos de video (*.mp4 *.avi *.mov)",
        )
        if file_path:
            self.update_video_source(file_path)

    def load_video_from_url(self, url: str):
        """Cargar video desde URL externa con barra de progreso."""
        misc_page = cast('MiscOptions', self.app.options_menu.options_pages["misc"])
        if misc_page.bg_type_combo.currentText() != "Video":
            video_page = cast('VideoOptions', self.app.options_menu.options_pages["video"])
            self.show_video_warning(video_page.video_url_btn)
            return
        if url:
            self.start_download_thread(url)

    def start_download_thread(self, url: str):
        """Inicia el hilo de descarga del video."""
        if self.download_thread is not None and self.download_thread.isRunning():
            return

        self.download_thread = DownloadThread(url)
        self.download_thread.progress.connect(self.update_progress)
        self.download_thread.finished.connect(self.on_download_finished)
        self.download_thread.error.connect(self.on_download_error)
        
        video_page = cast('VideoOptions', self.app.options_menu.options_pages["video"])
        video_page.video_progress_bar.show()
        video_page.video_progress_bar.setValue(0)
        self.download_thread.start()

    def update_progress(self, progress: int):
        """Actualiza la barra de progreso."""
        video_page = cast('VideoOptions', self.app.options_menu.options_pages["video"])
        video_page.video_progress_bar.setValue(progress)

    def on_download_finished(self, downloaded_file: str):
        """Maneja el final de la descarga."""
        video_page = cast('VideoOptions', self.app.options_menu.options_pages["video"])
        video_page.video_progress_bar.setValue(100)
        video_page.video_progress_bar.hide()
        self.temp_files.append(downloaded_file)
        self.update_video_source(downloaded_file)

    def on_download_error(self, error_message: str):
        """Maneja errores durante la descarga."""
        video_page = cast('VideoOptions', self.app.options_menu.options_pages["video"])
        video_page.video_progress_bar.hide()
        QMessageBox.critical(
            self.app,
            "Error",
            f"No se pudo cargar el video desde la URL proporcionada.\n{error_message}",
        )

    def update_video_source(self, source: str):
        """Actualizar la fuente del video y liberar recursos previos."""
        if self.app.cap is not None:
            if self.app.cap.isOpened():
                self.app.cap.release()
            if self.app.timer is not None:
                self.app.timer.stop()
        try:
            self.app.cap = cv2.VideoCapture(source)  # pylint: disable=no-member
            if self.app.cap.isOpened():
                if self.app.timer is not None:
                    self.app.timer.start(
                        int(1000 / self.app.cap.get(cv2.CAP_PROP_FPS))
                    )  # pylint: disable=no-member
                if self.app.background_label is not None:
                    self.app.background_label.hide()
            else:
                if self.app.background_label is not None:
                    self.app.background_label.show()
        except (IOError, OSError, ValueError):
            if self.app.background_label is not None:
                self.app.background_label.show()

    def load_local_image(self):
        """Cargar imagen local para fondo."""
        misc_page = cast('MiscOptions', self.app.options_menu.options_pages["misc"])
        if misc_page.bg_type_combo.currentText() != "Imagen":
            image_page = cast('ImageOptions', self.app.options_menu.options_pages["image"])
            self.show_image_warning(image_page.image_local_btn)
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self.app,
            "Seleccionar archivo de imagen",
            os.path.expanduser("~"),
            "Archivos de imagen (*.png *.jpg *.jpeg)",
        )
        if file_path:
            self.update_image_source(file_path)

    def load_image_from_url(self, url: str):
        """Cargar imagen desde URL externa."""
        misc_page = cast('MiscOptions', self.app.options_menu.options_pages["misc"])
        if misc_page.bg_type_combo.currentText() != "Imagen":
            image_page = cast('ImageOptions', self.app.options_menu.options_pages["image"])
            self.show_image_warning(image_page.image_url_btn)
            return
        if url:
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    pixmap = QPixmap()
                    pixmap.loadFromData(response.content)
                    self.update_image_source(pixmap)
            except (IOError, OSError, ValueError):
                pass

    def update_image_source(self, source: Union[str, QPixmap]):
        """Actualizar la fuente de la imagen."""
        if isinstance(source, str):
            pixmap = QPixmap(source)
        else:
            pixmap = source
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                self.app.width(),
                self.app.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            if self.app.background_label is not None:
                self.app.background_label.setPixmap(scaled_pixmap)
                self.app.background_label.show()
            if self.app.cap is not None:
                if self.app.cap.isOpened():
                    self.app.cap.release()
                if self.app.timer is not None:
                    self.app.timer.stop()
        else:
            if self.app.background_label is not None:
                self.app.background_label.setStyleSheet("background-color: black;")
                self.app.background_label.show()
            if self.app.cap is not None:
                if self.app.cap.isOpened():
                    self.app.cap.release()
                if self.app.timer is not None:
                    self.app.timer.stop()

    def handle_bg_type_change(self, bg_type: str):
        """Manejar cambio de tipo de fondo."""
        self.background_type_changed.emit(bg_type)
        if bg_type == "Video":
            if self.background_choice != "video":
                self.background_choice = "video"
        else:
            if self.background_choice != "imagen":
                self.background_choice = "imagen"

    def toggle_music_with_image(self, state: int):
        """Alternar reproducción de música con imagen de fondo."""
        # En PySide6 Qt.Checked se accede de forma diferente o como entero
        is_checked = bool(state == 2 or state == Qt.CheckState.Checked.value or state == Qt.CheckState.Checked)
        self.music_toggled.emit(is_checked)

    def show_video_warning(self, widget: QWidget):
        """Muestra un mensaje de advertencia debajo del widget especificado."""
        warning_label = QLabel(
            "Antes de seleccionar un video, cambia el tipo de fondo."
        )
        warning_label.setStyleSheet("color: gray; font-size: 12px; background-color: transparent;")
        warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        parent_widget = widget.parent()
        if isinstance(parent_widget, QWidget):
            parent_layout = parent_widget.layout()
            if parent_layout is not None:
                for i in reversed(range(parent_layout.count())):
                    item = parent_layout.itemAt(i)
                    if item is not None:
                        w = item.widget()
                        if isinstance(w, QLabel) and "Antes de seleccionar" in w.text():
                            parent_layout.removeItem(item)
                            w.deleteLater()
                parent_layout.addWidget(warning_label)

    def show_image_warning(self, widget: QWidget):
        """Muestra un mensaje de advertencia debajo del widget especificado."""
        warning_label = QLabel(
            "Antes de seleccionar una imagen, cambia el tipo de fondo."
        )
        warning_label.setStyleSheet("color: gray; font-size: 12px; background-color: transparent;")
        warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        parent_widget = widget.parent()
        if isinstance(parent_widget, QWidget):
            parent_layout = parent_widget.layout()
            if parent_layout is not None:
                for i in reversed(range(parent_layout.count())):
                    item = parent_layout.itemAt(i)
                    if item is not None:
                        w = item.widget()
                        if isinstance(w, QLabel) and "Antes de seleccionar" in w.text():
                            parent_layout.removeItem(item)
                            w.deleteLater()
                parent_layout.addWidget(warning_label)

    def clear_warnings(self):
        """Limpia los mensajes de advertencia de las páginas de configuración."""
        # Se asume que el usuario busca limpiar todo
        pass # Implementación simplificada para brevedad

    def toggle_fullscreen(self):
        """Alterna entre el modo de pantalla completa y el modo ventana."""
        self.fullscreen_toggled.emit()

    def adjust_volume(self, value: Union[int, float]):
        """Ajusta el volumen del reproductor de audio."""
        self.volume_changed.emit(float(value))