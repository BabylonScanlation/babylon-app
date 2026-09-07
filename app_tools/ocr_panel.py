import os
from typing import List
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QTextEdit, QFileDialog, QLineEdit, QComboBox, QSplitter
)
from app_tools.ocr_manager import OCRManager

class OCRWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, engine_name: str, images: List[str], langs: List[str]):
        super().__init__()
        self.engine_name = engine_name
        self.images = images
        self.langs = langs

    def run(self):
        try:
            results = OCRManager.run_engine(self.engine_name, self.images, self.langs)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class OCRPanel(QWidget):
    def __init__(self, engine_name: str, app):
        super().__init__(app.content_container)
        self.app = app
        self.engine_name = engine_name
        self.image_paths = []
        self.output_dir = ""
        self.init_ui()

    def init_ui(self):
        self.setObjectName("OCRPanelContainer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            #OCRPanelContainer {
                background-color: rgba(20, 22, 28, 220);
                border: 1px solid rgba(157, 70, 255, 0.3);
                border-radius: 15px;
            }
            QWidget { color: white; }
            QLineEdit, QTextEdit { background-color: rgba(30, 30, 30, 200); border: 1px solid #960096; border-radius: 6px; padding: 6px; }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Header with Title and Close Button
        header_layout = QHBoxLayout()
        title_lbl = QLabel(f"OCR - {self.engine_name.upper()}")
        title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; border: none; background: transparent;")
        header_layout.addWidget(title_lbl)
        
        header_layout.addStretch()
        
        close_btn = self.app._create_button("Cerrar", self.close_panel)
        close_btn.setFixedSize(80, 30)
        close_btn.setStyleSheet(close_btn.styleSheet() + " border: 1px solid red;")
        header_layout.addWidget(close_btn)
        
        main_layout.addLayout(header_layout)

        # Top Layout: File selection
        top_layout = QHBoxLayout()
        
        # Image selection
        self.img_path_input = QLineEdit()
        self.img_path_input.setPlaceholderText("Selecciona imagen/imágenes...")
        self.img_path_input.setReadOnly(True)
        img_btn = self.app._create_button("Seleccionar Imágenes", self.select_images)
        top_layout.addWidget(self.img_path_input)
        top_layout.addWidget(img_btn)

        # Output dir selection
        self.out_path_input = QLineEdit()
        self.out_path_input.setPlaceholderText("Directorio de salida (opcional)...")
        self.out_path_input.setReadOnly(True)
        out_btn = self.app._create_button("Ubicación de Salida", self.select_output_dir)
        top_layout.addWidget(self.out_path_input)
        top_layout.addWidget(out_btn)

        main_layout.addLayout(top_layout)

        # Middle Layout: Options
        mid_layout = QHBoxLayout()
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["Inglés", "Japonés", "Coreano", "Chino"])
        mid_layout.addWidget(QLabel("Idioma principal:"))
        mid_layout.addWidget(self.lang_combo)
        
        self.run_btn = self.app._create_button("Extraer Texto", self.run_ocr)
        self.run_btn.setMinimumHeight(40)
        self.run_btn.setStyleSheet(self.run_btn.styleSheet() + " background-color: rgba(157, 70, 255, 0.3); border: 1px solid #9d46ff;")
        mid_layout.addWidget(self.run_btn)
        main_layout.addLayout(mid_layout)

        # Bottom Layout: Splitter (Image left, Text right)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        self.image_preview = QLabel("Vista previa de la imagen")
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setStyleSheet("border: 1px dashed #960096;")
        
        self.text_result = QTextEdit()
        self.text_result.setPlaceholderText("El texto extraído aparecerá aquí...")

        splitter.addWidget(self.image_preview)
        splitter.addWidget(self.text_result)
        splitter.setSizes([400, 400])

        main_layout.addWidget(splitter)

    def select_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Seleccionar Imágenes", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if files:
            self.image_paths = files
            self.img_path_input.setText(f"{len(files)} imágenes seleccionadas")
            self.show_preview(files[0])

    def show_preview(self, path):
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            # Escalar manteniendo la relación de aspecto
            scaled = pixmap.scaled(self.image_preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.image_preview.setPixmap(scaled)

    def select_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "Seleccionar Directorio de Salida")
        if directory:
            self.output_dir = directory
            self.out_path_input.setText(directory)

    def run_ocr(self):
        if not self.image_paths:
            self.text_result.setText("Por favor selecciona al menos una imagen.")
            return

        self.run_btn.setEnabled(False)
        self.run_btn.setText("Procesando...")
        self.text_result.setText("Iniciando extracción de texto...")

        # Mapeo simple de idiomas
        lang_map = {"Inglés": "en", "Japonés": "ja", "Coreano": "ko", "Chino": "ch_sim"}
        langs = [lang_map[self.lang_combo.currentText()]]

        self.worker = OCRWorker(self.engine_name, self.image_paths, langs)
        self.worker.finished.connect(self.on_ocr_finished)
        self.worker.error.connect(self.on_ocr_error)
        self.worker.start()

    def on_ocr_finished(self, results: dict):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("Extraer Texto")
        
        combined_text = ""
        for path, text in results.items():
            filename = os.path.basename(path)
            combined_text += f"--- {filename} ---\n{text}\n\n"
            
            # Guardar a disco si hay ruta de salida
            if self.output_dir:
                out_file = os.path.join(self.output_dir, f"{os.path.splitext(filename)[0]}.txt")
                try:
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(text)
                except Exception as e:
                    combined_text += f"(Error guardando archivo: {e})\n"

        self.text_result.setText(combined_text.strip())

    def on_ocr_error(self, err_msg: str):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("Extraer Texto")
        self.text_result.setText(f"Error durante el OCR: {err_msg}")

    def close_panel(self):
        """Cierra el panel y notifica a la aplicación principal para que restaure la vista de herramientas."""
        self.hide()
        # Buscar la ventana principal (App) y llamar a show_utilities
        main_window = self.window()
        if hasattr(main_window, "show_utilities"):
            main_window.show_utilities()
