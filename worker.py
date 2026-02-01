import sys
import traceback
from typing import Callable, Any, Tuple
from PySide6.QtCore import QRunnable, QObject, Signal, Slot

class WorkerSignals(QObject):
    """
    Define las señales disponibles para un Worker.
    Deben derivar de QObject para poder emitir señales.
    """
    finished = Signal()
    error = Signal(tuple) # (ex_type, value, traceback.format_exc())
    result = Signal(object) # El valor de retorno de la función
    progress = Signal(int)

class Worker(QRunnable):
    """
    Clase Worker genérica para ejecutar funciones en un hilo separado.
    Hereda de QRunnable para ser usada con QThreadPool.
    """
    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()

        # Almacenar la función y argumentos
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        """
        Ejecuta la función pasada al constructor.
        Maneja excepciones y emite señales de resultado/error.
        """
        try:
            result = self.fn(*self.args, **self.kwargs)
        except:
            # Capturar excepción completa
            traceback.print_exc()
            exctype, value = sys.exc_info()[:2]
            self.signals.error.emit((exctype, value, traceback.format_exc()))
        else:
            # Si no hubo error, emitir el resultado
            self.signals.result.emit(result)
        finally:
            # Siempre emitir finalizado
            self.signals.finished.emit()
