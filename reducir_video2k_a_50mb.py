import subprocess
import os

# --- Configuración ---
# ¡IMPORTANTE! Reemplaza "input.mp4" con la ruta completa a tu video 2K original.
# Por ejemplo: INPUT_VIDEO = "C:\Users\TuUsuario\Videos\mi_video_2k.mp4"
INPUT_VIDEO = "input.mp4"

# Nombre del archivo de salida. Se guardará en el mismo directorio donde ejecutes el script.
OUTPUT_VIDEO = "video_50mb.mp4"

# Tamaño máximo deseado para el video en Megabytes (MB).
TARGET_FILE_SIZE_MB = 50

def reducir_video_a_50mb():
    """
    Reduce el tamaño del video de entrada a un máximo de 50MB, priorizando la calidad.
    Utiliza un proceso de codificación de dos pasadas para optimizar la calidad
    para el tamaño de archivo objetivo.
    """
    print(f"Reduciendo '{INPUT_VIDEO}' a un tamaño máximo de {TARGET_FILE_SIZE_MB}MB...")
    print("Esto creará un nuevo archivo de video. Asegúrate de que ffmpeg está instalado y en tu PATH.")

    # --- PASO 1: Obtener la duración del video ---
    # Como no tenemos ffprobe disponible en este entorno, el usuario debe proporcionar la duración.
    video_duration_seconds_str = input(
        "Por favor, introduce la duración del video en segundos (ej. 120.5 para 2 minutos y 30 segundos): "
    )
    try:
        video_duration_seconds = float(video_duration_seconds_str)
        if video_duration_seconds <= 0:
            raise ValueError
    except ValueError:
        print("ERROR: Duración del video inválida. Debe ser un número positivo.")
        return

    # --- PASO 2: Calcular el bitrate objetivo ---
    # Convertir MB a bits y dividir por la duración en segundos.
    # Se resta un pequeño porcentaje para el audio y la sobrecarga del contenedor.
    target_bitrate_bits = (TARGET_FILE_SIZE_MB * 1024 * 1024 * 8 * 0.95) / video_duration_seconds
    target_bitrate_kbps = int(target_bitrate_bits / 1000)

    print(f"Bitrate objetivo calculado: {target_bitrate_kbps} kbps")

    # --- PASO 3: Primera pasada de codificación (análisis) ---
    # Esta pasada analiza el video para optimizar la calidad en la segunda pasada.
    command_pass1 = [
        "ffmpeg",
        "-i", INPUT_VIDEO,
        "-c:v", "libx265",
        "-b:v", f"{target_bitrate_kbps}k",
        "-pass", "1",
        "-f", "mp4",
        "-an", # No procesar audio en la primera pasada
        "NUL" if os.name == 'nt' else "/dev/null" # Salida nula para Windows/Linux
    ]

    print("Iniciando primera pasada de codificación (esto puede tardar)...")
    try:
        subprocess.run(command_pass1, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("Primera pasada completada.")
    except FileNotFoundError:
        print("\nERROR: ffmpeg no encontrado.")
        print("Asegúrate de que ffmpeg está instalado en tu sistema y que su directorio está en la variable de entorno PATH.")
        print("Puedes descargarlo desde: https://ffmpeg.org/download.html")
        return
    except subprocess.CalledProcessError as e:
        print(f"\nERROR en la primera pasada de codificación:")
        print(f"Código de salida: {e.returncode}")
        print(f"Salida estándar: {e.stdout.decode()}")
        print(f"Salida de error: {e.stderr.decode()}")
        print("Asegúrate de que la ruta de INPUT_VIDEO es correcta y que el archivo existe.")
        return

    # --- PASO 4: Segunda pasada de codificación (generación del video final) ---
    command_pass2 = [
        "ffmpeg",
        "-i", INPUT_VIDEO,
        "-c:v", "libx265",
        "-b:v", f"{target_bitrate_kbps}k",
        "-pass", "2",
        "-c:a", "copy", # Copiar audio sin recodificar
        OUTPUT_VIDEO
    ]

    print("Iniciando segunda pasada de codificación (esto puede tardar)...")
    try:
        subprocess.run(command_pass2, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"¡Éxito! Video reducido guardado como '{OUTPUT_VIDEO}'")
        print(f"Ahora puedes reemplazar el 'video.mp4' original en 'app_media\vid-aux\' con este nuevo archivo.")
    except subprocess.CalledProcessError as e:
        print(f"\nERROR en la segunda pasada de codificación:")
        print(f"Código de salida: {e.returncode}")
        print(f"Salida estándar: {e.stdout.decode()}")
        print(f"Salida de error: {e.stderr.decode()}")
        print("Asegúrate de que la ruta de INPUT_VIDEO es correcta y que el archivo existe.")
    except Exception as e:
        print(f"Ocurrió un error inesperado: {e}")

if __name__ == "__main__":
    reducir_video_a_50mb()
