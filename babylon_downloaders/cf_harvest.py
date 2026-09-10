"""
cf_harvest.py - cf_clearance manual + auto-solve con Camoufox.
==============================================================
Cuando un sitio (18mh.org, bakamh.com) devuelve 403 por un challenge
de Cloudflare (checkbox / Just a moment), la app intenta resolverlo
con un navegador real (Camoufox) o, si prefieres, te deja la cookie
pegada a mano.

MODO AUTOMATICO (activar explicitamente):
    - Opciones → Seguridad → "Desbloqueo automático de Cloudflare",
      o en .env:  CF_AUTO_SOLVE=1

Con ello, el primer 403 de cada sitio dispara un headless de Camoufox
que resuelve el challenge y guarda la cookie en cf_clearance.json.
Camoufox SOLO se usa para estos dos sitios (18mh, bakamh) y SOLO si
están instalados paquete y navegador; si faltan, la app avisa de que
hace falta descargarlo (pip install camoufox; python -m camoufox fetch).
Guardas anti-flag (no se quiere alertar a Cloudflare ni que marque tu IP):
  - 1 solo solve a la vez en todo el proceso (lock global).
  - max CF_MAX_ATTEMPTS (3) intentos por dominio por ventana de 12h.
  - cooldown de CF_SOLVE_COOLDOWN (90s) entre intentos del mismo dominio.
  - timeout de CF_SOLVE_TIMEOUT (180s) por intento.
  - si se supera el cap: solo se muestra el hint manual, no se insiste.

MODO MANUAL (cookie pegada a mano):
    python -m babylon_downloaders.cf_harvest save 18mh <cookie> [user_agent]
    python -m babylon_downloaders.cf_harvest hint 18mh

Formas de definir la cookie: cf_clearance.json (junto al .exe o en la
raiz del repo) o variables de entorno / .env:

    CF_CLEARANCE_18MH=<cookie>
    CF_UA_18MH=mozilla/...
    CF_CLEARANCE_BAKAMH=<cookie>
    CF_UA_BAKAMH=mozilla/...

Ojo: cf_clearance va ligada a la IP y (usualmente) al User-Agent desde
donde se obtuvo. Por eso el auto-solve reutiliza la UA de Camoufox con
una impersonacion de Firefox en curl_cffi.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

HOSTS = {"18mh": "18mh.org", "bakamh": "bakamh.com"}
COOKIE_NAME = "cf_clearance"

_CHALLENGE_MARKERS = (
    "_cf_chl_opt",
    "cf_chl_",
    "challenge-platform",
    "Just a moment",
    "cf-chl-widget",
    "cf_chl_state",
)


def detect_challenge(text: str) -> bool:
    if not text:
        return False
    return any(m in text for m in _CHALLENGE_MARKERS)


def jsd_needed(text: str) -> bool:
    """True si el body parece el wrapper jsd (challenge invisible/oneshot)."""
    if not text:
        return False
    try:
        from cf_jsd import jsd_needed as _j
    except Exception:
        return False
    return _j(text)


def _data_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cache_path() -> str:
    return os.path.join(_data_dir(), "cf_clearance.json")


def _cache() -> dict:
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_cache(data: dict) -> None:
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log.warning("No se pudo escribir cf_clearance.json: %s", e)


def normalize_host(host: str) -> str:
    low = host.strip().lower().rstrip(".com").rstrip(".org").lstrip("www.")
    for k, v in HOSTS.items():
        if k in low or low in v:
            return k
    return host.strip()


def load_clearance(host: str) -> Optional[dict]:
    key = normalize_host(host).upper()
    env_cookie = os.getenv(f"CF_CLEARANCE_{key}", "").strip()
    env_ua = os.getenv(f"CF_UA_{key}", "").strip()
    if env_cookie:
        return {"cookie": env_cookie, "ua": env_ua or None, "source": "env"}
    entry = _cache().get(normalize_host(host))
    if entry and entry.get("cookie"):
        return {
            "cookie": entry["cookie"],
            "ua": entry.get("ua") or None,
            "source": "cache",
        }
    return None


def save_clearance(host: str, cookie: str, ua: str = "") -> None:
    host = normalize_host(host)
    data = _cache()
    data[host] = {"cookie": cookie.strip(), "ua": ua.strip()}
    _write_cache(data)
    log.info("cf_clearance guardada para %s", HOSTS.get(host, host))


def apply_clearance(sess, host: str) -> bool:
    """Inyecta la cookie cf_clearance (+ UA) en la sesión si está configurada."""
    data = load_clearance(host)
    if not data:
        return False
    domain = HOSTS.get(normalize_host(host), host)
    try:
        sess.cookies.set(COOKIE_NAME, data["cookie"], domain=domain, path="/")
    except Exception:
        try:
            sess.headers.update({COOKIE_NAME: data["cookie"]})
        except Exception:
            return False
    if data.get("ua"):
        sess.headers.update({"User-Agent": data["ua"]})
    log.info("cf_clearance aplicada para %s (fuente: %s)", domain, data.get("source", "?"))
    return True


def hint(host: str, url: str = "") -> str:
    host = normalize_host(host)
    domain = HOSTS.get(host, host)
    lines = [
        f"Cloudflare bloqueo {domain}. Para desbloquearlo una vez:",
        f"  1. Abre en tu navegador: {url or 'https://' + domain}",
        "  2. Resuelve el challenge (es el 'Just a moment' o el checkbox).",
        "  3. F12 -> Application/Aplicacion -> Cookies -> " + domain,
        "     y copia el valor de cf_clearance (el campo completo).",
        f'  4. Pega la cookie y el User-Agent del navegador (F12 -> Network, '
        f'sus headers, campo "User-Agent"):',
        f"     python -m babylon_downloaders.cf_harvest save {host} <cookie> <user_agent>",
        "  Nota: la cookie va ligada a la IP y al User-Agent, asi que cosechala",
        "  con el mismo navegador/IP que usa la app. Dura entre horas y dias.",
    ]
    return "\n".join(lines)


def _cli(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "status":
        for host in HOSTS:
            data = load_clearance(host)
            ua = (data or {}).get("ua") or ""
            print(f"{host:8s} {'SI' if data else 'NO'}  {ua}")
        ok, msg = camoufox_status()
        print(f"auto-solve: {'ON' if auto_solve_enabled() else 'OFF'}")
        print(f"camoufox:   {'OK  ' if ok else 'FALTA'} {msg}")
        return 0
    if len(argv) < 2:
        return _usage()
    cmd = argv[0]
    if cmd == "hint":
        if len(argv) < 2:
            return _usage()
        print(hint(argv[1]))
        return 0
    if cmd == "save":
        if len(argv) < 3:
            return _usage()
        save_clearance(argv[1], argv[2], argv[3] if len(argv) > 3 else "")
        print(f"Guardada para {HOSTS.get(normalize_host(argv[1]), argv[1])}.")
        return 0
    if cmd == "solve":
        if len(argv) < 2:
            return _usage()
        host = normalize_host(argv[1])
        url = argv[2] if len(argv) > 2 else "https://" + HOSTS.get(host, host)
        solved = solve_for(host, url, force=True)
        if solved:
            print(f"Resuelta para {host}: {solved['cookie'][:40]}...")
            print("UA:", solved["ua"])
            return 0
        print("No se pudo resolver.")
        return 1
    return _usage()


def _usage() -> int:
    print(__doc__)
    print("Uso:")
    print("  python -m babylon_downloaders.cf_harvest save <18mh|bakamh> <cookie> [user_agent]")
    print("  python -m babylon_downloaders.cf_harvest hint <18mh|bakamh> [url]")
    print("  python -m babylon_downloaders.cf_harvest solve <18mh|bakamh> [url]")
    print("  python -m babylon_downloaders.cf_harvest status")
    return 1


# ══════════════════════════════════════════════════════════════
#  AUTO-SOLVE con Camoufox  (navegador real, solo para resolver)
# ══════════════════════════════════════════════════════════════

SOLVE_LOCK = threading.Lock()
_attempt_log: dict[str, list[float]] = {}

MAX_ATTEMPTS = max(1, int(os.getenv("CF_MAX_ATTEMPTS", "3")))
COOLDOWN = max(30.0, float(os.getenv("CF_SOLVE_COOLDOWN", "90")))
WINDOW = max(1.0, float(os.getenv("CF_SOLVE_WINDOW_H", "12")) * 3600)
SOLVE_TIMEOUT = max(60, int(os.getenv("CF_SOLVE_TIMEOUT", "180")))

_FIREFOX_IMP = [135, 133]


def auto_solve_enabled() -> bool:
    """Auto-solve activo: variable CF_AUTO_SOLVE=1 o el toggle de Opciones."""
    if os.getenv("CF_AUTO_SOLVE", "0") == "1":
        return True
    return bool(_user_setting("cf_auto_solve", False))


def set_auto_solve(enabled: bool) -> None:
    """Persiste el toggle en user_settings.json (Opciones → Seguridad)."""
    try:
        from config import Config

        Config.save_user_settings({"cf_auto_solve": bool(enabled)})
        log.info("cf_auto_solve -> %s", bool(enabled))
    except Exception as e:
        log.warning("No se pudo guardar cf_auto_solve: %s", e)


def _user_setting(name: str, default=None):
    try:
        from config import Config

        return Config.load_user_settings().get(name, default)
    except Exception:
        return default


def camoufox_status() -> tuple[bool, str]:
    """(ok, mensaje). El desbloqueo automático requiere el paquete 'camoufox'
    Y el navegador descargado ('python -m camoufox fetch', una sola vez)."""
    try:
        import camoufox  # noqa: F401  (paquete)
    except Exception:
        return (
            False,
            "El paquete 'camoufox' no está instalado.\n"
            "Descárgalo con:  pip install camoufox\n"
            "y luego (una sola vez):  python -m camoufox fetch",
        )
    try:
        from camoufox.pkgman import installed_verstr

        ver = installed_verstr()
    except Exception as exc:
        if "NotInstalled" in type(exc).__name__:
            return (
                False,
                "Camoufox está instalado pero falta descargar su navegador.\n"
                "Ejecuta una sola vez:  python -m camoufox fetch",
            )
        return False, "Camoufox no usable: %s" % exc
    return True, "Camoufox %s (listo)." % ver


def camoufox_available() -> bool:
    return camoufox_status()[0]


def impersonate_for(ua: str) -> str:
    m = re.search(r"Firefox/(\d+)", ua or "")
    if m:
        maj = int(m.group(1))
        ver = next((x for x in _FIREFOX_IMP if x <= maj), _FIREFOX_IMP[-1])
        return f"firefox{ver}"
    m = re.search(r"Chrome/(\d+)", ua or "")
    if m:
        maj = int(m.group(1))
        if maj < 120:
            return "chrome120"
        return "chrome136"
    return ""


def solve_for(host: str, url: str, force: bool = False,
              kind: str = "") -> Optional[dict]:
    """Resuelve el challenge y persiste la cookie.

    kind: "jsd" (invisible/oneshot, resolver sin navegador), "managed"
    (turnstile/checkbox, requiere Camoufox) o "" (detectarlo mirando url).
    Guardas anti-flag: lock global, caps y cooldown.

    Devuelve {"cookie", "ua"} o None.
    """
    if not (force or auto_solve_enabled()):
        return None
    host = normalize_host(host)

    # 1) Intento nativo si el challenge es (o puede ser) invisible jsd.
    if kind != "managed":
        site = _site_of(url) or ("https://" + HOSTS.get(host, host))
        jsd_res = _solve_jsd_first(site)
        if jsd_res:
            save_clearance(host, jsd_res["cookie"], jsd_res["ua"])
            return jsd_res

    if not camoufox_available():
        log.warning("Camoufox no esta instalado: pip install camoufox")
        return None
    with SOLVE_LOCK:
        now = time.time()
        log_ = _attempt_log.setdefault(host, [])
        log_[:] = [t for t in log_ if now - t < WINDOW]
        if len(log_) >= MAX_ATTEMPTS:
            log.warning(
                "Anti-flag: ya se hicieron %d solves en %s en la ventana; "
                "se muestra el hint manual.", len(log_), host
            )
            return None
        last = log_[-1] if log_ else 0.0
        if now - last < COOLDOWN:
            log.warning(
                "Anti-flag: cooldown activo en %s (%.0fs restantes).",
                host, COOLDOWN - (now - last),
            )
            return None
        log_.append(now)
    res = _solve_in_browser(host, url)
    if res:
        save_clearance(host, res["cookie"], res["ua"])
    return res


def _site_of(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _solve_jsd_first(site: str) -> Optional[dict]:
    """Intenta el solver JSD nativo. En challenges managed no encontrara
    el endpoint jsd y devolvera None rapidamente (sin navegador)."""
    if not site:
        return None
    try:
        from cf_jsd import solve_jsd_for_site
    except Exception as e:
        log.warning("cf_jsd import err: %s", e)
        return None
    try:
        return solve_jsd_for_site(site)
    except Exception as e:
        log.warning("cf_jsd solve err: %s", e)
        return None


def _solve_in_browser(host: str, url: str) -> Optional[dict]:
    try:
        from camoufox.sync_api import Camoufox
    except Exception as e:
        log.warning("camoufox import err: %s", e)
        return None
    deadline = time.time() + SOLVE_TIMEOUT
    # 1) headless: resolv el managed ("Just a moment") sin ventana.
    # 2) si no, headed con click en el checkbox (turnstile interactivo).
    plan = [("headless", False), ("headed", True)]
    try:
        for mode, headless in plan:
            if time.time() >= deadline:
                break
            click = mode == "headed"
            try:
                with Camoufox(headless=headless) as browser:
                    page = browser.new_page()
                    try:
                        ua = page.evaluate("navigator.userAgent")
                    except Exception:
                        ua = ""
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    except Exception as e:
                        log.warning("camoufox goto err: %s", e)
                        continue
                    clicked = False
                    while time.time() < deadline:
                        time.sleep(3)
                        try:
                            title = page.title()
                            body = (page.inner_text("body") or "")[:300]
                            cookies = page.context.cookies()
                            frames = page.frames
                        except Exception:
                            continue
                        names = {c["name"]: (c.get("value") or "") for c in cookies}
                        active = any(
                            m in (title + " " + body) for m in _CHALLENGE_MARKERS
                        )
                        if COOKIE_NAME in names and not active:
                            save_clearance(host, names[COOKIE_NAME], ua)
                            log.info(
                                "cf_clearance resuelta para %s (modo=%s)",
                                host, "headless" if headless else "headed",
                            )
                            return {"cookie": names[COOKIE_NAME], "ua": ua}
                        if click and not clicked and frames:
                                for fr in frames:
                                    try:
                                        box = fr.locator(
                                            "iframe[src*=turnstile] input[type=checkbox], "
                                            "div[class*=check] input[type=checkbox]"
                                        )
                                        if box.count() > 0 and box.first.is_visible():
                                            box.first.click(timeout=5000)
                                            clicked = True
                                            break
                                    except Exception:
                                        continue
            except Exception as e:
                log.warning("camoufox error (modo=%s): %s", headless, e)
    except Exception as e:
        log.warning("camoufox error: %s", e)
    log.warning("Timeout sin resolver para %s", host)
    return None


class CFChallengedSession:
    """Envoltorio de sesion que, ante 403 con challenge de Cloudflare,
    intenta resolver con Camoufox y reintenta una vez con la cookie nueva.

    Guardas: solo se dispara con CF_AUTO_SOLVE=1 (o force desde el CLI);
    los caps/cooldown de solve_for() aplican. Nunca dispara en bucle.
    """

    def __init__(
        self,
        host: str,
        factory: Callable[[Optional[str]], object],
        warn: Optional[Callable[[], None]] = None,
    ) -> None:
        self.host = normalize_host(host)
        self._factory = factory
        cleared = load_clearance(self.host)
        self._s = factory(impersonate_for(cleared.get("ua", "")) if cleared else None)
        self._warn = warn or (lambda: None)
        self._max_retry = 2  # 1 intento normal + 1 reintento tras solve
        apply_clearance(self._s, self.host)

    def get(self, url: str, **kw):
        return self._request("get", url, kw)

    def post(self, url: str, **kw):
        return self._request("post", url, kw)

    def put(self, url: str, **kw):
        return self._request("put", url, kw)

    def _request(self, method: str, url: str, kw: dict):
        last = None
        host_for = self.host
        try:
            host_for = urlparse(url).netloc or self.host
        except Exception:
            pass
        for attempt in range(self._max_retry):
            try:
                last = getattr(self._s, method)(url, **kw)
            except Exception as e:
                # RST / TLS reset / conexion rota: tipico de Cloudflare cuando
                # rechaza la huella JA3 o falta clearance (rechazo a nivel TCP).
                if attempt < self._max_retry - 1:
                    solved = solve_for(host_for, url)
                    if solved:
                        log.info("%s: reset TLS resuelto, reintentando…", host_for)
                        self._s = self._factory(
                            impersonate_for(solved.get("ua", "")) or None
                        )
                        apply_clearance(self._s, host_for)
                        continue
                log.info("%s: error de red (%s: %s)", host_for, type(e).__name__, e)
                break
            code = getattr(last, "status_code", None)
            snippet = (_body_snippet(last) or "")[:4096]
            is_jsd = jsd_needed(snippet)
            is_cf = detect_challenge(snippet) or is_jsd
            if code in (403, 404, 429) and is_cf:
                if attempt < self._max_retry - 1:
                    solved = solve_for(
                        host_for, url, kind="jsd" if is_jsd else "managed"
                    )
                    if solved:
                        log.info(
                            "%s: challenge resuelto, reintentando…", host_for
                        )
                        self._s = self._factory(
                            impersonate_for(solved.get("ua", "")) or None
                        )
                        apply_clearance(self._s, host_for)
                        continue
                self._warn()
            break
        return last

    # Reexpone lo minimo que usan los downloaders
    @property
    def cookies(self):
        return self._s.cookies

    @property
    def headers(self):
        return self._s.headers

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass


def _body_snippet(resp) -> str:
    try:
        return str(getattr(resp, "text", ""))
    except Exception:
        try:
            return str((getattr(resp, "content", b"") or b"")[:4096].decode("utf-8", "ignore"))
        except Exception:
            return ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_cli(sys.argv[1:]))