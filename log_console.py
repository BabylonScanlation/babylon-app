import logging
import os
import threading
import codecs
import sys
import warnings
from PySide6.QtWidgets import QVBoxLayout, QTextEdit, QFrame, QMessageBox
from PySide6.QtCore import Signal, QObject

# --- Sistema Global de Logging ---
class LogSignal(QObject):
    """Emisor de señales para logs (necesario para thread-safety en Qt)."""
    log_received = Signal(str)

# --- PARCHEO GLOBAL DE QMESSAGEBOX PARA LOGGING ---
_original_critical = QMessageBox.critical
_original_warning = QMessageBox.warning
_original_information = QMessageBox.information
_original_question = QMessageBox.question

def _patched_critical(parent, title, text, *args, **kwargs):
    logging.error(f"[POPUP CRITICAL] {title}: {text}")
    return _original_critical(parent, title, text, *args, **kwargs)

def _patched_warning(parent, title, text, *args, **kwargs):
    logging.warning(f"[POPUP WARNING] {title}: {text}")
    return _original_warning(parent, title, text, *args, **kwargs)

def _patched_information(parent, title, text, *args, **kwargs):
    logging.info(f"[POPUP INFO] {title}: {text}")
    return _original_information(parent, title, text, *args, **kwargs)

def _patched_question(parent, title, text, *args, **kwargs):
    logging.info(f"[POPUP QUESTION] {title}: {text}")
    return _original_question(parent, title, text, *args, **kwargs)

# Aplicar parches
QMessageBox.critical = _patched_critical
QMessageBox.warning = _patched_warning
QMessageBox.information = _patched_information
QMessageBox.question = _patched_question

# Variables globales
LOG_BUFFER = []
LOG_SIGNAL = None # Se inicializará después de crear QApplication
UI_HANDLER = None # Referencia global al handler

class QtLogHandler(logging.Handler):
    """Handler de logging que redirige los mensajes a una señal Qt y guarda un buffer."""
    def __init__(self):
        super().__init__()
        self.signal_emitter = None

    def set_emitter(self, emitter):
        self.signal_emitter = emitter

    def emit(self, record):
        try:
            msg = self.format(record)
            
            # 1. Guardar siempre en buffer (historial)
            LOG_BUFFER.append(msg)
            
            # 2. Emitir señal solo si el sistema de señales ya está activo
            if self.signal_emitter:
                self.signal_emitter.log_received.emit(msg)
        except Exception:
            self.handleError(record)

class FDCapturer:
    """
    Captura salida a nivel de descriptor de archivo (OS level).
    Esto atrapa prints de C, C++, FFmpeg, Qt, etc.
    """
    def __init__(self, fd, logger, level):
        self.fd = fd
        self.logger = logger
        self.level = level
        self.original_fd = os.dup(fd)
        
        self.pipe_read, self.pipe_write = os.pipe()
        os.dup2(self.pipe_write, fd)
        
        self.thread = threading.Thread(target=self._read_pipe, daemon=True)
        self.thread.start()

    def _read_pipe(self):
        """Lee del pipe usando os.read de bajo nivel para evitar buffering de Python."""
        decoder = codecs.getincrementaldecoder("utf-8")(errors='replace')
        buf = ""
        
        while True:
            try:
                # Leer bytes crudos directamente del descriptor de archivo
                chunk = os.read(self.pipe_read, 1024)
                if not chunk:
                    break # EOF
                
                # Decodificar y acumular
                text_chunk = decoder.decode(chunk, final=False)
                buf += text_chunk
                
                # Procesar líneas completas
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    line = line.rstrip()
                    if not line: 
                        continue
                        
                    # --- Lógica de nivel (Agresiva) ---
                    level_to_use = self.level
                    lower_text = line.lower()
                    
                    # Si es el descriptor de error (2), mínimo es WARNING
                    if self.fd == 2:
                        level_to_use = logging.WARNING
                        error_keywords = ["error", "failed", "exception", "traceback", "fatal", "panic", "critical"]
                        if any(k in lower_text for k in error_keywords):
                            level_to_use = logging.ERROR
                    
                    self.logger.log(level_to_use, line)
                    
            except OSError:
                break
            except Exception as e:
                logging.error(f"FDCapturer pipe read error: {e}")
                continue

