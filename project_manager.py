from typing import List, Tuple
import os

# Módulos (NO BORRAR)
from config import Config
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QInputDialog, QLabel,
                             QMessageBox)


class ProjectManager:
    """Clase para gestionar proyectos en la aplicación."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.supported_formats = (".jpg", ".png", ".jpeg", ".gif")
        self.project_folders: List[Tuple[str, str]] = []
        self.delete_mode = False
        os.makedirs(self.base_dir, exist_ok=True)

    def toggle_delete_mode(self):
        """Alterna el modo de eliminación."""
        self.delete_mode = not self.delete_mode

    def add_project(self, layout: QGridLayout):
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
            QMessageBox.critical(None, "Error de Archivo", f"No se pudo copiar el archivo al proyecto: {e}")
            return
        while layout.count():
            child = layout.takeAt(0)
            if child:
                w = child.widget()
                if w:
                    w.deleteLater()
        self.load_images_with_descriptions(layout, temp_folder, (141, 212), 5)

    def load_images_with_descriptions(self, layout: QGridLayout, folder: str, size: Tuple[int, int], cols: int):
        """Carga imágenes desde una carpeta y las añade al layout con descripciones."""
        all_files = [
            f
            for f in os.listdir(folder)
            if f.lower().endswith(Config.SUPPORTED_FORMATS)
        ]
        all_files.sort()
        base_style = "border: 1px solid rgba(150, 0, 150, 50); border-radius: 8px; background-color: rgba(30, 30, 30, 150);"
        hover_style = "border: 2px solid #960096; border-radius: 8px; background-color: rgba(150, 0, 150, 30);"

        def enter_handler(label: QLabel, desc: QLabel):
            label.setStyleSheet(hover_style)
            desc.show()

        def leave_handler(label: QLabel, desc: QLabel):
            label.setStyleSheet(base_style)
            desc.hide()

        for i, file_name in enumerate(all_files):
            file_path = os.path.join(folder, file_name)
            try:
                description_text = self.get_description(file_name)
                pixmap = QPixmap(file_path)
                if pixmap.isNull():
                    continue
                
                # Encogemos la imagen un 15% para que no se corte y se vea mejor
                margin = 20
                scaled_pixmap = pixmap.scaled(
                    size[0] - margin,
                    size[1] - margin,
                    Qt.AspectRatioMode.KeepAspectRatio, # Usar KeepAspectRatio evita deformaciones
                    Qt.TransformationMode.SmoothTransformation,
                )
                
                # Usamos un solo QLabel como contenedor base
                image_label = QLabel()
                image_label.setPixmap(scaled_pixmap)
                image_label.setFixedSize(size[0], size[1])
                image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                image_label.setStyleSheet(base_style)
                image_label.setCursor(Qt.CursorShape.PointingHandCursor)
                
                # La descripción es un hijo directo que ocupa TODO el espacio
                description_label = QLabel(description_text, image_label)
                description_label.setFixedSize(size[0], size[1])
                description_label.setWordWrap(True)
                description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                description_label.setStyleSheet(
                    """
                    color: white;
                    background-color: rgba(0, 0, 0, 180);
                    font-size: 11px;
                    padding: 10px;
                    border-radius: 4px;
                    """
                )
                description_label.hide()
                
                # Aseguramos que la descripción no bloquee los eventos del ratón del padre si es necesario,
                # o manejamos los eventos para que ambos reaccionen.
                description_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

                # Eventos de hover (ahora mucho más simples)
                image_label.enterEvent = lambda a0, lbl=image_label, desc=description_label: enter_handler(lbl, desc)
                image_label.leaveEvent = lambda a0, lbl=image_label, desc=description_label: leave_handler(lbl, desc)
                
                row, col = divmod(i, cols)
                layout.addWidget(image_label, row, col)
                
            except Exception:
                pass

    def get_description(self, image_name: str) -> str:
        """Obtiene la descripción de una imagen basada en su nombre."""
        for file_path, project_name in self.project_folders:
            if os.path.basename(file_path) == image_name:
                return project_name
        return Config.TOOL_DESCRIPTIONS.get(image_name, "Descripción no disponible.")
