import random
from typing import List, Dict, Any
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QRadialGradient, QColor, QPen
from config import Config

class UniverseWidget(QWidget):
    """Widget que dibuja un universo dinámico procedural."""
    
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setGeometry(0, 0, *Config.WINDOW_SIZE)
        
        # Generar estrellas estáticas
        self.stars: List[Dict[str, Any]] = []
        for _ in range(150):
            self.stars.append({
                "x": random.random(),
                "y": random.random(),
                "size": random.uniform(0.5, 2.5),
                "opacity": random.randint(100, 255)
            })
            
        # Estrella fugaz
        self.shooting_star = {"x": -100, "y": -100, "active": False}
        
        # Timers para animación
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update_universe)
        self.anim_timer.start(50) # 20 FPS para fluidez
        
        self.shooting_star_timer = QTimer(self)
        self.shooting_star_timer.timeout.connect(self.trigger_shooting_star)
        self.shooting_star_timer.start(2000) # Una cada 2 segundos aprox

    def trigger_shooting_star(self):
        if not self.shooting_star["active"] and random.random() > 0.3:
            self.shooting_star = {
                "x": random.random() * self.width(),
                "y": random.random() * self.height() / 2,
                "speed_x": random.uniform(10, 20),
                "speed_y": random.uniform(5, 10),
                "length": random.uniform(50, 100),
                "active": True
            }

    def update_universe(self):
        if self.shooting_star["active"]:
            self.shooting_star["x"] += self.shooting_star["speed_x"]
            self.shooting_star["y"] += self.shooting_star["speed_y"]
            if self.shooting_star["x"] > self.width() or self.shooting_star["y"] > self.height():
                self.shooting_star["active"] = False
        
        # Parpadeo de estrellas
        if random.random() > 0.8:
            star = random.choice(self.stars)
            star["opacity"] = random.randint(100, 255)
            
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 1. FONDO: Nebulosa / Espacio Profundo
        grad = QRadialGradient(self.width()/2, self.height()/2, self.width())
        grad.setColorAt(0, QColor(26, 28, 44))
        grad.setColorAt(0.5, QColor(17, 19, 31))
        grad.setColorAt(1, QColor(10, 11, 20))
        painter.fillRect(self.rect(), grad)
        
        # 2. DIBUJAR LUNA
        moon_x, moon_y = self.width() * 0.8, 100
        # Aura de la luna
        moon_grad = QRadialGradient(moon_x, moon_y, 60)
        moon_grad.setColorAt(0, QColor(255, 255, 255, 100))
        moon_grad.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setBrush(moon_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(moon_x, moon_y), 60, 60)
        # Cuerpo de la luna
        painter.setBrush(QColor(240, 240, 240))
        painter.drawEllipse(QPointF(moon_x, moon_y), 30, 30)
        
        # 3. DIBUJAR ESTRELLAS
        for star in self.stars:
            color = QColor(255, 255, 255, star["opacity"])
            painter.setPen(QPen(color, star["size"]))
            painter.drawPoint(QPointF(star["x"] * self.width(), star["y"] * self.height()))
            
        # 4. ESTRELLA FUGAZ
        if self.shooting_star["active"]:
            s = self.shooting_star
            grad_s = QRadialGradient(s["x"], s["y"], s["length"])
            grad_s.setColorAt(0, QColor(255, 255, 255, 200))
            grad_s.setColorAt(1, QColor(255, 255, 255, 0))
            pen = QPen(grad_s, 2)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(s["x"], s["y"]), 
                QPointF(s["x"] - s["length"], s["y"] - s["length"]/2)
            )

class BackgroundManager:
    """Gestor de fondo que utiliza el UniverseWidget procedural."""
    
    def __init__(self, parent_widget: QWidget):
        self.universe = UniverseWidget(parent_widget)
        self.universe.lower() # Enviar al fondo

    def start_background(self, bg_type: str = "Video"):
        # Ignoramos bg_type, el universo procedural siempre está activo
        self.universe.show()

    def cleanup(self):
        self.universe.anim_timer.stop()
        self.universe.shooting_star_timer.stop()