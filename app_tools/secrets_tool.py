"""Generador / validador del keystore de claves (BBSL/secrets.bin).

Uso:
  python app_tools/secrets_tool.py build --env .env --passphrase <P> [--out BBSL/secrets.bin]
  python app_tools/secrets_tool.py check --passphrase <P> [--in BBSL/secrets.bin]
  python app_tools/secrets_tool.py build --random --out BBSL/secrets.bin   # genera y muestra la passphrase

El keystore cifra SOLO estas claves del .env:
  GEMINI_API_KEY, MISTRAL_API_KEY, DEEPL_API_KEY,
  PICACOMIC_EMAIL, PICACOMIC_PASSWORD, PICACOMIC_TOKEN

Consejo: usa la misma passphrase para todo el equipo (distribúyela por un canal
seguro, ej. un gestor de contraseñas compartido). Cada usuario solo la escribe
UNA vez por PC; luego la app la recuerda protegida con la cuenta de Windows.
"""

import argparse
import os
import secrets as pysecrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_tools.secrets_store import (  # noqa: E402
    decrypt_secrets,
    encrypt_secrets,
    SECRET_KEYS,
)

DEFAULT_OUT = os.path.join("BBSL", "secrets.bin")


def load_env(path: str) -> dict:
    """Lee del .env solo las variables que interesan (sin comentarios ni comillas)."""
    secrets = {}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in SECRET_KEYS and value:
                secrets[key] = value
    return secrets


def masked(value: str) -> str:
    value = str(value)
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def cmd_build(args) -> int:
    if not os.path.exists(args.env):
        print(f"ERROR: no existe {args.env}", file=sys.stderr)
        return 1
    secrets = load_env(args.env)
    if not secrets:
        print(f"ERROR: no se hallaron claves válidas en {args.env}", file=sys.stderr)
        return 1
    passphrase = args.passphrase
    if args.random or not passphrase:
        passphrase = pysecrets.token_urlsafe(24)
        print(f"Passphrase generada (guárdala): {passphrase}", file=sys.stderr)
    blob = encrypt_secrets(secrets, passphrase)
    out = args.out or DEFAULT_OUT
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "wb") as f:
        f.write(blob)
    print(f"OK: keystore escrito en {out} ({len(blob)} bytes).")
    print(f"Claves incluidas ({len(secrets)}):")
    for key in SECRET_KEYS:
        if secrets.get(key):
            print(f"  - {key}: {masked(secrets[key])}")
    missing = [k for k in SECRET_KEYS if not secrets.get(k)]
    if missing:
        print(f"Aviso: faltan en el .env: {', '.join(missing)}")
    print("Recuerda eliminar/recortar el .env del repo antes de publicar.")
    return 0


def cmd_check(args) -> int:
    inp = args.inp or DEFAULT_OUT
    if not os.path.exists(inp):
        print(f"ERROR: no existe {inp}", file=sys.stderr)
        return 1
    with open(inp, "rb") as f:
        blob = f.read()
    try:
        secrets = decrypt_secrets(blob, args.passphrase)
    except Exception as e:
        print(f"ERROR: passphrase incorrecta o keystore inválido: {e}", file=sys.stderr)
        return 1
    print(f"OK: keystore descifrado correctamente ({len(secrets)} claves).")
    for key in sorted(secrets):
        print(f"  - {key}: {masked(secrets[key])}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="secrets_tool", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Genera el keystore desde un .env")
    b.add_argument("--env", default=".env")
    b.add_argument("--out", default=DEFAULT_OUT)
    b.add_argument("--passphrase", default="")
    b.add_argument("--random", action="store_true", help="Genera una passphrase aleatoria")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("check", help="Comprueba que la passphrase descifra el keystore")
    c.add_argument("--in", dest="inp", default=DEFAULT_OUT)
    c.add_argument("--passphrase", required=True)
    c.set_defaults(func=cmd_check)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())