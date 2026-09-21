"""
d_bookwalker_har.py — BOOKWALKER member (tomo comprado) vía HAR/tokens.

Vía descubierta en la sesión de scraping (PARA_TORU / _DESCUBRIMIENTO_SCRAPER):
  - El HAR "member" del visor NO trae los bytes de las imágenes: solo el TOKEN
    por página. Cada imagen vive en
        {base}/OEBPS/text/p-XXX.xhtml/{TOKEN}.jpeg
    (p-cover.xhtml + p-001..p-164.xhtml = 167 tokens).
  - FLUJO A ("/c"): pegando el cURL de la petición browserWebApi/c se obtiene
    el JSON con auth_info (hti, cfg, bid, uuid, pfCd, Policy, Signature,
    Key-Pair-Id) y la url base. Las descargas llevan esa auth_info como query.
  - FLUJO B ("pages"): pegando el cURL de CUALQUIER imagen del HAR se extraen
    Cookie/UA/Referer/host y se descarga con esos headers + los mismos tokens.
  - Las 167 SOLO se bajan desde la máquina/IP que tiene el tomo abierto con su
    cuenta (los cURL/interacciones salen del navegador del dueño del libro).

El downloader:
  - search(text) acepta: ruta a un .har, el texto del .har, el cURL de /c (A)
    o el cURL de una imagen "pages" (B).
  - FLUJO A deriva los tokens AUTOMÁTICAMENTE: baja configuration_pack.json
    (misma auth_info), lo descifra (decodeConfig/a8j..tB0l + b8gNo, port del
    visor en d_bookwalker_xtea.py) y calcula los 167 tokens; el HAR deja de
    ser obligatorio.
  - Guarda cada captura por cid en bookwalker_har.json (mismo patrón que
    bookwalker_member.json del d_bookwalker).
  - get_series devuelve el tomo con UN capítulo (todo el tomo).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlencode

import requests

from common import CFG, BaseDownloader

# Descifrado de configuration_pack.json + derivación de tokens (port del visor)
try:
    from d_bookwalker_xtea import decrypt_config as _decrypt_config
    from d_bookwalker_xtea import build_tokens as _build_tokens
except Exception:
    _decrypt_config = None
    _build_tokens = None

# De-scramble de imágenes miembro (port de B2y/a3f/A9p + Page seeds)
try:
    from d_bookwalker_unscramble import build_seeds_for_book as _build_seeds
    from d_bookwalker_unscramble import unscramble as _unscramble
    _HAS_UNSCRAMBLE = True
except Exception:
    _build_seeds = None
    _unscramble = None
    _HAS_UNSCRAMBLE = False

# ── paths / constantes ────────────────────────────────────────────────────────

_STORAGE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bookwalker_har.json"
)

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
_CID_PARAM_RE = re.compile(r"[?&]cid=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
_CID_DE_RE = re.compile(r"/de([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
_XHTML_TOKEN_RE = re.compile(
    r"/OEBPS/text/((?:p-[a-z0-9_-]+)\.xhtml)/([0-9a-f]{12,})\.jpeg", re.I
)
_BASE_BEFORE_OEBPS_RE = re.compile(r"(https?://[^/]+/.+?/OEBPS/text/)", re.I)
_CURL_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_CURL_H_RE = re.compile(r'''\-H\s+(?:"([^"]+)"|'([^']+)')''', re.I)
_CURL_COOKIE_RE = re.compile(r'''\-b\s+(?:"([^"]+)"|'([^']+)')''', re.I)
_CURL_REFERER_RE = re.compile(r'''\-e\s+(?:"([^"]+)"|'([^']+)')''', re.I)

_AUTH_KEYS = ("hti", "cfg", "bid", "uuid", "pfCd", "Policy", "Signature", "Key-Pair-Id")
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)
_DEFAULT_REFERER = "https://viewer.bookwalker.jp/"


# ── storage de capturas ───────────────────────────────────────────────────────

def load_har_captures() -> dict:
    try:
        with open(_STORAGE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_har_captures(captures: dict) -> None:
    try:
        with open(_STORAGE, "w", encoding="utf-8") as f:
            json.dump(captures, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def clear_har_captures() -> None:
    try:
        os.remove(_STORAGE)
    except OSError:
        pass


# ── helpers de parseo ─────────────────────────────────────────────────────────

def _extract_cid(data: str, base: str = "") -> str:
    m = _CID_PARAM_RE.search(data) or _CID_DE_RE.search(data)
    if m:
        return m.group(1).lower()
    if base:
        m = _UUID_RE.search(base)
        if m:
            return m.group(0).lower()
    m = _UUID_RE.search(data)
    return m.group(0).lower() if m else ""


def _sort_key(page: str) -> tuple:
    m = re.match(r"p-(\d+)", page)
    return (0, int(m.group(1))) if m else (1, page)


def _parse_curl_request(curl: str) -> Dict[str, str]:
    """Extrae URL, cookies y headers de un 'Copy as cURL' (cmd)."""
    text = (curl or "").replace("^", "")
    m = _CURL_URL_RE.search(text)
    url = m.group(0).rstrip(";") if m else ""
    headers: Dict[str, str] = {}
    for hm in _CURL_H_RE.finditer(text):
        raw = (hm.group(1) or hm.group(2) or "").strip()
        if ":" in raw:
            k, v = raw.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    cookie = ""
    cm = _CURL_COOKIE_RE.search(text)
    if cm:
        cookie = (cm.group(1) or cm.group(2) or "").strip()
    else:
        cookie = headers.pop("cookie", "")
    if cookie and "cookie" not in headers:
        headers["cookie"] = cookie
    rm = _CURL_REFERER_RE.search(text)
    if rm and "referer" not in headers:
        headers["referer"] = (rm.group(1) or rm.group(2) or "").strip()
    return {"url": url, "cookie": cookie, "headers": headers}


def _pick_capture(captures: dict, cid: str, *, lacking_session: bool = False) -> Optional[str]:
    if cid in captures:
        return cid
    # Hay una sola captura sin sesión → asumir que el cURL le pertenece
    if not cid:
        candidates = [k for k, v in captures.items() if not v.get("session")]
        return candidates[0] if len(candidates) == 1 else None
    return None


def _flow_item(cid: str, title: str) -> dict:
    return {"id": cid, "cid": cid, "title": title, "slug": cid}


def _capture_title(cap: dict, cid: str) -> str:
    t = str(cap.get("title") or "").strip()
    return t or f"bookwalker {cid[:8]}"


# ══════════════════════════════════════════════════════════════════════════════

class DownloaderBookwalkerHar(BaseDownloader):
    NAME = "BOOKWALKER-HAR (member/HAR)"
    HAS_SEARCH = True
    HAS_CATALOG = True
    NEEDS_LOGIN = False
    HAR_SESSION = True

    def __init__(self) -> None:
        self._sess = requests.Session()
        self._active: Optional[dict] = None

    # ── importación de HAR ────────────────────────────────────────────────────

    def _import_har_text(self, text: str) -> list:
        tokens: Dict[str, str] = {}
        bases: set[str] = set()
        for m in _XHTML_TOKEN_RE.finditer(text or ""):
            page, tok = m.group(1).lower(), m.group(2).lower()
            tokens.setdefault(page, tok)
        for m in _BASE_BEFORE_OEBPS_RE.finditer(text or ""):
            bases.add(m.group(1).rstrip("/") + "/")
        if not tokens:
            return []
        base = min(bases, key=len) if bases else ""
        cid = _extract_cid(text or "", base)
        key = cid or ("gen_" + hashlib.md5("\n".join(sorted(tokens)).encode()).hexdigest()[:10])

        caps = load_har_captures()
        cap = caps.get(key, {"cid": key, "created_at": time.time()})
        cap["tokens"] = tokens
        if base:
            cap["pages_base"] = base
        if not cap.get("title"):
            cap["title"] = f"bookwalker {key[:8]}"
        cap.setdefault("session", None)
        caps[key] = cap
        save_har_captures(caps)
        return [_flow_item(key, _capture_title(cap, key))]

    def _import_curl_c(self, curl: str) -> list:
        """FLUJO A: cURL de browserWebApi/c → auth_info, base y tokens de
        configuration_pack.json descifrado (ya no hace falta el HAR)."""
        parsed = _parse_curl_request(curl)
        url = parsed["url"]
        if "/browserWebApi/c" not in url:
            return []
        qs = dict(parse_qsl(url.split("?", 1)[1] if "?" in url else ""))
        cid = str(qs.get("cid") or _extract_cid(url) or "").lower()

        headers = {"User-Agent": _DEFAULT_UA, "Referer": _DEFAULT_REFERER}
        if parsed["cookie"]:
            headers["Cookie"] = parsed["cookie"]
        for k in ("user-agent", "referer"):
            v = parsed["headers"].get(k)
            if v:
                headers["User-Agent" if k == "user-agent" else "Referer"] = v

        try:
            r = self._sess.get(url, headers=headers, timeout=CFG.get("timeout", (15, 45)))
        except Exception:
            return []
        try:
            d = r.json()
        except Exception:
            return []
        if not isinstance(d, dict) or str(d.get("status")) != "200" or not d.get("url"):
            return []

        info = d.get("auth_info") or {}
        query = urlencode(
            {k: str(info[k]) for k in _AUTH_KEYS if info.get(k) is not None and str(info[k]) != ""}
        )
        base = str(d["url"]).rstrip("/")
        img_base = base + "/OEBPS/text/"

        caps = load_har_captures()
        key = _pick_capture(caps, cid)
        if key is None:
            key = cid or "gen_" + hashlib.md5(img_base.encode()).hexdigest()[:10]
        cap = caps.get(key, {"cid": key, "created_at": time.time()})
        cap.setdefault("tokens", {})
        cap["session"] = {
            "flow": "c",
            "img_base": img_base,
            "query": query,
            "cookie": parsed["cookie"],
            "referer": headers.get("Referer") or _DEFAULT_REFERER,
            "captured_at": time.time(),
        }

        # Tokens automáticos: bajar y descifrar configuration_pack.json
        auto = self._derive_tokens_auto(base, query, headers)
        if auto is not None:
            cap["tokens"] = auto["tokens"]
            cap["tokens_from"] = "config"
            cap["config"] = auto["config"]
            cap["keys"] = auto["keys"]

        if not cap.get("title"):
            cap["title"] = f"bookwalker {(cid or key)[:8]}"
        caps[key] = cap
        save_har_captures(caps)
        return [_flow_item(key, _capture_title(cap, key))]

    def _derive_tokens_auto(self, base: str, query: str, headers: dict) -> Optional[dict]:
        """Baja configuration_pack.json, lo descifra y saca los tokens por
        página. Devuelve {tokens: {fid: token}, config, keys} o None si algo
        falla. config y keys se guardan en la captura para poder calcular las
        semillas de de-scramble offline."""
        cfg_url = f"{base}/configuration_pack.json?{query}"
        try:
            r = self._sess.get(
                cfg_url, headers=headers, timeout=CFG.get("timeout", (15, 60))
            )
            if r.status_code != 200:
                return None
            cfg, k1, k2, k3 = _decrypt_config(r.text)
        except Exception:
            return None
        if not cfg:
            return None
        tokens = _build_tokens(cfg, k1, k2, k3)
        # Las claves de build_tokens son "OEBPS/text/p-001.xhtml"; aquí se
        # guardan como basename "p-001.xhtml", igual que vienen del HAR
        return {
            "tokens": {fid.rsplit("/", 1)[-1]: tok for fid, tok in tokens.items()},
            "config": cfg,
            "keys": [k1, k2, k3],
        }

    def _import_curl_pages(self, curl: str) -> list:
        """FLUJO B: cURL de una imagen 'pages' del HAR → headers + base."""
        parsed = _parse_curl_request(curl)
        url = parsed["url"]
        m = _BASE_BEFORE_OEBPS_RE.search(url)
        if not m:
            return []
        img_base = m.group(1).rstrip("/") + "/"
        cid = _extract_cid(url, img_base)

        headers: Dict[str, str] = {"User-Agent": _DEFAULT_UA, "Referer": _DEFAULT_REFERER}
        if parsed["cookie"]:
            headers["Cookie"] = parsed["cookie"]
        for k in ("user-agent", "referer"):
            v = parsed["headers"].get(k)
            if v:
                headers["User-Agent" if k == "user-agent" else "Referer"] = v

        caps = load_har_captures()
        key = _pick_capture(caps, cid)
        if key is None:
            key = cid or "pages_" + hashlib.md5(img_base.encode()).hexdigest()[:10]
        cap = caps.get(key, {"cid": key, "created_at": time.time()})
        cap.setdefault("tokens", {})
        cap["session"] = {
            "flow": "pages",
            "img_base": img_base,
            "headers": headers,
            "captured_at": time.time(),
        }
        cap["pages_base"] = img_base
        if not cap.get("title"):
            cap["title"] = f"bookwalker {(cid or key)[:8]}"
        caps[key] = cap
        save_har_captures(caps)
        return [_flow_item(key, _capture_title(cap, key))]

    # ── interfaz BaseDownloader ───────────────────────────────────────────────

    def search(self, query: str) -> list:
        q = (query or "").strip()
        if not q:
            return []

        # archivo .har en disco
        if os.path.isfile(q):
            try:
                with open(q, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                return []
            return self._import_har_text(text)

        # cURL de /c → FLUJO A
        if "/browserWebApi/c" in q:
            return self._import_curl_c(q)

        # cURL de una imagen (pages) → FLUJO B
        if "/OEBPS/text/" in q:
            return self._import_curl_pages(q)

        # HAR pegado como texto/JSON
        if q.lstrip().startswith("{"):
            return self._import_har_text(q)

        # Si no lo es, filtrar capturas guardadas por título/cid
        caps = load_har_captures()
        out = []
        for cid, cap in caps.items():
            title = _capture_title(cap, cid)
            if q.lower() in title.lower() or cid.lower().startswith(q.lower()):
                out.append(_flow_item(cid, title))
        return out

    def get_catalog(self, **kwargs) -> list:
        caps = load_har_captures()
        return [_flow_item(cid, _capture_title(cap, cid)) for cid, cap in caps.items()]

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        cid = str(item.get("cid") or item.get("id") or "").lower()
        caps = load_har_captures()
        cap = caps.get(cid)
        if not cap:
            return {}, []
        title = _capture_title(cap, cid)
        series = {
            "id": cid,
            "slug": cid,
            "title": title,
            "url": f"https://bookwalker.jp/de{cid}/",
        }
        tokens = cap.get("tokens") or {}
        meta = {"Páginas": str(len(tokens))}
        fuente = cap.get("tokens_from")
        if fuente == "config":
            meta["Tokens"] = "derivados (config descifrado — sin HAR)"
        elif tokens:
            meta["Tokens"] = "extraídos del HAR"
        has_session = bool(cap.get("session"))
        meta["Estado"] = "Listo" if has_session else "Sin sesión — pega el cURL /c o de pages"
        if _HAS_UNSCRAMBLE and cap.get("config") and len(cap.get("keys") or []) == 3:
            meta["Imágenes"] = "de-scrambleadas (config+keys)"
        series["meta"] = meta
        chapter = {
            "id": cid,
            "cid": cid,
            "title": title,
            "url": series["url"],
        }
        return series, [chapter]

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        cid = str(
            chapter.get("cid")
            or chapter.get("id")
            or series.get("id")
            or (str(series.get("slug", "")).removeprefix("de"))
        ).lower()
        caps = load_har_captures()
        cap = caps.get(cid)
        if not cap:
            return []
        tokens = cap.get("tokens") or {}
        session = cap.get("session")
        if not session:
            return []

        self._active = cap
        self._seeds: Optional[dict] = None
        self._seeds_by_base: Dict[str, dict] = {}
        if _HAS_UNSCRAMBLE and cap.get("config") and cap.get("keys"):
            ks = cap.get("keys") or []
            if len(ks) == 3:
                try:
                    self._seeds = _build_seeds(cap["config"], ks[0], ks[1], ks[2])
                    for fid, s in (self._seeds or {}).items():
                        self._seeds_by_base[fid.rsplit("/", 1)[-1]] = s
                except Exception:
                    self._seeds = None
        img_base = str(session.get("img_base") or "").rstrip("/") + "/"
        query = str(session.get("query") or "")
        urls: list[str] = []
        for page in sorted(tokens, key=_sort_key):
            tok = str(tokens[page])
            suffix = ".jpegbvCoverImage" if page == "p-cover.xhtml" else ".jpeg"
            u = f"{img_base}{page}/{tok}{suffix}"
            if query:
                u += "?" + query
            urls.append(u)
        return urls

    def _policy_expired(self) -> bool:
        """True si la firma firmada (Policy AWS) de la sesión activa ya expiró.
        Se usa para abortar limpio en vez de reintentar 5×167 imágenes."""
        cap = self._active or {}
        sess = cap.get("session") or {}
        policy = quota = ""
        for k, v in parse_qsl(str(sess.get("query") or "")):
            if k == "Policy":
                policy = v
            elif k == "Key-Pair-Id":
                quota = "key=" + v
        if not policy:
            return False
        try:
            dec = base64.b64decode(policy + "===").decode("utf-8", "replace")
        except Exception:
            return False
        m = re.search(r'"AWS:EpochTime":\s*(\d+)', dec)
        if not m:
            return False
        exp = int(m.group(1))
        now = int(time.time())
        # un margen de 30s menos por si el reloj del cliente se adelanta
        return now >= exp - 30

    _PAGE_SEED_RE = re.compile(r"/((?:p-[a-z0-9_-]+)\.xhtml)/", re.I)

    def _unscramble_bytes(self, url: str, raw: bytes) -> bytes:
        """Si la captura lleva config+keys (y la URL es de una pagina del
        tomo), recompone la imagen scrambleada del visor a su disposicion
        original y recorta al Size declarado. Devuelve el JPEG de-scrambleado
        (calidad 92) con el mismo re-encode que la herramienta standalone;
        si no aplica, devuelve `raw` intacto."""
        seed = None
        if _HAS_UNSCRAMBLE:
            m = self._PAGE_SEED_RE.search(url or "")
            if m:
                seed = self._seeds_by_base.get(m.group(1).lower())
        if not seed or not seed.get("Size"):
            return raw
        try:
            import io as _io
            import PIL.Image as _PILImage
            img = _PILImage.open(_io.BytesIO(raw))
            w, h = img.size
            rgba = img.convert("RGBA")
            if seed.get("Scrambled"):
                out = _unscramble(rgba.tobytes(), w, h, seed)
                dec = _PILImage.frombytes("RGBA", (w, h), bytes(out)).convert("RGB")
            else:
                dec = rgba.convert("RGB")  # portada: llega sin scramble
            size = seed.get("Size") or {}
            sw, sh = size.get("Width"), size.get("Height")
            if sw and sh:
                sw, sh = min(int(sw), w), min(int(sh), h)
                if (sw, sh) != (w, h):
                    dec = dec.crop((0, 0, min(sw, w), min(sh, h)))
            buf = _io.BytesIO()
            dec.save(buf, format="JPEG", quality=92)
            return buf.getvalue()
        except Exception:
            return raw

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        cap = self._active or {}
        session = cap.get("session") or {}
        headers: Dict[str, str] = {}
        if session.get("flow") == "pages":
            headers = dict(session.get("headers") or {})
        if session.get("cookie") and "cookie" not in {k.lower() for k in headers}:
            headers["Cookie"] = session["cookie"]
        headers.setdefault("User-Agent", _DEFAULT_UA)
        headers.setdefault("Referer", session.get("referer") or referer or _DEFAULT_REFERER)
        headers.setdefault("Accept", "image/avif,image/webp,image/jpeg,*/*")

        expired = self._policy_expired()
        for attempt in range(5):
            if attempt:
                if expired:
                    break  # firma vencida: no tiene sentido reintentar
                time.sleep(8 + attempt * 6)  # cooldown tras 403/429 del CDN
            try:
                r = self._sess.get(
                    url, headers=headers, timeout=CFG.get("timeout", (15, 45))
                )
                if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                    return self._unscramble_bytes(url, r.content)
                if r.status_code in (403, 429):
                    if r.status_code == 403:
                        expired = self._policy_expired()
                    continue
                if r.status_code in (404, 410):
                    return None
            except Exception:
                pass
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        return _DEFAULT_REFERER

    def dl_batch(
        self, urls: list[str], referer: str = "", max_workers: int = 8
    ) -> List[Optional[bytes]]:
        """Descarga varias imágenes en paralelo. El CDN de BookWalker corta
        con 403 tras ~50 peticiones secuenciales; en paralelo aguanta las 167."""
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return list(pool.map(lambda u: self.dl_image(u, referer), urls))