def init_global_logging():
    """Configura el logging globalmente (Fase 1: Solo Buffer)."""
    global UI_HANDLER
    
    # Redirigir advertencias de Python al sistema de logging
    logging.captureWarnings(True)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO) # Nivel base más razonable

    # Silenciar librerías ruidosas
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.INFO)
    
    if root_logger.handlers:
        root_logger.handlers = []

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')

    # 1. Handler para la UI
    UI_HANDLER = QtLogHandler()
    UI_HANDLER.setFormatter(formatter)
    root_logger.addHandler(UI_HANDLER)
    
    # 2. INTERCEPTAR NIVEL BAJO (C++/FFmpeg/Qt/Network)
    try:
        native_out = logging.getLogger('NATIVE_OUT')
        native_out.propagate = False
        native_out.setLevel(logging.INFO) # Cambiado de DEBUG a INFO
        native_out.addHandler(UI_HANDLER)

        native_err = logging.getLogger('NATIVE_ERR')
        native_err.propagate = False
        native_err.setLevel(logging.WARNING) # Nivel de error ya era razonable
        native_err.addHandler(UI_HANDLER)

        # Captura total de descriptores 1 (stdout) y 2 (stderr)
        _ = FDCapturer(1, native_out, logging.INFO) # Cambiado de DEBUG a INFO
        _ = FDCapturer(2, native_err, logging.WARNING)
    except Exception as e:
        print(f"Error inicializando FDCapturer: {e}")


def init_signals():
    """Inicializa las señales Qt (Fase 2: Conectar UI). Llamar DESPUÉS de crear QApplication."""
    global LOG_SIGNAL
    if UI_HANDLER:
        LOG_SIGNAL = LogSignal()
        UI_HANDLER.set_emitter(LOG_SIGNAL)

class LogConsole(QFrame):
    """Widget de consola estilo terminal."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        self.connect_logging()

    def setup_ui(self):
        # Estilo del contenedor "Cyber-Glass"
        self.setObjectName("LogConsoleFrame")
        self.setStyleSheet(
            """
            #LogConsoleFrame {
                background-color: rgba(10, 10, 10, 200);
                border: 1px solid rgba(150, 0, 150, 80);
                border-radius: 15px;
            }
            """
        )
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20) # Márgenes un poco más amplios

        self.text_edit = QTextEdit()
        self.text_edit.setFrameShape(QFrame.Shape.NoFrame)
        self.text_edit.setReadOnly(True)
        # Estilo interno del área de texto y Scrollbar personalizada
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                color: #e0e0e0;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 13px;
                border: none;
                selection-background-color: rgba(150, 0, 150, 100);
                selection-color: white;
            }
            QScrollBar:vertical {
                background: rgba(0, 0, 0, 50);
                width: 8px;
                margin: 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(150, 0, 150, 100);
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(200, 50, 200, 150);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        
        layout.addWidget(self.text_edit)

    def connect_logging(self):
        """Conecta la UI al sistema de logging global."""
        # 1. Cargar historial previo (buffer)
        if LOG_BUFFER:
            self.text_edit.setText("\n".join(LOG_BUFFER))
            self.scroll_to_bottom()
        
        # 2. Conectar señal para nuevos logs
        if LOG_SIGNAL:
            LOG_SIGNAL.log_received.connect(self.append_log)

    def append_log(self, text):
        """Añade texto a la consola y hace auto-scroll."""
        self.text_edit.append(text)
        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        scrollbar = self.text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @staticmethod
    def notify(message: str):
        """Método estático para registrar una notificación importante."""
        logging.info(f"🔔 NOTIFICACIÓN: {message}")

