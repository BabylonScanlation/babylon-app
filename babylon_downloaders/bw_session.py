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
  1. La primera vez (o cuando la SESSION venció) se abre Chromium visible
     con un perfil persistente de la app; el usuario se loguea una vez.
  2. bw_session captura las cookies del visor (SESSION incluida) y las
     guarda en <datos>/bookwalker_session.json (portable: va junto al .exe
     en la app compilada).
  3. d_bookwalker reusa esa sesión con requests: `/c`, config y tokens se
     re-derivan solos. Cuando la SESSION falla, se reabre el navegador
     brevemente (perfil persistente → sigue logueado).

Captura /c por tomo (one-shot): se abre directamente el visor del cid
(`viewer.bookwalker.jp/03/30/viewer.html?cid=…&cty=1`). Al cargar, el visor
dispara `browserWebApi/c`; interceptamos esa petición (request + headers) y
su respuesta (auth_info/base). El módulo guarda la captura igual que el
cURL pegado a mano, pero SIN re-consumir el /c.

Req:  pip install playwright && python -m playwright install chromium
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
    """Abre (o reusa) un Chromium visible con el perfil persistente de la app.

    Intenta primero el Chromium de Playwright (playwright install chromium);
    si no está (típico en un .exe compilado o en una máquina ajena), cae al
    Edge/Chrome del sistema (`channel=msedge`/`chrome`), que Windows incluye.
    En todos los casos el perfil es el propio de la app (bw_profile), nunca el
    navegador personal del usuario.
    """
    global _pw, _context
    if _context is not None:
        return _context
    from playwright.sync_api import sync_playwright

    _pw = sync_playwright().start()
    last_err = None
    for opts in (dict(), {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            _context = _pw.chromium.launch_persistent_context(
                _profile_dir(),
                headless=False,
                viewport=None,
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
                **opts,
            )
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
        sid = _find_session(context_cookies())
        if sid:
            sess = _persist_cookies(ctx)
            sess["sid"] = sid
            save_session(sess)
            return sess
        time.sleep(1.5)
    _persist_cookies(ctx)  # igualmente guarda lo que haya para depurar
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
        # Si el visor pidió login, el usuario entra aquí y /c se dispara solo.
        time.sleep(0.8)
    _persist_cookies(ctx)

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