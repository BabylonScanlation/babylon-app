from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QLabel, QWidget

class ClickableThumbnail(QLabel):  # pylint: disable=too-few-public-methods
    """QLabel personalizado que emite una señal al hacer clic."""

    clicked = Signal(str)

    def __init__(self, video_id: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.video_id = video_id
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def mousePressEvent(self, ev: QMouseEvent):  # pylint: disable=invalid-name
        """Maneja el clic y emite la señal."""
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.video_id)
        super().mousePressEvent(ev)
