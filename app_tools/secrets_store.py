"""Gestor de claves cifradas (bóveda DPAPI).

Modelo actual (sin passphrase):
  - Las claves del usuario se guardan en secrets_vault.bin, cifradas con la cuenta
    de Windows (DPAPI) — estilo Windows Hello/WinCred. Se desbloquean solas en cada
    apertura: NO hay prompts de passphrase y NO se usa ningún keystore con clave.
  - La marca secrets_configured.flag indica que el usuario ya definió sus claves
    (aunque estén vacías).
  - La passphrase/keystore legado (BBSL/secrets.bin cifrado con AES-GCM) está
    DESACTIVADO: la app ya no lo lee ni lo desbloquea en ningún momento. El
    CLI app_tools/secrets_tool.py conserva las funciones de cifrado solo como
    utilidad de desarrollo.
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


# ─────────────────────────────────────────────────────────────────────────
#  Bóveda DPAPI del usuario (sin passphrase) — estilo Windows Hello/WinCred
#  Las claves propias del usuario se guardan cifradas con la cuenta de
#  Windows y se desbloquean solas en cada apertura. No hay prompts.
# ─────────────────────────────────────────────────────────────────────────

def _vault_path() -> str:
    import config as config_mod
    return os.path.join(config_mod.USER_DATA_DIR, "secrets_vault.bin")


def _user_flag_path() -> str:
    import config as config_mod
    return os.path.join(config_mod.USER_DATA_DIR, "secrets_configured.flag")


def _protect_vault(payload: bytes) -> bytes:
    """Cifra con DPAPI (cuenta Windows); en sistemas sin DPAPI deja texto plano."""
    blob = _dpapi_protect(payload)
    if blob is not None:
        return blob
    return payload


def _unprotect_vault(payload: bytes) -> bytes:
    if payload.startswith(b"DPAPI"):
        return _dpapi_unprotect(payload)
    return payload  # fallback sin DPAPI


def save_vault(secrets: dict) -> None:
    """Guarda (o sobrescribe) las claves del usuario en la bóveda DPAPI."""
    path = _vault_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = json.dumps(secrets, ensure_ascii=False).encode("utf-8")
    blob = _protect_vault(payload)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, path)
    flag = _user_flag_path()
    os.makedirs(os.path.dirname(flag), exist_ok=True)
    if not os.path.exists(flag):
        try:
            with open(flag, "w", encoding="utf-8") as f:
                f.write("1")
        except OSError as e:
            logging.error(f"No se pudo crear la marca de claves de usuario: {e}")


def load_vault() -> dict:
    """Devuelve las claves del usuario desde la bóveda ({} si no existe/corrupta)."""
    path = _vault_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as f:
            payload = _unprotect_vault(f.read())
        secrets = json.loads(payload.decode("utf-8"))
        return secrets if isinstance(secrets, dict) else {}
    except Exception as e:
        logging.warning(f"No se pudo leer la bóveda de claves: {e}")
        return {}


def has_vault() -> bool:
    return os.path.exists(_vault_path())


def user_configured() -> bool:
    """True si el usuario ya definió sus claves (aunque estén vacías/borradas)."""
    return os.path.exists(_user_flag_path())


def delete_vault(keep_flag: bool = True) -> None:
    """Elimina la bóveda DPAPI. La marca se conserva para que el keystore
    empaquetado no vuelva a inyectar claves tras un borrado intencional."""
    try:
        if os.path.exists(_vault_path()):
            os.remove(_vault_path())
    except OSError as e:
        logging.error(f"No se pudo eliminar la bóveda: {e}")
    if not keep_flag:
        try:
            if os.path.exists(_user_flag_path()):
                os.remove(_user_flag_path())
        except OSError as e:
            logging.error(f"No se pudo eliminar la marca de claves: {e}")


def set_user_key_secret(name: str, value: str) -> None:
    """Añade/actualiza (o elimina si vacío) una clave concreta en la bóveda."""
    vault = load_vault()
    value = (value or "").strip()
    if value:
        vault[name] = value
    else:
        vault.pop(name, None)
    save_vault(vault)


def delete_user_key_secret(name: str) -> None:
    set_user_key_secret(name, "")


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
    """Inyecta los secretos en Config. Solo PICACOMIC_* va también a os.environ
    (los downloaders leen de ahí). Las API keys NO se vuelcan al entorno para
    no enmascarar/desechar los guardados explícitos del usuario (ver config.save_user_settings)."""
    from config import Config
    gemini = str(secrets.get("GEMINI_API_KEY", "")).strip()
    if gemini:
        keys = [k.strip() for k in gemini.split(",") if k.strip()]
        Config.GEMINI_API_KEY = keys[0] if keys else ""
        Config.GEMINI_API_KEYS = list(dict.fromkeys(keys))
    for name in SECRET_KEYS:
        value = secrets.get(name)
        if value is None:
            continue
        value = str(value).strip()
        if not value:
            continue
        if name.startswith("PICACOMIC"):
            os.environ[name] = value
        if hasattr(Config, name):
            setattr(Config, name, value)


def apply_vault(secrets: dict) -> None:
    """Aplica las claves de la bóveda del usuario a Config (sin tocar os.environ)."""
    from config import Config
    gemini = str(secrets.get("GEMINI_API_KEY", "")).strip()
    if gemini:
        keys = [k.strip() for k in gemini.split(",") if k.strip()]
        Config.GEMINI_API_KEY = keys[0] if keys else ""
        Config.GEMINI_API_KEYS = list(dict.fromkeys(keys))
    for name in SECRET_KEYS:
        value = secrets.get(name)
        if value is None:
            continue
        value = str(value).strip()
        if not value:
            continue
        if name.startswith("PICACOMIC"):
            os.environ[name] = value
        if hasattr(Config, name):
            setattr(Config, name, value)


def ensure_secrets(app=None, force: bool = False) -> bool:
    """Disponibilidad de claves al arranque (sin passphrase ni keystore).

    Prioridad:
      1) Claves ya cargadas en Config (.env o user_settings a nivel de proceso).
      2) Bóveda DPAPI del usuario (secrets_vault.bin) → se aplica sola.
         La marca secrets_configured.flag se respeta (el usuario puede haber
         borrado sus claves a propósito).
    El keystore legado con passphrase (BBSL/secrets.bin) está desactivado y no
    se consulta en ningún caso.
    Devuelve True si quedaron claves disponibles.
    """
    from config import Config
    if not force and (Config.GEMINI_API_KEY and Config.GEMINI_API_KEYS):
        return True

    # 1) Bóveda del usuario (directo, sin passphrase). Funciona aunque esté vacía.
    if has_vault() or user_configured():
        vault = load_vault()
        apply_vault(vault)
        return bool(Config.GEMINI_API_KEY)

    if force:
        raise FileNotFoundError("No se encontró la bóveda de claves (secrets_vault.bin).")

    logging.info("Sin claves configuradas en este PC: se abre sin claves "
                 "(guárdalas en Opciones → Seguridad).")
    return bool(Config.GEMINI_API_KEY)