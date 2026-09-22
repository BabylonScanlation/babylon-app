"""
bw_session.py - sesión automática de bookwalker.jp vía navegador gestionado.
===========================================================================
Por qué hace falta un navegador (solo una vez, no es "config"):
  - Para descargar tomos COMPRADOS se necesita la cookie SESSION del visor
    member (viewer.bookwalker.jp). Esa SESSION nace al iniciar sesión y
    expira cuando vence la sesión web de la cuenta.
  - BookWalker solo ofrece login SNS (Google/Twitter/Naver) = OAuth, que
    exige navegador (consentimiento). No hay un POST email/contraseña
    aprovechable headless sin guardar credenciales (peor que una cookie).
  - TODO lo demás (firma CloudFront ~1h, configuration_pack.json y los
    tokens por página) se renueva headless con `requests`; aquí solo se
    cosechan la SESSION y las cookies del navegador del dueño de la cuenta.

Flujo:
  1. Se abre el navegador REAL de Chrome/Edge del usuario (donde su cuenta ya
     está logueada). Es la forma preferida: cero login extra. Único requisito:
     que ese navegador esté CERRADO durante la captura (los perfiles no se
     abren dos veces). Si no hay perfil real, se usa un Chromium propio.
  2. bw_session captura las cookies del visor (SESSION incluida) y las
     guarda en <datos>/bookwalker_session.json (portable: va junto al .exe
     en la app compilada).
  3. d_bookwalker reusa esa sesión con requests: `/c`, config y tokens se
     re-derivan solos. Cuando la SESSION falla, se reabre el navegador del
     usuario brevemente (su perfil → sigue logueado).

Captura /c por tomo (one-shot): se abre directamente el visor del cid
(`viewer.bookwalker.jp/03/30/viewer.html?cid=…&cty=1`). Al cargar, el visor
dispara `browserWebApi/c`; interceptamos esa petición (request + headers) y
su respuesta (auth_info/base). El módulo guarda la captura igual que el
cURL pegado a mano, pero SIN re-consumir el /c.

Req:  pip install playwright  (el Chromium de Playwright es opcional: con el
perfil real se usa el Edge/Chrome del sistema, `channel=msedge/chrome`).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Optional

log = logging.getLogger(__name__)

_VIEWER_REFERER_TPL = "https://viewer.bookwalker.jp/03/30/viewer.html?cid={cid}&cty=1"
_SESSION_TIMEOUT = 600  # s. de espera a que aparezca SESSION (login manual)


# ── ubicación de datos (portable en la app compilada) ────────────────────────

def data_dir() -> str:
    """Directorio de datos: junto al .exe si es una app compilada."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _profile_dir() -> str:
    d = os.path.join(data_dir(), "bookwalker", "bw_profile")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


# ── perfil real del usuario (modo preferido) ──────────────────────────────────

_PROGRAMFILES = os.environ.get("PROGRAMFILES", "")
_PROGRAMFILES_X86 = os.environ.get("PROGRAMFILES(X86)", "")
_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
_APPDATA = os.environ.get("APPDATA", "")

_REAL_PROFILES = (
    ("Edge",   os.path.join(_LOCALAPPDATA, "Microsoft", "Edge",   "User Data"), "msedge", None),
    ("Chrome", os.path.join(_LOCALAPPDATA, "Google",   "Chrome", "User Data"), "chrome", None),
)

