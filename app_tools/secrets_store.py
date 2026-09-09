"""Gestor de claves cifradas (bóveda DPAPI + keystore legado).

Modelo actual (sin passphrase para el usuario final):
  - Las claves del usuario se guardan en secrets_vault.bin, cifradas con la cuenta
    de Windows (DPAPI) — estilo Windows Hello/WinCred. Se desbloquean solas en cada
    apertura: NO hay prompts de passphrase.
  - La marca secrets_configured.flag indica que el usuario ya definió sus claves
    (aunque estén vacías), evitando que un keystore empaquetado vuelva a inyectar
    claves antiguas tras un borrado intencional.

Legado (solo si el usuario lo pide desde la UI):
  - BBSL/secrets.bin cifrado con AES-GCM + passphrase. En el arranque ya NO se pide
    la passphrase: solo se usa si está recordada en este PC (cache DPAPI). El
    desbloqueo manual desde Opciones → Seguridad lo importa a la bóveda DPAPI.
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
    """Disponibilidad de claves al arranque (SIN prompts de passphrase).

    Prioridad:
      1) Claves ya cargadas en Config (.env o user_settings a nivel de proceso).
      2) Bóveda DPAPI del usuario (secrets_vault.bin) → se aplica sola.
         La marca secrets_configured.flag deja fuera el keystore aunque la
         bóveda esté vacía (el usuario borró sus claves a propósito).
      3) Legado: keystore empaquetado (BBSL/secrets.bin) SOLO si la passphrase
         está recordada en este PC (cache DPAPI). Nunca se pide la passphrase
         en el arranque; el desbloqueo manual se hace desde Opciones → Seguridad.
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

    # 2) Legado: keystore empaquetado, solo con passphrase ya recordada.
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
            logging.warning(f"Passphrase recordada no válida: {e}")

    # Sin passphrase y sin prompt: la app abre sin claves. El usuario las define
    # desde la UI (campo API / panel Gemini) y quedan en la bóveda DPAPI.
    logging.info("Sin claves configuradas en este PC: se abre sin claves "
                 "(guárdalas en Opciones → Seguridad).")
    return bool(Config.GEMINI_API_KEY)


def decrypt_keystore_with(passphrase: str) -> dict:
    """Solo descifra (sin recordar); lo usa la UI del menú de seguridad."""
    keystore = resolve_keystore()
    if not keystore:
        raise FileNotFoundError("No se encontró secrets.bin.")
    with open(keystore, "rb") as f:
        return decrypt_secrets(f.read(), passphrase)