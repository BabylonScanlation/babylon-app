import logging
import os
import sys
import zipfile
import threading
from typing import TYPE_CHECKING, Optional

import requests
from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    from bbsl_app import App

# Configurar rutas del proyecto
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

try:
    from config import Config
except ImportError as e:
    logging.error("Error importando config: %s", e)
    sys.exit(1)

# Configuración inicial
logging.basicConfig(level=logging.ERROR)


class DownloadThread(QThread):
    finished = Signal(bool)
    error = Signal(str)

    def __init__(self, manager: 'HaruNekoManager', cancel_event: threading.Event):
        super().__init__()
        self.manager = manager
        self.cancel_event = cancel_event

    def run(self):
        try:
            success = self.manager.download_latest_hakuneko(self.cancel_event)
            self.finished.emit(success)
        except Exception as e:
            self.error.emit(str(e))


class HaruNekoManager:
    def __init__(self, app: 'App'):
        self.app = app

    def download_latest_hakuneko(self, cancel_event: threading.Event) -> bool:
        owner = "manga-download"
        repo = "haruneko"
        url = f"https://api.github.com/repos/{owner}/{repo}/tags"

        response = requests.get(url)
        if response.status_code == 200:
            tags = response.json()
            if tags:
                latest_tag = tags[0]["name"]
                releases_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{latest_tag}"
                releases_response = requests.get(releases_url)

                if releases_response.status_code == 200:
                    release_info = releases_response.json()
                    assets = release_info.get("assets", [])
                    zip_file = None
                    for asset in assets:
                        if (
                            "hakuneko-nw-" in asset["name"]
                            and "win-x64" in asset["name"]
                            and asset["name"].endswith(".zip")
                        ):
                            zip_file = asset
                            break

                    if zip_file:
                        download_url = zip_file["browser_download_url"]
                        zip_response = requests.get(download_url, stream=True)
                        if cancel_event.is_set():
                            return False
                        if zip_response.status_code == 200:
                            os.makedirs(Config.HARUNEKO_DIR, exist_ok=True)
                            download_path = os.path.join(
                                Config.HARUNEKO_DIR, zip_file["name"]
                            )
                            with open(download_path, "wb") as file:
                                for chunk in zip_response.iter_content(chunk_size=8192):
                                    if cancel_event.is_set():
                                        return False
                                    file.write(chunk)
                            self.extract_and_cleanup(
                                download_path, latest_tag, cancel_event
                            )
                            version_file_path = os.path.join(
                                Config.HARUNEKO_DIR, "version.txt"
                            )
                            with open(version_file_path, "w") as version_file:
                                version_file.write(latest_tag)
                            return True
        return False

    def extract_and_cleanup(self, zip_path: str, version: str, cancel_event: threading.Event):
        """Extrae el ZIP respetando su estructura interna."""
        base_extract_dir = os.path.join(Config.HARUNEKO_DIR, f"haruneko-{version}")
        os.makedirs(base_extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            first_file = zip_ref.namelist()[0]
            internal_folder = first_file.split("/")[0] if "/" in first_file else ""
            extract_path = os.path.join(base_extract_dir, internal_folder)
            zip_ref.extractall(extract_path)

        os.remove(zip_path)
        self.create_shortcut(extract_path, version)

    def create_shortcut(self, extract_path: str, version: str):
        """Crea el acceso directo con la ruta validada."""
        exe_path = self.get_exe_path(version)
        if exe_path and os.path.exists(exe_path):
            shortcut_path = os.path.join(
                os.environ["USERPROFILE"], "Desktop", "Haruneko.url"
            )
            with open(shortcut_path, "w") as shortcut:
                shortcut.write(
                    f"[InternetShortcut]\nURL=file:///{exe_path}\nIconIndex=0\nIconFile={exe_path}"
                )
        else:
            logging.error("No se encontró el ejecutable en: %s", extract_path)

    def get_exe_path(self, version: str) -> Optional[str]:
        """Busca el ejecutable con el nombre correcto."""
        extract_path = os.path.join(Config.HARUNEKO_DIR, f"haruneko-{version}")
        exe_name = "hakuneko-nw.exe"
        for root, _, files in os.walk(extract_path):
            if exe_name in files:
                return os.path.join(root, exe_name)
        return None

    def get_current_version(self) -> Optional[str]:
        """Obtiene la versión actual de HaruNeko instalada."""
        version_file_path = os.path.join(Config.HARUNEKO_DIR, "version.txt")
        if os.path.exists(version_file_path):
            with open(version_file_path, "r") as version_file:
                return version_file.read().strip()
        return None

    def check_for_updates(self) -> Optional[str]:
        """Verifica si hay actualizaciones disponibles para HaruNeko."""
        owner = "manga-download"
        repo = "haruneko"
        url = f"https://api.github.com/repos/{owner}/{repo}/tags"
        response = requests.get(url)
        if response.status_code == 200:
            tags = response.json()
            if tags:
                latest_tag = tags[0]["name"]
                current_version = self.get_current_version()
                if current_version and latest_tag != current_version:
                    return latest_tag
        return None
