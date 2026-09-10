"""
cf_jsd.py - Solver nativo del challenge invisible JSD/oneshot de Cloudflare.
=======================================================================
Resuelve el challenge SILENCIOSO de Cloudflare (el de las paginas
"Checking your browser..." via jsd/main.js) SIN navegador, solo con
curl_cffi (impersonacion de TLS de Chrome). Devuelve la cookie
cf_clearance (+ __cf_bm si la da).

- No aplica a challenges gestionados (managed / turnstile checkbox); ese
  caso lo cubre cf_harvest.py con Camoufox. Para detectar cual aplicar,
  usar cf_harvest_compatible() aqui o detect_challenge() + markers.

Basado en el trabajo MIT de BOTCHATTH/cloudflare-invisible-solver
(clon de notemrovsky) y B00H0O/cloudflare-jsd-solver.

Uso interno:
    from cf_jsd import solve_jsd, jsd_needed
    cookie, ua = solve_jsd("https://www.bakamh.com/...") or (None, None)

El pipeline:
  1. GET /cdn-cgi/challenge-platform/scripts/jsd/main.js (o la URL que
     devuelva el 403/404) sin seguir redirects -> cf-ray del header.
  2. El redirect (Location) apunta a /jsd/{jsd}/main.js; extraer jsd.
  3. GET main.js -> clave de 65 chars + token oneshot float:ts:token.
  4. Construir el payload de fingerprint (obfuscado J7r-like).
  5. POST /jsd/oneshot/{jsd}/{t}/{cf_ray} con el payload -> cf_clearance.

Guardas anti-flag identicas a cf_harvest: lock global, cap de intentos
por dominio y cooldown.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

from curl_cffi import requests as creq

from cf_jsd_fingerprint import create_wb_result

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
IMPERSONATE = "chrome136"  # max que soporta curl_cffi 0.13

BASE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.8",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "priority": "u=0, i",
    "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="146", "Chromium";v="146"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": DEFAULT_UA,
}

POST_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.5",
    "cache-control": "no-cache",
    "content-type": "text/plain;charset=UTF-8",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="146", "Chromium";v="146"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": DEFAULT_UA,
}

# El challenge invisible (jsd) contiene estos markers; el managed no.
_JSD_MARKERS = ("jsd/main.js", "__CF$cv$params", "challenge-platform/scripts/jsd", "window.__CF")
_MANAGED_MARKERS = ("challenges.cloudflare.com", "turnstile", "cf-turnstile", "managed")

ENC_KEY_RE = re.compile(r"[a-zA-Z0-9+\-$]{65}")
ONESHOT_RE = re.compile(
    r"/jsd/oneshot/([a-f0-9]+)/(\d+\.\d+):(\d+):([a-zA-Z0-9+\-_$]+)/"
)

SOLVE_LOCK = threading.Lock()
_attempt_log: dict[str, list[float]] = {}
MAX_ATTEMPTS = 3
COOLDOWN = 90.0
WINDOW = 12 * 3600.0


def jsd_needed(text: str) -> bool:
    """True si el body (403/404/HTML) corresponde al challenge JSD invisible."""
    if not text:
        return False
    if any(m in text for m in _MANAGED_MARKERS):
        return False
    return any(m in text for m in _JSD_MARKERS)


def _bump(host: str) -> bool:
    with SOLVE_LOCK:
        now = time.time()
        lst = _attempt_log.setdefault(host, [])
        lst[:] = [t for t in lst if now - t < WINDOW]
        if len(lst) >= MAX_ATTEMPTS:
            log.warning("cf_jsd anti-flag: %s ya resuelto %d veces en 12h", host, MAX_ATTEMPTS)
            return False
        last = lst[-1] if lst else 0.0
        if now - last < COOLDOWN:
            log.warning("cf_jsd anti-flag: cooldown en %s", host)
            return False
        lst.append(now)
    return True


def solve_jsd(challenge_url: str, timeout: float = 30.0) -> Optional[dict]:
    """Resuelve el challenge JSD para challenge_url y devuelve
    {"cookie": cf_clearance, "ua": UA, "cookies": {..}} o None.

    challenge_url normalmente es la URL que devolvió el 403/404, ej.
    f"{site}/cdn-cgi/challenge-platform/scripts/jsd/main.js".
    """
    challenge_website = challenge_url.split("/cdn-cgi/")[0]
    host = challenge_website.replace("https://", "").split("/")[0]
    if not _bump(host):
        return None

    sess = creq.Session(impersonate=IMPERSONATE, headers=BASE_HEADERS.copy())
    try:
        # Paso 1: GET del entry point sin seguir redirects
        try:
            resp = sess.get(challenge_url, allow_redirects=False, timeout=timeout)
        except Exception as e:
            log.warning("cf_jsd GET entry err: %s", e)
            return None
        cf_ray = (resp.headers.get("cf-ray") or "").split("-")[0]
        if not cf_ray:
            log.warning("cf_jsd: sin cf-ray en %s", challenge_url)
            return None
        location = resp.headers.get("location", "")
        jsd_match = re.search(r"/jsd/([a-f0-9]+)/main\.js", location)
        if jsd_match:
            jsd = jsd_match.group(1)
            main_js_url = f"{challenge_website}{location}"
        else:
            # Algunas respuestas no usan 302: el main.js viene referenciado en el body
            alt = re.search(r"src='(/cdn-cgi/challenge-platform[^']*main\.js)'", resp.text or "")
            if not alt:
                alt = re.search(r'src="(/cdn-cgi/challenge-platform[^"]*main\.js)"', resp.text or "")
            if not alt:
                log.warning("cf_jsd: sin redirect ni main.js en %s", challenge_url)
                return None
            main_js_url = f"{challenge_website}{alt.group(1)}"
            jsd = ""

        # Paso 2: GET main.js -> clave + token
        try:
            main_resp = sess.get(main_js_url, timeout=timeout)
        except Exception as e:
            log.warning("cf_jsd GET main.js err: %s", e)
            return None
        main_text = main_resp.text or ""

        key_match = ENC_KEY_RE.search(main_text)
        if not key_match:
            log.warning("cf_jsd: sin clave 65-chars en main.js")
            return None
        enc_key = key_match.group(0)

        oneshot = ONESHOT_RE.search(main_text)
        if not oneshot:
            log.warning("cf_jsd: sin token oneshot en main.js")
            return None
        t_jsd, t_float, t_ts, t_str = oneshot.groups()
        if jsd and t_jsd != jsd:
            jsd = t_jsd

        # Paso 3: payload
        try:
            wb = create_wb_result(
                sess.headers.get("user-agent", DEFAULT_UA), enc_key, challenge_website
            )
        except Exception:
            wb = create_wb_result(DEFAULT_UA, enc_key, challenge_website)

        post_url = (
            f"{challenge_website}/cdn-cgi/challenge-platform/h/b/jsd/oneshot/"
            f"{jsd}/{t_float}:{t_ts}:{t_str}/{cf_ray}"
        )

        # Paso 4: POST
        post_headers = POST_HEADERS.copy()
        post_headers["origin"] = challenge_website
        try:
            presp = sess.post(post_url, data=wb, headers=post_headers, timeout=timeout)
        except Exception as e:
            log.warning("cf_jsd POST oneshot err: %s", e)
            return None

        if presp.status_code != 200:
            log.warning("cf_jsd POST status %s", presp.status_code)
            return None
        cookies = dict(sess.cookies)
        cookie = cookies.get("cf_clearance")
        if not cookie:
            log.warning("cf_jsd: 200 sin cf_clearance")
            return None
        log.info("cf_jsd resuelto %s", host)
        return {"cookie": cookie, "ua": sess.headers.get("user-agent", DEFAULT_UA), "cookies": cookies}
    finally:
        try:
            sess.close()
        except Exception:
            pass


def solve_jsd_for_site(site_url: str) -> Optional[dict]:
    """Atajo: dado el origen (https://site), resuelve su main.js."""
    base = (site_url or "").rstrip("/")
    return solve_jsd(base + "/cdn-cgi/challenge-platform/scripts/jsd/main.js")