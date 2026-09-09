import os
import sys
import importlib.util
from typing import Dict, Any, List, Optional, Tuple

# Ruta base de la app derivada del propio módulo (independiente del CWD del proceso).
APP_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class OCRManager:
    """Gestor centralizado para instalaciones y ejecución de herramientas OCR (Modo Portátil)."""
    
    @staticmethod
    def get_env_dir() -> str:
        return os.path.join(APP_BASE_DIR, "app_tools", "python_ocr")

    @staticmethod
    def get_portable_python() -> str:
        return os.path.join(OCRManager.get_env_dir(), "python.exe")

    @staticmethod
    def check_engine_installed(engine_name: str) -> bool:
        """Verifica si las dependencias de un motor OCR están instaladas en el entorno portátil."""
        engine_name = engine_name.lower()
        python_exe = OCRManager.get_portable_python()
        
        if not os.path.exists(python_exe):
            return False
            
        # Comprobar la existencia del paquete usando el python portátil
        import subprocess
        try:
            if engine_name == "paddleocr-v5":
                subprocess.run([python_exe, "-c", "import paddleocr"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
        except subprocess.CalledProcessError:
            return False
            
        return False

    @staticmethod
    def get_install_command(engine_name: str, hw_type: str = "cpu") -> Optional[str]:
        """Devuelve el comando shell necesario para invocar al script instalador portátil."""
        script_path = os.path.join(APP_BASE_DIR, "app_tools", "install_ocr_env.py")
        if not os.path.exists(script_path):
            return None
            
        # Ejecutamos con el sys.executable base de la app para levantar el entorno aislado
        return f'"{sys.executable}" "{script_path}" {engine_name} {hw_type}'

    @staticmethod
    def get_exact_download_size(engine_name: str) -> str:
        """Devuelve el tamaño de la descarga en base a los metadatos de los wheels y PyPI."""
        engine_name = engine_name.lower()
        python_bytes = 8629277 # python-3.10.11-embed-amd64.zip
        
        if engine_name == "paddleocr-v5":
            # PaddleOCR-v5: Python embed + PaddlePaddle + paddleocr/paddlex + deps (opencv, shapely, scipy...)
            total_bytes = python_bytes + 220000000 + 330000000
            return f"{total_bytes / (1024*1024):.2f} MB"
        return "Tamaño desconocido"

    @staticmethod
    def get_download_size(engine_name: str) -> str:
        """Alias usado por la UI de herramientas (tools.py)."""
        return OCRManager.get_exact_download_size(engine_name)

    @staticmethod
    def run_engine(engine_name: str, image_paths: List[str], langs: List[str] = ['es', 'en']) -> Dict[str, str]:
        """Ejecuta un motor OCR a través de un proceso aislado en el entorno portátil."""
        python_exe = OCRManager.get_portable_python()
        if not os.path.exists(python_exe):
            raise ImportError(f"Entorno portátil de Python no encontrado. Instala {engine_name} primero.")
            
        script_path = os.path.join(APP_BASE_DIR, "app_tools", "run_ocr_script.py")
        
        # Guardar las rutas en un archivo temporal para pasarlas al script
        import tempfile
        import json
        import subprocess
        
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json', encoding='utf-8') as f:
            json.dump({"engine": engine_name, "images": image_paths, "langs": langs}, f, ensure_ascii=False)
            temp_path = f.name
            
        try:
            proc = subprocess.run(
                [python_exe, script_path, temp_path], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True, 
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            stdout = (proc.stdout or "").strip()
            try:
                return json.loads(stdout)
            except (json.JSONDecodeError, ValueError):
                err_tail = (proc.stderr or "").strip()[-1000:]
                return {path: f"Error: salida inesperada del motor OCR. {err_tail}" for path in image_paths}
        except subprocess.CalledProcessError as e:
            return {path: f"Error: {e.stderr}" for path in image_paths}
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

