"""
Módulo para manejar la descarga de videos en un hilo separado utilizando yt_dlp.
"""

import os
import tempfile

from typing import Any, Dict, cast
from PySide6.QtCore import QThread, Signal
from yt_dlp import YoutubeDL


class DownloadThread(QThread):
    """
    Hilo para manejar la descarga de videos desde una URL.
    """

    # pylint: disable=too-few-public-methods

    progress = Signal(int)
    finished = Signal(str)
    error = Signal(str)
    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        """
        Inicia la descarga del video desde la URL proporcionada.
        Emite señales de progreso, finalización o error según corresponda.
        """
        try:
            temp_dir = tempfile.gettempdir()
            output_path = os.path.join(temp_dir, "%(title)s.%(ext)s")

            def progress_hook(d: Dict[str, Any]):
                if d.get("status") == "downloading":
                    percent_str: str = d.get("_percent_str", " 0%")
                    progress = percent_str.strip().replace("%", "")
                    try:
                        progress_value = int(float(progress))
                        self.progress.emit(progress_value)
                    except (ValueError, TypeError):
                        pass
                elif d.get("status") == "finished":
                    downloaded_file: str = d.get("filename", "")
                    self.finished.emit(downloaded_file)

            ydl_opts: Dict[str, Any] = {
                "format": "bestvideo[ext=mp4]",
                "outtmpl": output_path,
                "progress_hooks": [progress_hook],
                "concurrent_fragment_downloads": 8,
                "http_chunk_size": 10 * 1024 * 1024,
                "retries": 10,
                "fragment_retries": 10,
            }

            with YoutubeDL(cast(Any, ydl_opts)) as ydl:
                ydl.download([self.url])
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.error.emit(f"Error inesperado: {str(e)}")

    run.__doc__ = "Inicia la descarga del video desde la URL proporcionada."
