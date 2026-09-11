from typing import Any, cast
from PySide6.QtCore import Qt, Signal, QThreadPool
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QComboBox, QCheckBox, QPushButton, QTextEdit, QMessageBox
)
from config import Config, DEFAULT_GEMINI_SYSTEM_INSTRUCTION
from worker import Worker

class GeminiConfigPanel(QWidget):
    """Panel de configuración independiente para Gemini."""
    closed = Signal()

    def __init__(self, parent: QWidget, tools_manager: Any, super_cartoon_font: Any, roboto_black_font: Any, adventure_font: Any):
        super().__init__(parent)
        self.tools_manager = tools_manager
        self.super_cartoon_font = super_cartoon_font
        self.roboto_black_font = roboto_black_font
        self.adventure_font = adventure_font
        
        self.threadpool = QThreadPool()

        self._store_originals()
        self.setup_ui()

    def showEvent(self, event):
        """Se ejecuta cada vez que se muestra el panel."""
        self._store_originals()
        # Sincronizar UI con la configuración actual (por si cambió externamente)
        self.gemini_model_combo.setCurrentText(Config.GEMINI_MODEL)
        self.gemini_thinking_cb.setChecked(Config.GEMINI_ENABLE_THINKING)
        thinking_idx = self.gemini_thinking_level_combo.findData(Config.GEMINI_THINKING_LEVEL)
        self.gemini_thinking_level_combo.setCurrentIndex(thinking_idx if thinking_idx >= 0 else 0)
        self.gemini_thinking_level_combo.setEnabled(Config.GEMINI_ENABLE_THINKING)
        self._update_thinking_level_info()
        self.auto_switch_checkbox.setChecked(Config.ENABLE_AUTO_MODEL_SWITCH)
        
        # Bloquear señales para que no salte el QMessageBox de advertencia al abrir la ventana
        self.ultra_high_quality_cb.blockSignals(True)
        self.ultra_high_quality_cb.setChecked(Config.GEMINI_ULTRA_HIGH_QUALITY)
        self.ultra_high_quality_cb.blockSignals(False)
        
        self.stitching_only_cb.setChecked(Config.GEMINI_STITCHING_ONLY)
        self.gemini_api_input.setText(Config.GEMINI_API_KEY)
        self.pending_system_instruction.setPlainText(Config.GEMINI_SYSTEM_INSTRUCTION)
        super().showEvent(event)

    def _store_originals(self):
        """Guarda los valores originales para poder cancelar."""
        self._original_gemini_model = Config.GEMINI_MODEL
        self._original_gemini_thinking = Config.GEMINI_ENABLE_THINKING
        self._original_gemini_thinking_level = Config.GEMINI_THINKING_LEVEL
        self._original_gemini_temperature = Config.GEMINI_TEMPERATURE
        self._original_auto_model_switch = Config.ENABLE_AUTO_MODEL_SWITCH
        self._original_gemini_api_key = Config.GEMINI_API_KEY
        self._original_gemini_ultra_high_quality = Config.GEMINI_ULTRA_HIGH_QUALITY
        self._original_gemini_stitching_only = Config.GEMINI_STITCHING_ONLY
        self._original_gemini_system_instruction = Config.GEMINI_SYSTEM_INSTRUCTION

    def setup_ui(self):
        self.setObjectName("GeminiConfigPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True) # Fuerza el pintado del fondo
        
        # SIMETRÍA PERFECTA: Margen de 40px en los 4 costados (Contenedor 900x600)
        self.setGeometry(40, 40, 820, 520) 
        
        self.setStyleSheet(
            """
            #GeminiConfigPanel {
                background-color: rgba(0, 0, 0, 245);
                border: 2px solid rgba(150, 0, 150, 180);
                border-radius: 15px;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40) # Márgenes internos uniformes
        layout.setSpacing(10)

        layout.addStretch() # Centrado vertical del contenido

        title_label = QLabel("CONFIGURACIÓN AVANZADA DE GEMINI")
        title_label.setStyleSheet(
            """
            font-size: 24px;
            color: #960096;
            background: transparent;
            qproperty-alignment: AlignCenter;
            letter-spacing: 2px;
            border: none;
            """
        )
        title_label.setFont(self.super_cartoon_font)
        layout.addWidget(title_label)

        # --- Contenedor de Ajustes ---
        settings_container = QWidget()
        settings_container.setStyleSheet("background: transparent; border: none;")
        settings_layout = QVBoxLayout(settings_container)
        settings_layout.setSpacing(15)

        # API Key
        api_box = QVBoxLayout()
        api_label = QLabel("CLAVE DE API (GEMINI API KEY):")
        api_label.setStyleSheet("color: #cccccc; font-size: 12px; border: none;")
        api_label.setFont(self.roboto_black_font)
        
        self.gemini_api_input = QLineEdit()
        self.gemini_api_input.setText(Config.GEMINI_API_KEY)
        self.gemini_api_input.setPlaceholderText("Pega tu API Key aquí...")
        self.gemini_api_input.textChanged.connect(self._validate_gemini_api_ui)
        self.gemini_api_input.setStyleSheet(
            """
            QLineEdit {
                background-color: rgba(20, 20, 20, 200);
                color: white;
                border: 1px solid #572364;
                border-radius: 5px;
                padding: 10px 12px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #960096;
                background-color: rgba(30, 30, 30, 255);
            }
            """
        )
        self.gemini_api_input.setFont(self.roboto_black_font)
        api_box.addWidget(api_label)
        api_box.addWidget(self.gemini_api_input)
        settings_layout.addLayout(api_box)

        # Modelo e Instrucciones en fila
        row1_layout = QHBoxLayout()
        
        model_box = QVBoxLayout()
        model_label = QLabel("MODELO IA:")
        model_label.setStyleSheet("color: #cccccc; font-size: 12px; border: none;")
        model_label.setFont(self.roboto_black_font)
        self.gemini_model_combo = QComboBox()
        self.gemini_model_combo.setStyleSheet(
            """
            QComboBox {
                background-color: rgba(20, 20, 20, 200);
                color: white;
                border: 1px solid #572364;
                border-radius: 5px;
                padding: 10px;
                font-size: 13px;
            }
            QComboBox:hover {
                border: 1px solid #960096;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a1a;
                color: white;
                selection-background-color: #572364;
            }
            """
        )
        self.gemini_model_combo.setFont(self.roboto_black_font)
        
        g_proc = cast(Any, self.tools_manager.gemini_processor)
        models = g_proc.get_available_models()
        
        if models and Config.GEMINI_MODEL not in models:
            Config.GEMINI_MODEL = models[0]
            
        self.gemini_model_combo.addItems(models)
        
        for i in range(self.gemini_model_combo.count()):
            m_name = self.gemini_model_combo.itemText(i).lower()
            if "image" in m_name:
                self.gemini_model_combo.setItemData(i, "MODELO ESPECIALISTA: Uso exclusivo para imágenes/fuentes de dificultad extrema. Solo 1 archivo.", Qt.ItemDataRole.ToolTipRole)
            elif "pro" in m_name:
                self.gemini_model_combo.setItemData(i, "MODELO PRO: Máxima inteligencia y razonamiento profundo.", Qt.ItemDataRole.ToolTipRole)
            elif "flash" in m_name:
                self.gemini_model_combo.setItemData(i, "MODELO FLASH: Equilibrio ideal entre velocidad y precisión.", Qt.ItemDataRole.ToolTipRole)

        self.gemini_model_combo.setCurrentText(Config.GEMINI_MODEL)
        model_box.addWidget(model_label)
        model_box.addWidget(self.gemini_model_combo)
        
        # System Instruction
        self.pending_system_instruction = QTextEdit()
        self.pending_system_instruction.hide()
        self.pending_system_instruction.setPlainText(Config.GEMINI_SYSTEM_INSTRUCTION)

        sys_inst_box = QVBoxLayout()
        sys_inst_label = QLabel("INSTRUCCIONES DE SISTEMA (BASE):")
        sys_inst_label.setStyleSheet("color: #cccccc; font-size: 12px; border: none;")
        sys_inst_label.setFont(self.roboto_black_font)
        
        self.edit_sys_inst_button = QPushButton("EDITAR PERSONALIDAD DE LA IA")
        self.edit_sys_inst_button.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(15, 15, 15, 220);
                border: 1px solid rgba(150, 0, 150, 80);
                color: #e0e0e0;
                padding: 10px;
                font-size: 13px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: rgba(40, 40, 40, 240);
                border: 1px solid #960096;
                color: white;
            }
            """
        )
        self.edit_sys_inst_button.setFont(self.adventure_font)
        self.edit_sys_inst_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_sys_inst_button.clicked.connect(
            lambda: self.tools_manager.open_expanded_editor(self.pending_system_instruction, "Editar Instrucciones de Sistema")
        )
        
        self.reset_sys_inst_button = QPushButton("↺")
        self.reset_sys_inst_button.setToolTip("Restablecer a instrucciones por defecto")
        self.reset_sys_inst_button.setFixedWidth(40)
        self.reset_sys_inst_button.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(60, 20, 20, 220);
                border: 1px solid rgba(150, 0, 0, 80);
                color: #e0e0e0;
                padding: 10px;
                font-size: 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: rgba(100, 30, 30, 240);
                border: 1px solid #ff0000;
                color: white;
            }
            """
        )
        self.reset_sys_inst_button.setFont(self.roboto_black_font)
        self.reset_sys_inst_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_sys_inst_button.clicked.connect(self._reset_system_instruction)

        b_layout = QHBoxLayout()
        b_layout.setSpacing(5)
        b_layout.addWidget(self.edit_sys_inst_button, 1)
        b_layout.addWidget(self.reset_sys_inst_button, 0)

        sys_inst_box.addWidget(sys_inst_label)
        sys_inst_box.addLayout(b_layout)
        
        row1_layout.addLayout(sys_inst_box, 1)
        row1_layout.addSpacing(20)
        row1_layout.addLayout(model_box, 1)
        settings_layout.addLayout(row1_layout)

        # Checkboxes
        options_group = QWidget()
        options_group.setStyleSheet("border: 1px solid rgba(150, 0, 150, 50); border-radius: 8px; padding: 10px;")
        options_layout = QVBoxLayout(options_group)
        options_layout.setSpacing(2)

        # Estilo compartido: padding-bottom evita que los descenders (p, j, q, g, y) se corten
        cb_style_white = "color: white; font-size: 13px; border: none; padding-bottom: 4px;"
        cb_style_red = "color: #ffcccc; font-size: 13px; border: none; font-weight: bold; padding-bottom: 4px;"
        cb_style_green = "color: #ccffcc; font-size: 13px; border: none; font-weight: bold; padding-bottom: 4px;"
        
        self.gemini_thinking_cb = QCheckBox("Activar modo pensamiento (Thinking Mode)")
        self.gemini_thinking_cb.setStyleSheet(cb_style_white)
        self.gemini_thinking_cb.setFont(self.roboto_black_font)
        self.gemini_thinking_cb.setChecked(Config.GEMINI_ENABLE_THINKING)

        self.gemini_thinking_level_combo = QComboBox()
        self.gemini_thinking_level_combo.setStyleSheet(
            """
            QComboBox {
                background-color: rgba(20, 20, 20, 200);
                color: white;
                border: 1px solid #572364;
                border-radius: 5px;
                padding: 8px;
                font-size: 12px;
            }
            QComboBox:hover {
                border: 1px solid #960096;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a1a;
                color: white;
                selection-background-color: #572364;
                width: 180px;
                max-width: 220px;
                min-width: 120px;
            }
            """
        )
        self.gemini_thinking_level_combo.setFont(self.roboto_black_font)
        thinking_level_items = [
            ("auto", "Auto"),
            ("minimal", "Minimal"),
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
        ]
        thinking_level_tooltips = {
            "auto": "NO sobreescribe el nivel nativo de cada modelo:\n"
                    "• gemini-2.5-flash: pensamiento dinámico (hasta ~8.000 tokens/página)\n"
                    "• gemini-3-flash-preview: nivel 'high' (máximo)\n"
                    "• gemini-3.1-flash-lite: nivel 'minimal' (casi 0)\n\n"
                    "Mejor relación calidad/coste. Sin coste inesperado.",
            "minimal": "≈ 0-700 tokens de pensamiento por página.\n"
                       "Máxima velocidad y MÍNIMO consumo/coste.\n"
                       "En modelos 3.x el pensamiento no se apaga del todo: es el estado más cercano a apagado.",
            "low": "≈ 2.000-5.000 tokens de pensamiento por página.\n"
                   "Poca latencia extra. Pensamiento suficiente para texto sencillo.\n"
                   "Úsalo si en 'minimal' hubo errores de calidad por tokens.",
            "medium": "≈ 8.000 tokens de pensamiento por página.\n"
                      "Equilibrio recomendado para traducción:\n"
                      "mejor contexto sin coste excesivo (~+30-50% vs minimal).",
            "high": "PENSAMIENTO EXTENDIDO: ≈ 16.000-25.000 tokens de pensamiento por página.\n"
                    "De 2 a 4 veces más tokens/coste y hasta 2× más lento.\n"
                    "Solo para páginas con texto muy complejo o dañado.\n"
                    "⚠ Con lotes de 3 imágenes puede rozar el límite de salida (65.536 tokens).",
        }
        for i, (value, label) in enumerate(thinking_level_items):
            self.gemini_thinking_level_combo.addItem(label)
            self.gemini_thinking_level_combo.setItemData(i, value, Qt.ItemDataRole.UserRole)
            self.gemini_thinking_level_combo.setItemData(i, thinking_level_tooltips[value], Qt.ItemDataRole.ToolTipRole)
        thinking_idx = self.gemini_thinking_level_combo.findData(Config.GEMINI_THINKING_LEVEL)
        self.gemini_thinking_level_combo.setCurrentIndex(thinking_idx if thinking_idx >= 0 else 0)
        self.gemini_thinking_level_combo.setEnabled(Config.GEMINI_ENABLE_THINKING)
        self.gemini_thinking_level_combo.currentIndexChanged.connect(self._update_thinking_level_info)
        self.gemini_thinking_cb.stateChanged.connect(self._on_thinking_toggled)

        self.ultra_high_quality_cb = QCheckBox("Activar Ultra Alta Calidad (Experimental)")
        self.ultra_high_quality_cb.setToolTip(
            "Maximiza la precisión del OCR usando la resolución más alta de Gemini.\n\n"
            "• Resolución: ULTRA_HIGH (2240 tokens/imagen vs 1120 en modo normal)\n"
            "• Slices/lienzos: 3840px (768×5 tiles, alineado al tiling de Gemini)\n"
            "• Solo disponible en modelos Gemini 3+\n\n"
            "⚠ Duplica el consumo de tokens. Solo procesa 1 imagen por tanda.\n"
            "Recomendado para imágenes con texto muy pequeño o fuentes difíciles."
        )
        self.ultra_high_quality_cb.setStyleSheet(cb_style_red)
        self.ultra_high_quality_cb.setFont(self.roboto_black_font)
        self.ultra_high_quality_cb.setChecked(Config.GEMINI_ULTRA_HIGH_QUALITY)
        self.ultra_high_quality_cb.stateChanged.connect(self._on_ultra_high_toggled)

        self.auto_switch_checkbox = QCheckBox("Cambio automático de modelo en caso de saturación/error")
        self.auto_switch_checkbox.setStyleSheet(cb_style_white)
        self.auto_switch_checkbox.setFont(self.roboto_black_font)
        self.auto_switch_checkbox.setChecked(Config.ENABLE_AUTO_MODEL_SWITCH)

        self.stitching_only_cb = QCheckBox("Modo Unión (Pre-procesa y une imágenes sin traducir)")
        self.stitching_only_cb.setToolTip(
            "Une las imágenes del capítulo en lienzos optimizados para el tiling de Gemini (768px).\n"
            "• Sin Ultra: lienzos de 3072px (768×4 tiles, 0 desperdicio)\n"
            "• Con Ultra: lienzos de 3840px (768×5 tiles, 0 desperdicio)\n\n"
            "Úsalo para preparar las imágenes ANTES de enviarlas a la IA, o para \n"
            "unirlas sin traducir si luego las procesarás manualmente."
        )
        self.stitching_only_cb.setStyleSheet(cb_style_green)
        self.stitching_only_cb.setFont(self.roboto_black_font)
        self.stitching_only_cb.setChecked(Config.GEMINI_STITCHING_ONLY)

        thinking_group = QVBoxLayout()
        thinking_group.setSpacing(2)
        thinking_group.addWidget(self.gemini_thinking_level_combo)
        options_layout.addWidget(self.gemini_thinking_cb)
        options_layout.addLayout(thinking_group)
        options_layout.addWidget(self.ultra_high_quality_cb)
        options_layout.addWidget(self.auto_switch_checkbox)
        options_layout.addWidget(self.stitching_only_cb)
        settings_layout.addWidget(options_group)

        self._on_ultra_high_toggled(Qt.CheckState.Checked if Config.GEMINI_ULTRA_HIGH_QUALITY else Qt.CheckState.Unchecked, initial_load=True)

        layout.addWidget(settings_container)
        layout.addStretch()

        # Botones finales
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)
        btn_style = """
            QPushButton {
                font-size: 15px;
                color: white;
                background-color: #333333;
                border: 1px solid #572364;
                border-radius: 5px;
                padding: 12px 30px;
            }
            QPushButton:hover {
                background-color: #444444;
                border: 1px solid #960096;
            }
        """
        self.cancel_button = QPushButton("CANCELAR")
        self.cancel_button.setStyleSheet(btn_style)
        self.cancel_button.setFont(self.adventure_font)
        self.cancel_button.clicked.connect(self._cancel_settings)

        self.save_button = QPushButton("APLICAR CAMBIOS")
        self.save_button.setStyleSheet(btn_style.replace("#333333", "#572364"))
        self.save_button.setFont(self.adventure_font)
        self.save_button.clicked.connect(self._save_settings_from_ui)

        button_layout.addStretch()
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.save_button)
        layout.addLayout(button_layout)

    def _on_ultra_high_toggled(self, state: Any, initial_load: bool = False):
        is_checked = (state == Qt.CheckState.Checked) if isinstance(state, Qt.CheckState) else bool(state)
        if hasattr(self.tools_manager, 'gemini_browse_folders_button') and self.tools_manager.gemini_browse_folders_button:
            self.tools_manager.gemini_browse_folders_button.setEnabled(not is_checked)
            if is_checked:
                self.tools_manager.gemini_browse_folders_button.setToolTip("Desactivado en modo Ultra High Quality (Solo 1 imagen a la vez).")
            else:
                model_name = self.gemini_model_combo.currentText() if self.gemini_model_combo else Config.GEMINI_MODEL
                if "image" in model_name.lower():
                    self.tools_manager.gemini_browse_folders_button.setToolTip("Los modelos 'image' solo soportan procesamiento de archivos individuales.")
                    self.tools_manager.gemini_browse_folders_button.setEnabled(False)
                else:
                    self.tools_manager.gemini_browse_folders_button.setToolTip("")

        if is_checked and not initial_load:
            QMessageBox.warning(
                self, 
                "Modo Ultra High Quality", 
                "ATENCIÓN: Este modo maximiza la resolución y la precisión del OCR.\n\n"
                "• Lienzos/slices: 3840px (768×5 tiles, alineado al tiling de Gemini)\n"
                "• Resolución: ULTRA_HIGH (2240 tokens por imagen)\n"
                "• Duplica el consumo de tokens vs el modo normal (1120 tokens)\n\n"
                "Solo permite procesar UNA imagen por tanda para evitar errores de memoria."
            )

    def _on_thinking_toggled(self, state: Any):
        """Habilita/oculta el selector de nivel según el checkbox de pensamiento."""
        is_checked = (state == Qt.CheckState.Checked) if isinstance(state, Qt.CheckState) else bool(state)
        if self.gemini_thinking_level_combo:
            self.gemini_thinking_level_combo.setEnabled(is_checked)

    def _update_thinking_level_info(self):
        """Actualiza el tooltip del selector con el resumen de consumo del nivel
        de pensamiento seleccionado (aparece al pasar el mouse, no siempre)."""
        if not self.gemini_thinking_level_combo:
            return
        level = cast(Any, self.gemini_thinking_level_combo.currentData()) or "auto"
        summaries = {
            "auto": "NO sobreescribe el nivel nativo de cada modelo:\n"
                    "• gemini-2.5-flash: pensamiento dinámico (hasta ~8.000 tokens/página)\n"
                    "• gemini-3-flash-preview: nivel 'high' (máximo)\n"
                    "• gemini-3.1-flash-lite: nivel 'minimal' (casi 0)\n\n"
                    "Mejor relación calidad/coste. Sin coste inesperado.",
            "minimal": "Consumo: ~0-700 tokens de pensamiento por página.\n"
                       "Máxima velocidad y MÍNIMO consumo/coste.\n"
                       "En modelos 3.x el pensamiento no se apaga del todo: es el estado más cercano a apagado.",
            "low": "Consumo: ~2.000-5.000 tokens de pensamiento por página.\n"
                   "Poca latencia extra. Pensamiento suficiente para texto sencillo.\n"
                   "Úsalo si en 'minimal' hubo errores de calidad por tokens.",
            "medium": "Consumo: ~8.000 tokens de pensamiento por página (≈+30-50% vs minimal).\n"
                      "Equilibrio recomendado para traducción.",
            "high": "PENSAMIENTO EXTENDIDO: ≈ 16.000-25.000 tokens de pensamiento por página.\n"
                    "De 2 a 4 veces más tokens/coste y hasta 2× más lento.\n"
                    "Solo para páginas con texto muy complejo o dañado.\n"
                    "⚠ Con lotes de 3 imágenes puede rozar el límite de salida (65.536 tokens).",
        }
        self.gemini_thinking_level_combo.setToolTip(summaries.get(level, ""))

    def _validate_gemini_api_ui(self):
        """Valida la API key visualmente de forma asíncrona."""
        if not self.gemini_api_input:
            return
        api_key = self.gemini_api_input.text().strip()
        if len(api_key) < 20: # Demasiado corta para ser válida
            self.gemini_api_input.setStyleSheet("background-color: rgba(20, 20, 20, 255); color: white; border: 2px solid #572364;")
            return

        # Feedback visual inmediato: "Validando..." (Amarillo)
        self.gemini_api_input.setStyleSheet("background-color: rgba(40, 40, 0, 100); color: white; border: 2px solid yellow;")
        
        # Función wrapper para ejecutar en el worker
        def do_validation(key):
            g_proc = cast(Any, self.tools_manager.gemini_processor)
            # validate_key devuelve (bool, msg)
            return g_proc.validate_key(key)

        worker = Worker(do_validation, api_key)
        worker.signals.result.connect(self._handle_validation_result)
        self.threadpool.start(worker)

    def _handle_validation_result(self, result):
        """Recibe el resultado del worker de validación."""
        is_valid, _ = result
        if is_valid:
            self.gemini_api_input.setStyleSheet("background-color: rgba(0, 40, 0, 255); color: white; border: 2px solid green;")
        else:
            self.gemini_api_input.setStyleSheet("background-color: rgba(40, 0, 0, 255); color: white; border: 2px solid red;")

    def _reset_system_instruction(self):
        if QMessageBox.question(self, "Restablecer", "¿Seguro?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.pending_system_instruction.setPlainText(DEFAULT_GEMINI_SYSTEM_INSTRUCTION)

    def _save_settings_from_ui(self):
        settings = {
            "GEMINI_MODEL": self.gemini_model_combo.currentText(),
            "GEMINI_ENABLE_THINKING": self.gemini_thinking_cb.isChecked(),
            "GEMINI_THINKING_LEVEL": cast(str, self.gemini_thinking_level_combo.currentData()),
            "GEMINI_STITCHING_ONLY": self.stitching_only_cb.isChecked(),
            "ENABLE_AUTO_MODEL_SWITCH": self.auto_switch_checkbox.isChecked(),
            "GEMINI_ULTRA_HIGH_QUALITY": self.ultra_high_quality_cb.isChecked(),
            "GEMINI_SYSTEM_INSTRUCTION": self.pending_system_instruction.toPlainText(),
            "GEMINI_TEMPERATURE": 1.0
        }
        api_key = self.gemini_api_input.text().strip()
        # La clave se persiste/borra con Config.set_gemini_api_key (ajustes + bóveda DPAPI)
        Config.save_user_settings({k: v for k, v in settings.items() if k != "GEMINI_API_KEY"})
        Config.set_gemini_api_key(api_key)
        # Update Config in memory
        for k, v in settings.items():
            if k != "GEMINI_API_KEY":
                setattr(Config, k, v)
        
        self.hide()
        self.closed.emit()

    def _cancel_settings(self):
        # Restaurar configuración original en memoria (por seguridad)
        Config.GEMINI_MODEL = self._original_gemini_model
        Config.GEMINI_ENABLE_THINKING = self._original_gemini_thinking
        Config.GEMINI_THINKING_LEVEL = self._original_gemini_thinking_level
        Config.GEMINI_STITCHING_ONLY = self._original_gemini_stitching_only
        Config.ENABLE_AUTO_MODEL_SWITCH = self._original_auto_model_switch
        Config.GEMINI_API_KEY = self._original_gemini_api_key
        Config.GEMINI_ULTRA_HIGH_QUALITY = self._original_gemini_ultra_high_quality
        Config.GEMINI_SYSTEM_INSTRUCTION = self._original_gemini_system_instruction
        
        # Restaurar estado VISUAL de los widgets
        self.gemini_model_combo.setCurrentText(Config.GEMINI_MODEL)
        self.gemini_thinking_cb.setChecked(Config.GEMINI_ENABLE_THINKING)
        thinking_idx = self.gemini_thinking_level_combo.findData(Config.GEMINI_THINKING_LEVEL)
        self.gemini_thinking_level_combo.setCurrentIndex(thinking_idx if thinking_idx >= 0 else 0)
        self.gemini_thinking_level_combo.setEnabled(Config.GEMINI_ENABLE_THINKING)
        self._update_thinking_level_info()
        self.stitching_only_cb.setChecked(Config.GEMINI_STITCHING_ONLY)
        self.auto_switch_checkbox.setChecked(Config.ENABLE_AUTO_MODEL_SWITCH)
        
        # Bloquear señales para no lanzar el popup al cancelar
        self.ultra_high_quality_cb.blockSignals(True)
        self.ultra_high_quality_cb.setChecked(Config.GEMINI_ULTRA_HIGH_QUALITY)
        self.ultra_high_quality_cb.blockSignals(False)
        
        self.gemini_api_input.setText(Config.GEMINI_API_KEY)
        self.pending_system_instruction.setPlainText(Config.GEMINI_SYSTEM_INSTRUCTION)

        # Forzar actualización de estado dependiente (ej: botón carpetas)
        self._on_ultra_high_toggled(Qt.CheckState.Checked if Config.GEMINI_ULTRA_HIGH_QUALITY else Qt.CheckState.Unchecked, initial_load=True)
        
        self.hide()
        self.closed.emit()