_CHROMIUM_BY_BIN = (
    (
        "Brave",
        os.path.join(_LOCALAPPDATA, "BraveSoftware", "Brave-Browser", "User Data"),
        (
            os.path.join(_PROGRAMFILES, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.join(_PROGRAMFILES_X86, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.join(_LOCALAPPDATA, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ),
    ),
    (
        "Opera",
        os.path.join(_LOCALAPPDATA, "Opera Software", "Opera Stable"),
        (
            os.path.join(_PROGRAMFILES, "Opera", "opera.exe"),
            os.path.join(_PROGRAMFILES_X86, "Opera", "opera.exe"),
            os.path.join(_LOCALAPPDATA, "Programs", "Opera", "opera.exe"),
        ),
    ),
)


def _profile_locked(user_data: str) -> bool:
    """True si el navegador está abierto (el perfil no se puede reabrir)."""
    try:
        for flag in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            if os.path.exists(os.path.join(user_data, flag)):
                return True
    except OSError:
        return True
    return False


def _chromium_profile(name: str, user_data: str, channel: str, exes: tuple = ()) -> Optional[dict]:
    """Perfil Chromium con carpeta Default. Edge/Chrome se abren por `channel`;
    Brave/Opera por `executable_path` (Playwright busca el binario)."""
    if not user_data or not os.path.isdir(os.path.join(user_data, "Default")):
        return None
    exe = next((e for e in exes if e and os.path.exists(e)), None)
    return {
        "name": name,
        "user_data": user_data,
        "channel": channel,
        "executable_path": exe,
        "running": _profile_locked(user_data),
    }


def _firefox_profile() -> Optional[dict]:
    """Perfil real de Firefox (APPDATA/Mozilla/Firefox/Profiles). Prefiere el
    perfil .default-release; el lock es `parent.lock`, no SingletonLock."""
    base = os.path.join(_APPDATA, "Mozilla", "Firefox", "Profiles")
    try:
        entries = sorted(
            e for e in os.listdir(base) if os.path.isdir(os.path.join(base, e))
        )
    except OSError:
        return None
    if not entries:
        return None

    def score(n: str) -> int:
        if ".default-release" in n:
            return 3
        if ".default" in n:
            return 2
        if "dev-edition" in n:
            return 1
        return 0

    best = max(entries, key=score)
    prof = os.path.join(base, best)
    return {
        "name": "Firefox",
        "user_data": prof,
        "channel": "firefox",
        "executable_path": None,
        "running": os.path.exists(os.path.join(prof, "parent.lock")),
    }


def _real_profile() -> Optional[dict]:
    """Primer perfil REAL disponible: Edge, Chrome, Brave, Opera y Firefox."""
    for name, user_data, channel, exes in _REAL_PROFILES:
        p = _chromium_profile(name, user_data, channel, exes or ())
        if p:
            return p
    for name, user_data, exes in _CHROMIUM_BY_BIN:
        p = _chromium_profile(name, user_data, "", exes)
        if p:
            return p
    return _firefox_profile()


def real_profile_status() -> tuple:
    """(nombre, en_uso) del navegador real que se abrirá, o ('gestionado', False)."""
    r = _real_profile()
    if not r:
        return ("Navegador gestionado por la app", False)
    return (r["name"], r["running"])


def _session_path() -> str:
    return os.path.join(data_dir(), "bookwalker", "bookwalker_session.json")


def importable() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        return True
    except Exception:
        return False


# ── sesión persistida ─────────────────────────────────────────────────────────

def load_session() -> dict:
    try:
        with open(_session_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_session(sess: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_session_path()), exist_ok=True)
        with open(_session_path(), "w", encoding="utf-8") as f:
            json.dump(sess, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def clear_session() -> None:
    try:
        os.remove(_session_path())
    except OSError:
        pass


def _cookie_value(sess: dict) -> Optional[str]:
    return str(sess.get("sid") or "").strip() or None


def session_sid() -> Optional[str]:
    return _cookie_value(load_session())


def have_session() -> bool:
    """True si hay una SESSION guardada (no garantiza que siga viva en el server)."""
    return bool(session_sid())


def cookie_header(sess: dict) -> str:
    """Reconstruye el header `Cookie` con las cookies del dominio bookwalker.jp."""
    parts = []
    for c in sess.get("cookies") or []:
        try:
            name, value = str(c.get("name") or ""), str(c.get("value") or "")
            if name and value is not None:
                parts.append(f"{name}={value}")
        except Exception:
            continue
    return "; ".join(parts)


def _as_playwright_cookies(browser_cookies) -> list:
    """Normaliza cookies de Playwright a {name, value, domain, path}."""
    out = []
    for c in browser_cookies:
        try:
            out.append(
                {
                    "name": str(c.get("name") or ""),
                    "value": str(c.get("value") or ""),
                    "domain": str(c.get("domain") or ""),
                    "path": str(c.get("path") or "/"),
                }
            )
        except Exception:
            continue
    return out


def _find_session(browser_cookies) -> str:
    for c in browser_cookies:
        if str(c.get("name")) == "SESSION" and "viewer" in (c.get("domain") or ""):
            return str(c.get("value") or "")
    return ""


def _persist_cookies(context) -> dict:
    """Guarda las cookies del perfil + UA como sesión reutilizable."""
    try:
        cookies = context.cookies()
        sid = _find_session(cookies)
        ua = "Mozilla/5.0"
        try:
            ua = context.pages[0].evaluate("navigator.userAgent") if context.pages else ua
        except Exception:
            pass
        sess = {
            "captured_at": time.time(),
            "sid": sid,
            "ua": ua,
            "cookies": _as_playwright_cookies(cookies),
        }
        save_session(sess)
        return sess
    except Exception as e:
        log.warning("No se pudieron persistir cookies del visor: %s", e)
        return {}


# ── navegador gestionado ──────────────────────────────────────────────────────

_pw = None
_context = None
_pages = []


def _launch():
    """Abre el navegador del USUARIO (modo preferido) o, si no hay perfil real,
    un Chromium/Edge/Chrome/Firefox propio de la app.

    Preferencia: se usa el perfil real de Edge/Chrome/Brave/Opera/Firefox (la
    cuenta ya está logueada ahí) → no se pide login nunca. Requisito único: ese
    navegador debe estar CERRADO en el momento de la captura (los perfiles no se
    abren dos veces); al terminar, el usuario puede volver a abrirlo.
    """
    global _pw, _context
    if _context is not None:
        try:
            if not _context.is_closed():
                return _context
        except Exception:
            pass
        # Contexto viejo/cerrado: limpiar y relanzar desde cero.
        _context = None
        try:
            if _pw is not None:
                _pw.stop()
        except Exception:
            pass
        _pw = None
    from playwright.sync_api import sync_playwright

    _pw = sync_playwright().start()
    real = _real_profile()
    if real:
        if real["running"]:
            try:
                _pw.stop()
            except Exception:
                pass
            _pw = None
            raise RuntimeError(
                f"Tu navegador ({real['name']}) está abierto y se necesita una "
                f"vez. Cerrá {real['name']} y reintentá. Esto toma la sesión de "
                f"tu cuenta y no lo vuelve a pedir."
            )
        launch = dict(
            channel=real["channel"],
            headless=False,
            viewport=None,
        ) if real["channel"] else dict(headless=False, viewport=None)
        if real.get("executable_path"):
            launch["executable_path"] = real["executable_path"]
        # Los flags de abajo son de Chromium; Firefox no los acepta.
        if real["channel"] in ("msedge", "chrome", ""):
            launch["args"] = [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ]
        try:
            bt = _pw.firefox if real["channel"] == "firefox" else _pw.chromium
            _context = bt.launch_persistent_context(real["user_data"], **launch)
            _context.set_default_timeout(30000)
            return _context
        except Exception as e:
            try:
                _pw.stop()
            except Exception:
                pass
            _pw = None
            raise RuntimeError(
                f"No se pudo abrir tu perfil real de {real['name']}: {e}"
            )

    # Sin perfil real: navegador propio de la app (dev / máquina sin browser real).
    last_err = None
    for opts in (
        dict(),
        {"channel": "msedge"},
        {"channel": "chrome"},
        {"channel": "firefox"},
    ):
        try:
            bt = _pw.firefox if opts.get("channel") == "firefox" else _pw.chromium
            kwargs = dict(headless=False, viewport=None)
            if opts.get("channel") != "firefox":
                kwargs["args"] = ["--disable-blink-features=AutomationControlled"]
            _context = bt.launch_persistent_context(_profile_dir(), **opts, **kwargs)
            break
        except Exception as e:
            last_err = e
            _context = None
            log.info("Intento de navegador %s falló: %s", opts or "(bundled)", e)
    if _context is None:
        try:
            _pw.stop()
        except Exception:
            pass
        _pw = None
        raise RuntimeError(
            "No se pudo abrir ningún navegador (¿Playwright sin Chromium "
            "instalado y sin Edge/Chrome en el sistema?). Último error: %s" % last_err
        )
    _context.set_default_timeout(30000)
    return _context


def _close() -> None:
    global _pw, _context
    try:
        if _context is not None:
            _context.close()
    except Exception:
        pass
    try:
        if _pw is not None:
            _pw.stop()
    except Exception:
        pass
    _context = _pw = None


def capture_login(timeout: int = _SESSION_TIMEOUT) -> Optional[dict]:
    """Abre bookwalker.jp y espera a que el usuario quede logueado.

    Cuando aparece la cookie SESSION del visor (i.e. hay cuenta activa),
    guarda la sesión y la devuelve. None si se agotó el tiempo.
    """
    if not importable():
        return None
    ctx = _launch()
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto("https://bookwalker.jp/", wait_until="domcontentloaded")
    except Exception:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        # El usuario cerró la ventana → no seguir esperando 600s.
        try:
            if ctx.is_closed():
                break
        except Exception:
            break
        sid = _find_session(context_cookies())
        if sid:
            sess = _persist_cookies(ctx)
            sess["sid"] = sid
            save_session(sess)
            return sess
        time.sleep(1.5)
    try:
        if not ctx.is_closed():
            _persist_cookies(ctx)  # igualmente guarda lo que haya para depurar
    except Exception:
        pass
    return None


def context_cookies() -> list:
    try:
        return _context.cookies() if _context is not None else []
    except Exception:
        return []


def capture_c_for_cid(cid: str, timeout: int = _SESSION_TIMEOUT) -> Optional[dict]:
    """Abre el visor member del cid y captura la petición `/c` (one-shot).

    Devuelve {cid, url, auth_info, cty, cookie, referer, ua, sid} o None.
    El `/c` lo consume el propio navegador; NO se re-pide con requests.
    Si la sesión del perfil sigue viva, el visor se abre y dispara /c solo;
    si no, se muestra el login y hay que entrar una vez.
    """
    if not importable():
        return None
    ctx = _launch()
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    hits: list = []

    def on_response(resp):
        try:
            if "/browserWebApi/c" in resp.url:
                req = resp.request
                try:
                    body = resp.json() or {}
                except Exception:
                    body = {}
                hits.append(
                    {
                        "url": req.url,
                        "headers": req.headers,
                        "body": body,
                        "status": resp.status,
                    }
                )
        except Exception:
            pass

    page.on("response", on_response)

    # 1) Da contexto de tienda (site session) para que /c funcione limpio.
    try:
        page.goto(f"https://bookwalker.jp/de{cid}/", wait_until="domcontentloaded")
    except Exception:
        pass
    # 2) Abre el visor member del tomo.
    try:
        page.goto(
            _VIEWER_REFERER_TPL.format(cid=cid), wait_until="domcontentloaded"
        )
    except Exception:
        pass

    deadline = time.time() + timeout
    while time.time() < deadline and not hits:
        # El usuario cerró la ventana → cortar antes del timeout.
        try:
            if ctx.is_closed():
                break
        except Exception:
            break
        # Si el visor pidió login, el usuario entra aquí y /c se dispara solo.
        time.sleep(0.8)
    try:
        if not ctx.is_closed():
            _persist_cookies(ctx)
    except Exception:
        pass

    if not hits:
        return None
    hit = hits[0]
    req_url = str(hit.get("url") or "")
    body = hit.get("body") or {}
    if not isinstance(body, dict) or str(body.get("status")) != "200":
        # Si no hay respuesta JSON utilizable, con el request alcanza:
        # d_bookwalker puede re-pedir /c idéntico (misma SESSION).
        body = {}
    headers = hit.get("headers") or {}
    cookie = str(headers.get("cookie") or "")
    referer = str(headers.get("referer") or "")
    ua = str(headers.get("user-agent") or "")

    data = {
        "cid": cid,
        "url": str(body.get("url") or req_url).rstrip("/"),
        "auth_info": body.get("auth_info") or {},
        "cty": int(body.get("cty") or 0),
        "cookie": cookie,
        "referer": referer,
        "ua": ua,
        "sid": _find_session(context_cookies()),
        "captured_at": time.time(),
    }
    return data


close = _close


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Asistente de sesión BookWalker")
    ap.add_argument(
        "comando",
        choices=["login", "capturar"],
        help="login: loguear en bookwalker.jp; capturar: abrir visor de un cid",
    )
    ap.add_argument("--cid", help="cid para el modo capturar", default="")
    ap.add_argument(
        "--timeout", type=int, default=_SESSION_TIMEOUT, help="segundos de espera"
    )
    args = ap.parse_args()

    if args.comando == "login":
        res = capture_login(timeout=args.timeout)
        print("SESION OK, SID:", (res or {}).get("sid", "")[:12] + "…" if res else "None")
    else:
        if not args.cid:
            print("Falta --cid")
            sys.exit(2)
        res = capture_c_for_cid(args.cid, timeout=args.timeout)
        if not res:
            print("No se capturó /c (¿no iniciaste sesión o no es tu tomo?)")
            sys.exit(1)
        print("CAPTURA OK cid:", res["cid"], "| base:", res["url"][:70])
    close()