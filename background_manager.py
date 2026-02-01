import os
import cv2
from typing import Optional, List
from PySide6.QtCore import QTimer, QPropertyAnimation, Qt
from PySide6.QtWidgets import QLabel, QGraphicsOpacityEffect, QWidget
from PySide6.QtGui import QPixmap, QImage
from config import Config

class BackgroundManager:
    """Gestor de fondo dinámico (Video OpenCV / Carrusel de Imágenes)."""
    
    def __init__(self, parent_widget: QWidget):
        self.parent = parent_widget
        self.background_label = QLabel(parent_widget)
        self.background_label.setScaledContents(True)
        self.background_label.setGeometry(0, 0, *Config.WINDOW_SIZE)
        
        self.video_label = QLabel(parent_widget)
        self.video_label.setGeometry(0, 0, *Config.WINDOW_SIZE)
        self.video_label.hide() # Por defecto oculto si se usa carrusel

        self.cap: Optional[cv2.VideoCapture] = None
        self.video_timer = QTimer(parent_widget)
        self.video_timer.timeout.connect(self._update_video_frame)

        self.carousel_timer = QTimer(parent_widget)
        self.carousel_timer.setInterval(Config.CAROUSEL_INTERVAL)
        self.carousel_timer.timeout.connect(self._next_carousel_image)
        
        self.carousel_images: List[QPixmap] = []
        self.current_carousel_index = 0
        self.opacity_effect: Optional[QGraphicsOpacityEffect] = None
        self.fade_animation: Optional[QPropertyAnimation] = None

        self._load_carousel_images()

    def _load_carousel_images(self):
        """Pre-carga las imágenes del carrusel."""
        self.carousel_images = [QPixmap(path) for path in Config.CAROUSEL_IMAGES if os.path.exists(path)]
        if self.carousel_images:
            self.background_label.setPixmap(self.carousel_images[0])
            self.opacity_effect = QGraphicsOpacityEffect(self.background_label)
            self.background_label.setGraphicsEffect(self.opacity_effect)
            self.opacity_effect.setOpacity(1.0)

    def start_background(self, bg_type: str = "Video"):
        """Inicia el fondo según el tipo (Video o Imagen)."""
        if bg_type == "Video":
            self.start_video()
        else:
            self.start_carousel()

    def start_video(self):
        """Intenta iniciar el video. Si falla, cae al carrusel."""
        if self.carousel_timer.isActive():
            self.carousel_timer.stop()
        
        if self._initialize_video_capture():
            self.background_label.hide()
            self.video_label.show()
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            if fps > 0:
                self.video_timer.start(int(1000 / fps))
        else:
            print("WARN: Falló la carga del video, usando carrusel.")
            self.start_carousel()

    def start_carousel(self):
        """Inicia el carrusel de imágenes."""
        if self.video_timer.isActive():
            self.video_timer.stop()
        if self.cap:
            self.cap.release()
        
        self.video_label.hide()
        self.background_label.show()
        
        if self.carousel_images and not self.carousel_timer.isActive():
            self.carousel_timer.start()

    def _initialize_video_capture(self) -> bool:
        """Inicializa OpenCV."""
        try:
            self.cap = cv2.VideoCapture(Config.VIDEO_PATH)
            return bool(self.cap and self.cap.isOpened())
        except Exception:
            return False

    def _update_video_frame(self):
        """Lee el siguiente frame del video."""
        if not self.cap or not self.cap.isOpened():
            self.start_carousel()
            return

        ret, frame = self.cap.read()
        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = cv2.resize(frame, Config.WINDOW_SIZE)
            h, w, ch = frame.shape
            q_img = QImage(frame.data, w, h, ch * w, QImage.Format.Format_RGB888)
            self.video_label.setPixmap(QPixmap.fromImage(q_img))
        else:
            # Reiniciar video (loop)
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    def _next_carousel_image(self):
        """Transición suave a la siguiente imagen."""
        if not self.carousel_images or not self.opacity_effect:
            return

        # Fade Out
        self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_animation.setDuration(1000)
        self.fade_animation.setStartValue(1.0)
        self.fade_animation.setEndValue(0.0)
        self.fade_animation.finished.connect(self._swap_image_and_fade_in)
        self.fade_animation.start()

    def _swap_image_and_fade_in(self):
        """Cambia la imagen y hace Fade In."""
        self.current_carousel_index = (self.current_carousel_index + 1) % len(self.carousel_images)
        self.background_label.setPixmap(self.carousel_images[self.current_carousel_index])
        
        # Fade In
        self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_animation.setDuration(1000)
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.start()

    def cleanup(self):
        """Libera recursos."""
        if self.cap:
            self.cap.release()
        self.video_timer.stop()
        self.carousel_timer.stop()
