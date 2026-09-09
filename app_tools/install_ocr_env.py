import os
import sys
import urllib.request
import zipfile
import subprocess
import shutil

PYTHON_URL = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

def reporthook(count, block_size, total_size):
    if total_size > 0:
        percent = int(count * block_size * 100 / total_size)
        if percent % 5 == 0:  # Imprimir cada 5% para no saturar
            print(f"[PROGRESS] {min(percent, 100)}", flush=True)

def download_file(url, dest):
    print(f"Descargando {url}...", flush=True)
    urllib.request.urlretrieve(url, dest, reporthook=reporthook)
    print("Descarga completada.", flush=True)

def setup_portable_python(env_dir):
    """Descarga y configura el entorno portátil de Python 3.10."""
    os.makedirs(env_dir, exist_ok=True)
    python_exe = os.path.join(env_dir, "python.exe")
    
    if not os.path.exists(python_exe):
        zip_path = os.path.join(env_dir, "python.zip")
        download_file(PYTHON_URL, zip_path)
        print("Extrayendo entorno Python...", flush=True)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(env_dir)
        os.remove(zip_path)
        
        # Modificar el archivo _pth para habilitar site-packages y pip.
        # CRÍTICO: el Python "embed" NO incluye Lib\\site-packages en sys.path;
        # sin esta línea los paquetes instalados con pip NO se importan.
        pth_file = os.path.join(env_dir, "python310._pth")
        if os.path.exists(pth_file):
            with open(pth_file, "r") as f:
                content = f.read()
            lines = []
            has_site_packages = False
            for ln in content.splitlines():
                strip = ln.strip()
                if strip == "#import site":
                    lines.append("import site")
                elif strip == "Lib\\site-packages":
                    has_site_packages = True
                    lines.append(ln)
                else:
                    lines.append(ln)
            if not has_site_packages:
                lines.append("Lib\\site-packages")
            with open(pth_file, "w") as f:
                f.write("\n".join(lines))
                
    # Instalar pip si no existe
    scripts_dir = os.path.join(env_dir, "Scripts")
    pip_exe = os.path.join(scripts_dir, "pip.exe")
    if not os.path.exists(pip_exe):
        get_pip_path = os.path.join(env_dir, "get-pip.py")
        download_file(GET_PIP_URL, get_pip_path)
        print("Instalando pip...", flush=True)
        subprocess.run([python_exe, get_pip_path], check=True)
        os.remove(get_pip_path)
        
    return python_exe, pip_exe

def install_engine(engine_name, hw_type):
    # Ruta base derivada del propio script (independiente del CWD del proceso).
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base_dir = os.path.join(app_root, "app_tools")
    env_dir = os.path.join(base_dir, "python_ocr")
    
    print(f"Iniciando instalación para {engine_name} en modo {hw_type}...", flush=True)
    python_exe, pip_exe = setup_portable_python(env_dir)
    
    # 1. Instalar el motor OCR específico
    print(f"Instalando paquetes para {engine_name}...", flush=True)
    if engine_name == "paddleocr-v5":
        print("Instalando PaddlePaddle...", flush=True)
        if hw_type == "nvidia":
            subprocess.run([pip_exe, "install", "--progress-bar", "off", "paddlepaddle-gpu==3.3.1", "-i", "https://www.paddlepaddle.org.cn/packages/stable/cu118/"], check=True)
        else:
            subprocess.run([pip_exe, "install", "--progress-bar", "off", "paddlepaddle==3.3.1", "-i", "https://www.paddlepaddle.org.cn/packages/stable/cpu/"], check=True)
        print("Instalando paddleocr y dependencias...", flush=True)
        subprocess.run([pip_exe, "install", "--progress-bar", "off", "paddleocr==3.7.0", "Pillow"], check=True)
    else:
        print(f"Motor no soportado por el instalador: {engine_name}", flush=True)
        sys.exit(1)

    print(f"Instalación de {engine_name} completada con éxito.", flush=True)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python install_ocr_env.py <engine_name> <hw_type>")
        sys.exit(1)
        
    engine = sys.argv[1].lower()
    hw = sys.argv[2].lower()
    
    install_engine(engine, hw)
