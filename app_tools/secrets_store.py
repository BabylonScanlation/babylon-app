"""Gestor de claves cifradas (keystore AES-GCM + recordatorio DPAPI en Windows).

Flujo pensado para tu equipo dentro del exe:
  1) El desarrollador genera BBSL/secrets.bin con app_tools/secrets_tool.py,
     cifrando las claves del .env con una passphrase (el archivo viaja en el exe).
  2) En runtime, si no hay .env ni claves en ajustes, la app descifra secrets.bin:
       - usa la passphrase recordada localmente (DPAPI/CryptProtectData) si existe;
       - si no, la pide una vez (QInputDialog) y la RECUERDA para futuras versiones.
  3) Las claves se inyectan en Config y en os.environ (incluye PICACOMIC_*).

Nota de seguridad: DPAPI ata el archivo de recordatorio a la cuenta de Windows del
usuario. La passphrase está protegida en reposo; en runtime la app necesita las claves
en claro para llamar a las APIs, así que viven en memoria mientras la app está abierta.
"""

import base64
import json
import logging
import os
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KEYSTORE_MAGIC = "BABYLON-SECRETS-V1"
KDF_ITERATIONS = 600_000
_ENTROPY = b"babylon-secrets-unlock"


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text)


# ─────────────────────────────────────────────────────────────────────────
#  Rutas
# ─────────────────────────────────────────────────────────────────────────

def _app_root() -> str:
    """Raíz del repo (dev) o directorio del exe (frozen)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_keystore() -> str:
    """Busca secrets.bin: junto al exe > embebido en BBSL (_MEIPASS) > BBSL del repo."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "secrets.bin"))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "BBSL", "secrets.bin"))
    candidates.append(os.path.join(_app_root(), "BBSL", "secrets.bin"))
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


def has_keystore() -> bool:
    return bool(resolve_keystore())


def _cache_path() -> str:
    import config as config_mod
    return os.path.join(config_mod.USER_DATA_DIR, "secrets_unlock.bin")


# ─────────────────────────────────────────────────────────────────────────
#  Recordatorio de passphrase (DPAPI en Windows, permiso 0600 en otros)
# ─────────────────────────────────────────────────────────────────────────

def _dpapi_protect(data: bytes) -> bytes:
    try:
        import win32crypt
        blob = win32crypt.CryptProtectData(data, "Babylon secrets unlock", _ENTROPY, None, None, 0)
        return b"DPAPI" + blob
    except Exception:
        return None


def _dpapi_unprotect(blob: bytes) -> bytes:
    try:
        import win32crypt
        _, data = win32crypt.CryptUnprotectData(blob[5:], _ENTROPY, None, None, 0)
        return data
    except Exception:
        raise ValueError("No se pudo descifrar la passphrase recordada (cuenta de Windows?).")


def save_passphrase(passphrase: str) -> None:
    """Guarda la passphrase protegida con la cuenta de Windows (o texto con 0600)."""
    data = passphrase.encode("utf-8")
    blob = _dpapi_protect(data)
    plain_fallback = blob is None
    if blob is None:
        blob = data
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(blob)
    if plain_fallback:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        logging.warning("win32crypt no disponible: passphrase guardada en texto plano con permisos restringidos.")


def load_passphrase() -> str:
    """Devuelve la passphrase recordada o '' si no existe / no se puede usar."""
    path = _cache_path()
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        blob = f.read()
    try:
        if blob.startswith(b"DPAPI"):
            return _dpapi_unprotect(blob).decode("utf-8")
        return blob.decode("utf-8")  # fallback no-Windows
    except Exception as e:
        logging.warning(f"No se pudo recuperar la passphrase guardada: {e}")
        return ""


def has_cached_passphrase() -> bool:
    return os.path.exists(_cache_path())


def forget_passphrase() -> None:
    """Elimina la passphrase recordada (el keystore sigue existiendo)."""
    path = _cache_path()
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            logging.error(f"No se pudo olvidar la clave: {e}")


# ─────────────────────────────────────────────────────────────────────────
#  Cifrado
# ─────────────────────────────────────────────────────────────────────────

def _derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_secrets(secrets: dict, passphrase: str, iterations: int = KDF_ITERATIONS) -> bytes:
    """Cifra un dict de secretos en un blob JSON (salt/nonce/cipher b64)."""
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt, iterations)
    cipher = AESGCM(key).encrypt(nonce, json.dumps(secrets, ensure_ascii=False).encode("utf-8"), None)
    payload = {
        "magic": KEYSTORE_MAGIC,
        "kdf": "pbkdf2-sha256",
        "iterations": iterations,
        "salt": _b64e(salt),
        "nonce": _b64e(nonce),
        "cipher": _b64e(cipher),
    }
    return json.dumps(payload).encode("utf-8")


