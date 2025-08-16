"""
Módulo para gestionar proyectos en la aplicación.
"""

import os

# Módulos (NO BORRAR)
from config import Config
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import (QFileDialog, QGridLayout, QInputDialog, QLabel,
                             QMessageBox, QWidget)


class ProjectManager:
    """Clase para gestionar proyectos en la aplicación."""

    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.supported_formats = (".jpg", ".png", ".jpeg", ".gif")
        self.project_folders = []
        self.delete_mode = False
        os.makedirs(self.base_dir, exist_ok=True)

    def toggle_delete_mode(self):
        """Alterna el modo de eliminación."""
        self.delete_mode = not self.delete_mode

    def add_project(self, layout):
        """Permite al usuario añadir un proyecto seleccionando un archivo."""
        file_path, _ = QFileDialog.getOpenFileName(
            None,
            "Seleccionar Archivo",
            self.base_dir,
            "Todos los archivos (*.*);;Archivos de imagen (*.jpg *.png *.jpeg *.gif)",
        )
        if not file_path:
            return
        if file_path in [path for path, _ in self.project_folders]:
            QMessageBox.warning(
                None, "Advertencia", "Este archivo ya ha sido agregado."
            )
            return
        dialog = QInputDialog()
        dialog.setWindowTitle("Proyecto:")
        dialog.setLabelText("Ingrese el nombre del proyecto:")
        dialog.setTextValue("")
        dialog.setWindowIcon(QIcon(Config.ICON_PATH))
        project_name, ok = dialog.getText(
            None, "Proyecto", "Ingrese el nombre del proyecto:"
        )
        if not ok or not project_name.strip():
            QMessageBox.warning(
                None, "Advertencia", "El nombre del proyecto no puede estar vacío."
            )
            return
        self.project_folders.append((file_path, project_name))
        temp_folder = os.path.join(self.base_dir, "proyectos")
        os.makedirs(temp_folder, exist_ok=True)
        file_name = os.path.basename(file_path)
        destination_path = os.path.join(temp_folder, file_name)
        try:
            with open(file_path, "rb") as src, open(destination_path, "wb") as dst:
                dst.write(src.read())
        except IOError as e:
            print(f"Error copiando archivo '{file_name}': {str(e)}")
            return
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._load_images_with_descriptions(layout, temp_folder, (141, 212), 5)

    def _load_images_with_descriptions(self, layout, folder, size, cols):
        """Carga archivos desde una carpeta y los organiza en el layout con descripciones flotantes."""
        if not os.path.exists(folder):
            print(f"Advertencia: Carpeta no encontrada: {folder}")
            return
        all_files = [
            f
            for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f))
            and f.lower().endswith(self.supported_formats)
        ]
        row, col = 0, 0
        padding = 4
        border = 2
        adjusted_size = (size[0] - padding - border, size[1] - padding - border)

        base_style = """
        background-color: rgba(0, 0, 0, 100);
        border-radius: 4px;
        border: 1px solid rgba(87, 35, 100, 180);
        """

        hover_style = """
        background-color: rgba(0, 0, 0, 100);
        border-radius: 4px;
        border: 1px solid rgba(87, 35, 100, 180);
        """

        def enter_event(label, desc):
            try:
                label.setStyleSheet(hover_style)
                desc.show()
            except AttributeError as e:
                print(f"Error en enter_event: {str(e)}")

        def leave_event(label, desc):
            try:
                label.setStyleSheet(base_style)
                desc.hide()
            except AttributeError as e:
                print(f"Error en leave_event: {str(e)}")

        for file_name in all_files:
            file_path = os.path.join(folder, file_name)
            try:
                description_text = self.get_description(file_name)
                pixmap = QPixmap(file_path)
                if pixmap.isNull():
                    raise ValueError(f"Archivo no válido: {file_path}")
                scaled_pixmap = pixmap.scaled(
                    adjusted_size[0],
                    adjusted_size[1],
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation,
                )
                container = QWidget()
                container_layout = QGridLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(0)
                image_label = QLabel()
                image_label.setPixmap(scaled_pixmap)
                image_label.setAlignment(Qt.AlignCenter)
                image_label.setStyleSheet(base_style)
                image_label.setCursor(Qt.PointingHandCursor)
                description_label = QLabel(description_text)
                description_label.setStyleSheet(
                    """
                color: white;
                background-color: rgba(0, 0, 0, 150);
                font-size: 12px;
                qproperty-alignment: AlignCenter;
                border-radius: 4px;
                border: 1px solid rgba(230, 0, 230, 180);
                """
                )
                description_label.setWordWrap(True)
                description_label.setFixedWidth(size[0])
                description_label.hide()
                description_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

                image_label.enterEvent = (
                    lambda event, lbl=image_label, desc=description_label: enter_event(
                        lbl, desc
                    )
                )
                image_label.leaveEvent = (
                    lambda event, lbl=image_label, desc=description_label: leave_event(
                        lbl, desc
                    )
                )
                container_layout.addWidget(image_label, 0, 0)
                container_layout.addWidget(description_label, 0, 0)
                container.setFixedSize(size[0], size[1])
                layout.addWidget(container, row, col)
                col += 1
                if col >= cols:
                    col = 0
                    row += 1
            except ValueError as e:
                print(f"Error procesando archivo '{file_name}': {str(e)}")
                continue
        layout.setRowStretch(row + 1, 1)
        layout.setColumnStretch(cols, 1)

    def get_description(self, image_name):
        """Obtiene la descripción de una imagen basada en su nombre."""
        descriptions = {
            "01_ocr.png": "Varios OCR's (Reconocimiento óptico de carácteres) para extraer el texto de imágenes.",
            "02_traductor.png": "Varios traductores de texto.",
            "03_ai.png": "Varias Inteligencias Artificiales del tipo Multimodal.",
            "04_descargar_capitulos.png": "Varias maneras para descargar el RAW de una serie.",
            "05_mejorar_imagen.png": "Varias formas para mejorar la calidad de las imágenes [PROXIMAMENTE].",
            "06_nsfw.png": "Varios programas para detectar NSFW en imágenes para evitar baneos en páginas externas [PROXIMAMENTE].",
            "07_fuentes.png": "Varias fuentes y programas para organizar las tipografías [PROXIMAMENTE].",
            "08_editor_texto.png": "Aplicaciones para editar texto [PROXIMAMENTE].",
        }
        for file_path, project_name in self.project_folders:
            if os.path.basename(file_path) == image_name:
                return project_name
        return descriptions.get(image_name, "Descripción no disponible.")
