import sys
import os
import time
import subprocess
import glob
from pathlib import Path
import threading

# Forzar salida UTF-8 en la consola de Windows para este script
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Configuración
MAIN_SCRIPT = "bbsl_app.py"
WATCH_EXTENSIONS = {'.py', '.qss', '.json'}
POLL_INTERVAL = 1.0

def get_file_mtimes():
    """Obtiene un diccionario con los tiempos de modificación de todos los archivos vigilados."""
    mtimes = {}
    for filepath in glob.glob("**/*", recursive=True):
        p = Path(filepath)
        if p.is_file() and p.suffix in WATCH_EXTENSIONS:
            try:
                mtimes[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    return mtimes

def stream_reader(pipe, prefix):
    """Lee un pipe línea por línea y lo imprime en tiempo real."""
    try:
        with pipe:
            for line in iter(pipe.readline, b''):
                try:
                    # Decodificar y limpiar
                    text = line.decode('utf-8', errors='replace').rstrip()
                    print(f"{prefix} {text}")
                except Exception:
                    # Fallback por si el print falla (ej. consola muy restrictiva)
                    pass
    except Exception:
        pass

def main():
    print(f"--- 🛡️ MODO DESARROLLO ROBUSTO: Vigilando {os.getcwd()} ---")
    
    process = None
    last_mtimes = get_file_mtimes()

    # Preparar entorno para el subproceso (Forzar UTF-8)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        while True:
            # 1. Iniciar la aplicación si no está corriendo
            if process is None:
                print(f"\n🚀 Iniciando {MAIN_SCRIPT}...")
                
                # Ejecutamos con pipes para capturar la salida
                process = subprocess.Popen(
                    [sys.executable, MAIN_SCRIPT],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,  # Pasamos el entorno con UTF-8 forzado
                    # bufsize=1, # Line buffering no soportado en modo binario
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )

                # Hilos para leer stdout y stderr sin bloquear
                t_out = threading.Thread(target=stream_reader, args=(process.stdout, "[APP]"))
                t_err = threading.Thread(target=stream_reader, args=(process.stderr, "[ERR]"))
                t_out.daemon = True
                t_err.daemon = True
                t_out.start()
                t_err.start()

            # 2. Verificar estado del proceso
            ret_code = process.poll()
            
            # Si el proceso murió...
            if ret_code is not None:
                if ret_code != 0:
                    print(f"\n❌ La aplicación se cerró con error (Código: {ret_code})")
                    print("👀 Esperando cambios en el código para reiniciar...")
                    # Esperar bloqueo hasta que haya cambios
                    while True:
                        time.sleep(POLL_INTERVAL)
                        current_mtimes = get_file_mtimes()
                        if current_mtimes != last_mtimes:
                            last_mtimes = current_mtimes
                            process = None # Reset para reiniciar
                            break
                else:
                    print(f"\n✅ La aplicación se cerró correctamente.")
                    # Si se cerró bien, ¿quizás el usuario la cerró? Esperamos cambios o reiniciamos?
                    # Asumimos que si la cerró el usuario, quiere esperar.
                    while True:
                        time.sleep(POLL_INTERVAL)
                        current_mtimes = get_file_mtimes()
                        if current_mtimes != last_mtimes:
                            last_mtimes = current_mtimes
                            process = None
                            break
                
                # Si salimos del bucle de espera, es porque hubo cambios -> reiniciamos bucle principal
                continue

            # 3. Bucle de vigilancia (si la app sigue viva)
            time.sleep(POLL_INTERVAL)
            current_mtimes = get_file_mtimes()

            # 4. Si hay cambios mientras corre...
            if current_mtimes != last_mtimes:
                print("\n📝 Cambio detectado! Reiniciando...")
                
                if process:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        print("💀 Forzando cierre...")
                        process.kill()
                
                process = None
                last_mtimes = current_mtimes

    except KeyboardInterrupt:
        print("\n🛑 Deteniendo modo desarrollo...")
        if process:
            process.kill()

if __name__ == "__main__":
    main()