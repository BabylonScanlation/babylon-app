import subprocess
import os

# --- Configuración ---
# ¡IMPORTANTE! Reemplaza "input.mp4" con la ruta completa a tu video 2K original.
# Por ejemplo: INPUT_VIDEO = "C:\Users\TuUsuario\Videos\mi_video_2k.mp4"
INPUT_VIDEO = "input.mp4" 

# Nombre del archivo de salida. Se guardará en el mismo directorio donde ejecutes el script.
OUTPUT_VIDEO = "video_1200x600.mp4"

# Resolución objetivo para el video. Coincide con el WINDOW_SIZE de la aplicación.
TARGET_RESOLUTION = "1200:600"

def redimensionar_video():
    """
    Redimensiona el video de entrada a la resolución TARGET_RESOLUTION (1200x600)
    manteniendo el códec H.265 y copiando el audio.
    Esto reducirá la carga de la CPU de la aplicación al no tener que redimensionar
    el video en tiempo real desde 2K.
    """
    print(f"Redimensionando '{INPUT_VIDEO}' a {TARGET_RESOLUTION}...")
    print("Esto creará un nuevo archivo de video. Asegúrate de que ffmpeg está instalado y en tu PATH.")

    command = [
        "ffmpeg",
        "-i", INPUT_VIDEO,
        "-vf", f"scale={TARGET_RESOLUTION}",
        "-c:v", "libx265",  # Mantener codec H.265
        "-crf", "23",       # Calidad (menor es mejor, 23 es un buen balance entre calidad y tamaño)
        "-preset", "medium", # Velocidad de codificación (medium es un buen balance)
        "-c:a", "copy",     # Copiar audio sin recodificar
        OUTPUT_VIDEO
    ]

    try:
        # Ejecutar el comando ffmpeg
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"¡Éxito! Video redimensionado guardado como '{OUTPUT_VIDEO}'")
        print(f"Ahora puedes reemplazar el 'video.mp4' original en 'app_media\vid-aux\' con este nuevo archivo.")
    except FileNotFoundError:
        print("\nERROR: ffmpeg no encontrado.")
        print("Asegúrate de que ffmpeg está instalado en tu sistema y que su directorio está en la variable de entorno PATH.")
        print("Puedes descargarlo desde: https://ffmpeg.org/download.html")
    except subprocess.CalledProcessError as e:
        print(f"\nERROR al redimensionar el video:")
        print(f"Código de salida: {e.returncode}")
        print(f"Salida estándar: {e.stdout.decode()}")
        print(f"Salida de error: {e.stderr.decode()}")
        print("Asegúrate de que la ruta de INPUT_VIDEO es correcta y que el archivo existe.")
    except Exception as e:
        print(f"Ocurrió un error inesperado: {e}")

if __name__ == "__main__":
    redimensionar_video()