def decrypt_secrets(blob: bytes, passphrase: str) -> dict:
    """Descifra un blob generado por encrypt_secrets. Lanza excepción si la passphrase es incorrecta."""
    payload = json.loads(blob.decode("utf-8"))
    if payload.get("magic") != KEYSTORE_MAGIC:
        raise ValueError("Keystore no reconocido (¿se generó con esta versión?).")
    salt = _b64d(payload["salt"])
    nonce = _b64d(payload["nonce"])
    cipher = _b64d(payload["cipher"])
    key = _derive_key(passphrase, salt, int(payload.get("iterations", KDF_ITERATIONS)))
    plain = AESGCM(key).decrypt(nonce, cipher, None)
    return json.loads(plain.decode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────
#  Aplicación de las claves
# ─────────────────────────────────────────────────────────────────────────

SECRET_KEYS = (
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPL_API_KEY",
    "PICACOMIC_EMAIL",
    "PICACOMIC_PASSWORD",
    "PICACOMIC_TOKEN",
)


def apply_secrets(secrets: dict) -> None:
    """Inyecta los secretos en Config y en os.environ para la sesión."""
    from config import Config
    gemini = str(secrets.get("GEMINI_API_KEY", "")).strip()
    if gemini:
        keys = [k.strip() for k in gemini.split(",") if k.strip()]
        Config.GEMINI_API_KEY = keys[0] if keys else ""
        Config.GEMINI_API_KEYS = list(dict.fromkeys(keys))
        os.environ["GEMINI_API_KEY"] = gemini
    for name in SECRET_KEYS:
        value = secrets.get(name)
        if value is None:
            continue
        value = str(value).strip()
        if not value:
            continue
        os.environ[name] = value
        if hasattr(Config, name):
            setattr(Config, name, value)


def unlock_with_passphrase(passphrase: str) -> dict:
    """Descifra el keystore, recuerda la passphrase y aplica las claves. Devuelve los secretos."""
    keystore = resolve_keystore()
    if not keystore:
        raise FileNotFoundError("No se encontró secrets.bin.")
    with open(keystore, "rb") as f:
        secrets = decrypt_secrets(f.read(), passphrase)
    save_passphrase(passphrase)
    apply_secrets(secrets)
    logging.info("✅ Keystore desbloqueado: claves aplicadas.")
    return secrets


def ensure_secrets(app=None, force: bool = False) -> bool:
    """Desbloqueo automático al arranque (si hiciera falta).

    - Si ya hay claves (por .env o ajustes) y no se fuerza, no hace nada.
    - Si hay keystore y passphrase recordada, descifra sin preguntar.
    - Si no, pide la passphrase una vez (requiere app Qt) y la recuerda.
    Devuelve True si las claves quedaron disponibles.
    """
    from config import Config
    if not force and (Config.GEMINI_API_KEY and Config.GEMINI_API_KEYS):
        return True

    if not has_keystore():
        if force:
            raise FileNotFoundError("No se encontró secrets.bin.")
        return bool(Config.GEMINI_API_KEY)

    cached = load_passphrase()
    if cached:
        try:
            unlock_with_passphrase(cached)
            return True
        except Exception as e:
            logging.warning(f"Passphrase recordada no válida, se pedirá de nuevo: {e}")

    if app is None:
        return False

    try:
        from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox
    except Exception:
        return False

    passphrase, ok = QInputDialog.getText(
        app,
        "Claves cifradas",
        "Introduce la passphrase de las claves de Babylon:",
        QLineEdit.EchoMode.Password,
    )
    if not ok or not passphrase:
        return False
    try:
        secrets = decrypt_keystore_with(passphrase)
    except Exception as e:
        QMessageBox.warning(app, "Claves cifradas", f"No se pudo descifrar el keystore:\n{e}")
        return False
    save_passphrase(passphrase)
    apply_secrets(secrets)
    return True


def decrypt_keystore_with(passphrase: str) -> dict:
    """Solo descifra (sin recordar); lo usa la UI del menú de seguridad."""
    keystore = resolve_keystore()
    if not keystore:
        raise FileNotFoundError("No se encontró secrets.bin.")
    with open(keystore, "rb") as f:
        return decrypt_secrets(f.read(), passphrase)