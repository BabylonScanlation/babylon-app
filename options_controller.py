"""
Controlador para manejar las funcionalidades del menú de opciones.
"""

import os

import cv2
import requests
from config import Config
from download_thread import DownloadThread
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QPixmap
from PyQt5.QtMultimedia import QMediaContent
from PyQt5.QtWidgets import QFileDialog, QLabel, QMessageBox


class OptionsController:
    """Controlador para manejar las funcionalidades del menú de opciones."""

    def __init__(self, parent):
        self.parent = parent
        self.temp_files = []
        self.background_choice = "video"
        self.download_thread = None

    def clear_temp_files(self):
        """Limpia todos los archivos temporales creados."""
        for temp_file in self.temp_files:
            try:
                os.remove(temp_file)
            except OSError as e:
                print(f"Error al eliminar archivo temporal: {str(e)}")
        self.temp_files = []

    def pause_resume_video(self):
        """Pausa o reanuda la reproducción del video."""
        if hasattr(self.parent, "cap") and self.parent.cap.isOpened():
            if self.parent.timer.isActive():
                self.parent.timer.stop()
            else:
                self.parent.timer.start(
                    int(1000 / self.parent.cap.get(cv2.CAP_PROP_FPS))
                )  # pylint: disable=no-member

    def set_background_type(self, bg_type):
        """Establece el tipo de fondo directamente."""
        self._handle_bg_type_change(bg_type)

    def _load_local_music(self):
        """Cargar archivo de música local."""
        file_path, _ = QFileDialog.getOpenFileName(
            self.parent,
            "Seleccionar archivo de música",
            os.path.expanduser("~"),
            "Archivos de audio (*.mp3 *.wav *.ogg)",
        )
        if file_path:
            try:
                self.parent.playlist.clear()
                self.parent.playlist.addMedia(
                    QMediaContent(QUrl.fromLocalFile(file_path))
                )
                self.parent.audio_player.play()
            except (FileNotFoundError, ValueError) as e:
                print(f"Error cargando música local: {str(e)}")
                QMessageBox.critical(
                    self.parent, "Error", "No se pudo cargar el archivo de música."
                )

    def _load_music_from_url(self, url):
        """Cargar música desde URL externa."""
        if not url:
            msg = QMessageBox(self.parent)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Advertencia")
            msg.setText("La URL está vacía.")
            msg.exec_()
            return
        if not url.lower().endswith((".mp3", ".wav", ".ogg")):
            msg = QMessageBox(self.parent)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Advertencia")
            msg.setText("La URL no apunta a un archivo de audio válido.")
            msg.exec_()
            return
        try:
            self.parent.playlist.clear()
            self.parent.playlist.addMedia(QMediaContent(QUrl(url)))
            self.parent.audio_player.play()
        except (FileNotFoundError, ValueError) as e:
            print(f"Error cargando música desde URL: {str(e)}")
            msg = QMessageBox(self.parent)
            msg.setIcon(QMessageBox.Critical)
            msg.setWindowTitle("Error")
            msg.setText("No se pudo cargar la música desde la URL proporcionada.")
            msg.exec_()

    def _load_local_video(self):
        """Cargar video local para fondo."""
        if (
            self.parent.options_menu.options_pages["misc"].bg_type_combo.currentText()
            != "Video"
        ):
            self._show_video_warning(self.parent.options_menu.video_local_btn)
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self.parent,
            "Seleccionar archivo de video",
            os.path.expanduser("~"),
            "Archivos de video (*.mp4 *.avi *.mov)",
        )
        if file_path:
            self._update_video_source(file_path)

    def _load_video_from_url(self, url):
        """Cargar video desde URL externa con barra de progreso."""
        if (
            self.parent.options_menu.options_pages["misc"].bg_type_combo.currentText()
            != "Video"
        ):
            self._show_video_warning(self.parent.options_menu.video_url_btn)
            return
        if url:
            self._start_download_thread(url)

    def _start_download_thread(self, url):
        """Inicia el hilo de descarga del video."""
        if self.download_thread is not None and self.download_thread.isRunning():
            return

        self.download_thread = DownloadThread(url)
        self.download_thread.progress.connect(self._update_progress)
        self.download_thread.finished.connect(self._on_download_finished)
        self.download_thread.error.connect(self._on_download_error)
        self.parent.options_menu.options_pages["video"].video_progress_bar.show()
        self.parent.options_menu.options_pages["video"].video_progress_bar.setValue(0)
        self.download_thread.start()

    def _update_progress(self, progress):
        """Actualiza la barra de progreso."""
        print(f"Updating progress bar to {progress}%")  # Mensaje de depuración
        self.parent.options_menu.options_pages["video"].video_progress_bar.setValue(
            progress
        )

    def _on_download_finished(self, downloaded_file):
        """Maneja el final de la descarga."""
        self.parent.options_menu.options_pages["video"].video_progress_bar.setValue(100)
        self.parent.options_menu.options_pages["video"].video_progress_bar.hide()
        print(f"✅ Video descargado: {downloaded_file}")
        self.temp_files.append(downloaded_file)
        self._update_video_source(downloaded_file)

    def _on_download_error(self, error_message):
        """Maneja errores durante la descarga."""
        self.parent.options_menu.options_pages["video"].video_progress_bar.hide()
        print(f"❌ Error cargando video desde URL: {error_message}")
        QMessageBox.critical(
            self.parent,
            "Error",
            f"No se pudo cargar el video desde la URL proporcionada.\n{error_message}",
        )

    def _update_video_source(self, source):
        """Actualizar la fuente del video y liberar recursos previos."""
        if hasattr(self.parent, "cap"):
            if self.parent.cap.isOpened():
                self.parent.cap.release()
            self.parent.timer.stop()
        try:
            self.parent.cap = cv2.VideoCapture(source)  # pylint: disable=no-member
            if self.parent.cap.isOpened():
                self.parent.timer.start(
                    int(1000 / self.parent.cap.get(cv2.CAP_PROP_FPS))
                )  # pylint: disable=no-member
                self.parent.background_label.hide()
            else:
                print("Error: No se pudo abrir el archivo de video.")
                self.parent.background_label.show()
        except (IOError, OSError, ValueError) as e:
            print(f"Error cargando video: {str(e)}")
            self.parent.background_label.show()

    def _load_local_image(self):
        """Cargar imagen local para fondo."""
        if (
            self.parent.options_menu.options_pages["misc"].bg_type_combo.currentText()
            != "Imagen"
        ):
            self._show_image_warning(self.parent.options_menu.image_local_btn)
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self.parent,
            "Seleccionar archivo de imagen",
            os.path.expanduser("~"),
            "Archivos de imagen (*.png *.jpg *.jpeg)",
        )
        if file_path:
            self._update_image_source(file_path)

    def _load_image_from_url(self, url):
        """Cargar imagen desde URL externa."""
        if (
            self.parent.options_menu.options_pages["misc"].bg_type_combo.currentText()
            != "Imagen"
        ):
            self._show_image_warning(self.parent.options_menu.image_url_btn)
            return
        if url:
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    pixmap = QPixmap()
                    pixmap.loadFromData(response.content)
                    self._update_image_source(pixmap)
                else:
                    print(
                        f"Error al cargar imagen desde URL: Código {response.status_code}"
                    )
            except (IOError, OSError, ValueError) as e:
                print(f"Error cargando imagen desde URL: {str(e)}")

    def _update_image_source(self, source):
        """Actualizar la fuente de la imagen."""
        if isinstance(source, str):
            pixmap = QPixmap(source)
        else:
            pixmap = source
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                self.parent.width(),
                self.parent.height(),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            )
            self.parent.background_label.setPixmap(scaled_pixmap)
            self.parent.background_label.show()
            if hasattr(self.parent, "cap"):
                if self.parent.cap.isOpened():
                    self.parent.cap.release()
                self.parent.timer.stop()
        else:
            self.parent.background_label.setStyleSheet("background-color: black;")
            self.parent.background_label.show()
            if hasattr(self.parent, "cap"):
                if self.parent.cap.isOpened():
                    self.parent.cap.release()
                self.parent.timer.stop()

    def _handle_bg_type_change(self, bg_type):
        """Manejar cambio de tipo de fondo."""
        if bg_type == "Video":
            if self.background_choice != "video":
                self.parent.background_label.clear()
                self.parent.background_label.hide()
                self.parent._setup_opencv_video()
                self.background_choice = "video"
        else:
            if self.background_choice != "imagen":
                if hasattr(self.parent, "cap"):
                    if self.parent.cap.isOpened():
                        self.parent.cap.release()
                    self.parent.timer.stop()
                default_image_path = Config.BACKGROUND_PATH
                if os.path.exists(default_image_path):
                    pixmap = QPixmap(default_image_path).scaled(
                        self.parent.width(),
                        self.parent.height(),
                        Qt.IgnoreAspectRatio,
                        Qt.SmoothTransformation,
                    )
                    self.parent.background_label.setPixmap(pixmap)
                    self.parent.background_label.show()
                self.background_choice = "imagen"

    def _toggle_music_with_image(self, state):
        """Alternar reproducción de música con imagen de fondo."""
        if state == Qt.Checked:
            self.parent.audio_player.play()
        else:
            self.parent.audio_player.pause()

    def _show_video_warning(self, widget):
        """Muestra un mensaje de advertencia debajo del widget especificado."""
        warning_label = QLabel(
            "Antes de seleccionar un video, cambia el tipo de fondo."
        )
        warning_label.setStyleSheet(
            """
        color: gray;
        font-size: 12px;
        background-color: transparent;
        """
        )
        warning_label.setAlignment(Qt.AlignCenter)
        parent_layout = widget.parent().layout()
        for i in reversed(range(parent_layout.count())):
            item = parent_layout.itemAt(i)
            if (
                isinstance(item.widget(), QLabel)
                and "Antes de seleccionar" in item.widget().text()
            ):
                parent_layout.removeItem(item)
                item.widget().deleteLater()
        parent_layout.addWidget(warning_label)

    def _show_image_warning(self, widget):
        """Muestra un mensaje de advertencia debajo del widget especificado."""
        warning_label = QLabel(
            "Antes de seleccionar una imagen, cambia el tipo de fondo."
        )
        warning_label.setStyleSheet(
            """
        color: gray;
        font-size: 12px;
        background-color: transparent;
        """
        )
        warning_label.setAlignment(Qt.AlignCenter)
        parent_layout = widget.parent().layout()
        for i in reversed(range(parent_layout.count())):
            item = parent_layout.itemAt(i)
            if (
                isinstance(item.widget(), QLabel)
                and "Antes de seleccionar" in item.widget().text()
            ):
                parent_layout.removeItem(item)
                item.widget().deleteLater()
        parent_layout.addWidget(warning_label)

    def _clear_video_warning(self):
        """Limpia el mensaje de advertencia de la página de configuración de video."""
        if hasattr(self.parent, "video_local_btn") and self.parent.video_local_btn:
            parent_layout = self.parent.video_local_btn.parent().layout()
            for i in reversed(range(parent_layout.count())):
                item = parent_layout.itemAt(i)
                if (
                    isinstance(item.widget(), QLabel)
                    and "Antes de seleccionar" in item.widget().text()
                ):
                    parent_layout.removeItem(item)
                    item.widget().deleteLater()

    def _clear_image_warning(self):
        """Limpia el mensaje de advertencia de la página de configuración de imagen."""
        if hasattr(self.parent, "image_local_btn") and self.parent.image_local_btn:
            parent_layout = self.parent.image_local_btn.parent().layout()
            for i in reversed(range(parent_layout.count())):
                item = parent_layout.itemAt(i)
                if (
                    isinstance(item.widget(), QLabel)
                    and "Antes de seleccionar" in item.widget().text()
                ):
                    parent_layout.removeItem(item)
                    item.widget().deleteLater()

    def _toggle_fullscreen(self):
        """Alterna entre el modo de pantalla completa y el modo ventana."""
        if self.parent.isFullScreen():
            self.parent.showNormal()
        else:
            self.parent.showFullScreen()

    def _clear_warnings(self):
        """Limpia los mensajes de advertencia de las páginas de configuración."""
        self._clear_video_warning()
        self._clear_image_warning()

    def _adjust_volume(self, value):
        """Ajusta el volumen del reproductor de audio."""
        if hasattr(self.parent, "audio_player") and self.parent.audio_player:
            volume = int(value)
            self.parent.audio_player.setVolume(volume)
