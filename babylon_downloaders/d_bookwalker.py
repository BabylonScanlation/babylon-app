"""
d_bookwalker.py — bookwalker.jp downloader (sin menú)
Tienda japonesa de e-books (manga マンガ / ラノベ etc.).

Modelo de datos:
  - En bookwalker cada VOLUMEN es un libro independiente con un cid UUID
    (los URLs de tienda son bookwalker.jp/de{cid}/).
  - El catálogo/búsqueda devuelve tarjetas de SERIE (link /series/{id}/list/)
    y tarjetas de LIBRO (link /de{cid}/) según el tipo de página.
  - Sobre una serie se listan sus volúmenes como capítulos.

Contenido GRATUITO (vista previa / 試し読み):
  El visor de prueba no requiere login. Flujo (verificado empíricamente):
    1. GET viewer-trial.bookwalker.jp/trial-page/c?cid={cid}&BID=0
       → JSON con auth_info (firma CloudFront) + url base + cty (0=novela,1=manga)
    2. base + configuration_pack.json (+firma) → metadatos de páginas
       ("configuration.contents[]" + entrada por archivo con FileLinkInfo.PageCount)
    3. cada página es base + {file}/{n}.jpeg (+firma)
  Las firmas expiran ~1h y están atadas a la IP → se regeneran en cada petición.

Tomos COMPRADOS (requiere cuenta del usuario en otro navegador):
  El visor real de `member` ata la sesión al navegador logueado y cada /c es
  de UN SOLO uso por sesión (SESSION), por lo que la vía práctica es CAPTURAR
  la petición `/c` del visor del usuario (Copy as cURL) y reproducirla:
    1. El usuario abre el visor del tomo (viewer.html?cid=...) logueado.
    2. Copia la petición browserWebApi/c (con su SESSION/u1/BID y cookies).
    3. import_bookwalker_curl(curl) guarda la captura por cid en
       bookwalker_member.json junto a este módulo.
    4. _member_resolve() llama /c una única vez (no es renovable) y cachea la
       firma CloudFront (~1h). Si caduca, se pide una captura nueva.
  El host de archivos member es bw-bv-epubs.bookwalker.jp (mismo esquema de
  páginas {file}/{n}.jpeg + firma que el trial, salvo el tamaño del pack).
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import time
from typing import Optional
from urllib.parse import parse_qsl, urlencode

import requests
from common import CFG, BaseDownloader, extract_series_extras

BASE_URL = "https://bookwalker.jp"
TRIAL_URL = "https://viewer-trial.bookwalker.jp/trial-page/c"
MEMBER_URL = "https://viewer.bookwalker.jp/browserWebApi/c"
TRIAL_REFERER = "https://viewer-trial.bookwalker.jp/"
# Host de los archivos firmados (nunca con firma: mero referer)
FILE_REFERER = "https://viewer-epubs-trial.bookwalker.jp/"
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookwalker_cookies.txt")
_BROWSER_ID_SUFFIX = "NFBR"
# Campo visible en la configuración del sitio (se persiste en COOKIE_FILE)
SITE_COOKIES = ""

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DE_RE = re.compile(r"/de([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/")
_SERIES_RE = re.compile(r"/series/(\d+)/list/")
_TOTAL_RE = re.compile(r"(\d+)～(\d+)件目/全(\d+)件")
_FREE_SIGN = 300  # renovar firma si queda menos de 5 min de validez


def _make_session() -> requests.Session:
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(HEADERS)
    return s


def _policy_expiry(policy: str) -> Optional[float]:
    """Decodifica el base64 url-safe de `Policy` y extrae AWS:EpochTime."""
    try:
        b64 = policy.replace("-", "+").replace("_", "/")
        b64 += "=" * (-len(b64) % 4)
        data = json.loads(base64.b64decode(b64))
        for st in data.get("Statement") or []:
            less = (st.get("Condition") or {}).get("DateLessThan") or {}
            if "AWS:EpochTime" in less:
                return float(less["AWS:EpochTime"])
    except Exception:
        pass
    return None


def _clean_series_title(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^『|』$", "", t)
    t = re.sub(r"(の\.?電子書籍一覧|の電子書籍(一覧)?|無料試し読みならBOOK.?WALKER).*$", "", t)
    return t.strip(" 　｜|")


def load_site_cookies() -> str:
    """Lee las cookies de sesión guardadas ('k=v; k2=v2') o ''."""
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_site_cookies(value: str) -> None:
    """Persiste/limpia las cookies de sesión en el archivo del módulo."""
    v = (value or "").strip()
    try:
        if v:
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                f.write(v)
        else:
            os.remove(COOKIE_FILE)
    except OSError:
        pass


# ── capturas del visor member (tomo comprado) ─────────────────────────────

MEMBER_SESSION_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bookwalker_member.json"
)


def load_member_captures() -> dict:
    """Lectura de las capturas (/c por cid) guardadas del visor member."""
    try:
        with open(MEMBER_SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_member_captures(captures: dict) -> None:
    """Persiste las capturas (/c por cid) del visor member."""
    try:
        with open(MEMBER_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(captures, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def clear_member_captures() -> None:
    """Elimina todas las capturas member guardadas."""
    try:
        os.remove(MEMBER_SESSION_FILE)
    except OSError:
        pass


def import_bookwalker_curl(curl: str) -> Optional[str]:
    """Importa la petición `/c` del visor member pegada como cURL del navegador.

    Espera el texto de 'Copy as cURL' de la petición
    http.../browserWebApi/c?cid=...&u1=...&BID=...&cr=... (incluye el header
    `-b "cookie=..."`). Guarda la captura por {cid} y devuelve el cid, o None
    si el texto no se reconoce.
    """
    text = (curl or "").replace("^", "")
    m = re.search(r"https://viewer\.bookwalker\.jp/browserWebApi/c\?[^\s'\"]*", text)
    if not m:
        return None
    qs = {}
    for k, v in parse_qsl(m.group(0).split("?", 1)[1]):
        qs[k] = v
    cid = str(qs.get("cid") or "")
    u1 = str(qs.get("u1") or "")
    bid = str(qs.get("BID") or "")
    if not (cid and u1 and bid):
        return None

    cookies = ""
    mc = re.search(r"-b\s+(\".*?\"|'.*?')", text, re.S)
    if mc:
        cookies = mc.group(1).strip("\"'").strip()

    caps = load_member_captures()
    caps[cid] = {
        "cid": cid,
        "u1": u1,
        "bid": bid,
        "cr": str(qs.get("cr") or ""),
        "cookies": cookies,
        "captured_at": time.time(),
        "auth": None,
        "base": "",
        "cty": 1,
        "expires_at": 0.0,
    }
    save_member_captures(caps)
    return cid


class DownloaderBookwalker(BaseDownloader):
    NAME = "BOOKWALKER   (bookwalker.jp)"
    NEEDS_LOGIN = False
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()
        self._logged = False
        # Firma por cid: (expiry_ts, resolve_dict) — trial y member por separado
        self._res_cache: dict[str, tuple[Optional[float], dict]] = {}
        self._mem_cache: dict[str, tuple[Optional[float], dict]] = {}
        self._ensure_cookies()

    def _make_browser_id(self) -> str:
        now = int(time.time() * 1000)
        rnd = "%08d" % random.randint(0, 99999999)
        return f"{now}{rnd}{_BROWSER_ID_SUFFIX}"

    def _inject_cookies(self, cookies) -> None:
        if isinstance(cookies, str):
            for part in cookies.split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                k, _, v = part.partition("=")
                k = k.strip()
                if k:
                    self._sess.cookies.set(
                        k, v.strip(), domain=".bookwalker.jp", path="/"
                    )
        elif isinstance(cookies, dict) and cookies:
            self._sess.cookies.update(cookies)
        self._sess.headers.update({"Referer": "https://member.bookwalker.jp/"})
        self._logged = True

    def _ensure_cookies(self) -> bool:
        """Reintenta el login si ya hay cookies persistidas en el archivo."""
        if self._logged:
            return True
        cookies = load_site_cookies()
        if not cookies:
            return False
        try:
            self._inject_cookies(cookies)
        except Exception:
            self._logged = False
            return False
        return True

    # ── helpers de red ───────────────────────────────────────────────────

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[requests.Response]:
        last = None
        for attempt in range(3):
            try:
                r = self._sess.get(url, params=params, timeout=CFG.get("timeout", (15, 45)))
                if r.status_code == 200:
                    return r
                if r.status_code in (403, 404, 410):
                    return None
                last = r
            except Exception:
                pass
            time.sleep(CFG.get("retry_delay", 2.0))
        return last

    # ── visor de prueba (trial) ──────────────────────────────────────────

    def _trial_resolve(self, cid: str) -> Optional[dict]:
        """Firma CloudFront para el trial del cid. Renueva si está por vencer."""
        if cid in self._res_cache:
            expiry, res = self._res_cache[cid]
            if expiry is not None and expiry > time.time() + _FREE_SIGN:
                return res

        r = self._get(TRIAL_URL, {"cid": cid, "BID": "0"})
        if r is None:
            return None
        try:
            d = r.json()
        except Exception:
            return None
        if d.get("status") != "200" or not d.get("url"):
            return None

        base = str(d["url"]).rstrip("/") + "/"
        if int(d.get("cty") or 0) == 0:
            base += "normal_default/"
        info = d.get("auth_info") or {}
        auth = {
            k: info.get(k)
            for k in ("pfCd", "Policy", "Signature", "Key-Pair-Id")
        }
        expiry = _policy_expiry(str(auth.get("Policy") or ""))
        res = {
            "base": base,
            "auth": auth,
            "cty": int(d.get("cty") or 0),
            "cti": str(d.get("cti") or ""),
        }
        self._res_cache[cid] = (expiry, res)
        return res

    def _member_session_cookies(self, cap: dict) -> None:
        """Aplica cookies de cuenta + cookie SESSION desde una captura del visor."""
        if cap.get("cookies"):
            self._inject_cookies(cap["cookies"])
        sid = str(cap.get("u1") or "")
        if sid:
            self._sess.cookies.set(
                "SESSION", sid, domain="viewer.bookwalker.jp", path="/browserWebApi/"
            )
        # referer idéntico al del visor (lo espera el endpoint /c)
        cid = str(cap.get("cid") or "")
        self._sess.headers.update(
            {"Referer": f"https://viewer.bookwalker.jp/03/30/viewer.html?cid={cid}&cty=1"}
        )
        self._logged = True

    @staticmethod
    def _res_from_capture(cap: dict) -> Optional[dict]:
        """Convierte una captura consumida (auth/base/cty) en un res usable."""
        auth = cap.get("auth") or {}
        base = str(cap.get("base") or "").rstrip("/")
        if not auth or not base:
            return None
        base += "/"
        if int(cap.get("cty") or 0) == 0 and not base.endswith("normal_default/"):
            base += "normal_default/"
        return {
            "base": base,
            "auth": {k: auth[k] for k in auth if auth[k] is not None and str(auth[k]) != ""},
            "cty": int(cap.get("cty") or 0),
            "cti": "",
            "member": True,
        }

    def _member_resolve(self, cid: str) -> Optional[dict]:
        """Sesión del visor de `member` para un tomo COMPRADO (captura del usuario).

        Usa la captura `/c` importada con import_bookwalker_curl(). El endpoint
        es de UN SOLO uso por SESSION: si ya hay una firma válida cacheada no se
        re-consume; si caducó (~1h) se necesita una captura nueva.
        """
        if cid in self._mem_cache:
            expiry, res = self._mem_cache[cid]
            if expiry is not None and expiry > time.time() + _FREE_SIGN:
                return res

        caps = load_member_captures()
        cap = caps.get(cid)
        if not cap:
            return None
        self._member_session_cookies(cap)

        exp = float(cap.get("expires_at") or 0)
        res = None
        if exp > time.time() + _FREE_SIGN and cap.get("auth"):
            # Firma ya consumida y válida → no re-llamar /c (es one-shot)
            res = self._res_from_capture(cap)
        else:
            r = self._get(
                MEMBER_URL,
                {
                    "cid": cid,
                    "u1": str(cap.get("u1") or ""),
                    "BID": str(cap.get("bid") or ""),
                    "cr": str(cap.get("cr") or "")
                    or str(int(time.time() % 10**19)),
                },
            )
            if r is not None:
                d = None
                try:
                    d = r.json()
                except Exception:
                    d = None
                if isinstance(d, dict) and str(d.get("status")) == "200" and d.get("url"):
                    info = d.get("auth_info") or {}
                    auth = {
                        k: info.get(k)
                        for k in ("hti", "cfg", "Policy", "Signature", "Key-Pair-Id")
                        if info.get(k)
                    }
                    cap["auth"] = auth
                    cap["base"] = str(d["url"]).rstrip("/")
                    cap["cty"] = int(d.get("cty") or 0)
                    cap["expires_at"] = float(
                        _policy_expiry(str(info.get("Policy") or "")) or 0
                    )
                    caps[cid] = cap
                    save_member_captures(caps)
                    res = self._res_from_capture(cap)

        if res is None and exp > time.time() + _FREE_SIGN:
            res = self._res_from_capture(cap)
        if res is None:
            return None
        self._mem_cache[cid] = (exp, res)
        return res

    def _configuration(self, res: dict) -> Optional[dict]:
        r = self._get(res["base"] + "configuration_pack.json", params=res["auth"])
        if r is None:
            return None
        try:
            return r.json()
        except Exception:
            return None

    def _build_page_urls(self, res: dict, cfg: dict) -> list[str]:
        base = res["base"]
        query = urlencode({k: str(v) for k, v in (res["auth"] or {}).items() if v})
        contents = ((cfg.get("configuration") or {}).get("contents")) or []
        urls: list[str] = []
        for chapter in contents:
            key = (chapter.get("file") or "").strip()
            if not key:
                continue
            entry = cfg.get(key) or cfg.get(key.lstrip("./")) or {}
            n = int(((entry.get("FileLinkInfo") or {}).get("PageCount")) or 0)
            if n <= 0:
                continue
            for p in range(n):
                urls.append(f"{base}{key}/{p}.jpeg?{query}")
        return urls

    # ── parseo de tarjetas (catálogo / búsqueda / serie) ────────────────

    @staticmethod
    def _img_title(img) -> str:
        for attr in ("title", "alt"):
            v = (img.get(attr) or "").strip()
            if v:
                return v
        return ""

    @staticmethod
    def _tile_item(tile) -> Optional[dict]:
        """Convierte una tarjeta `li.m-tile` en item de libro o de serie."""
        thumb = tile.select_one("a.m-thumb__image") or tile.select_one("a.m-book-item__title")
        if thumb is None:
            return None
        href = str(thumb.get("href") or "")
        img = thumb.find("img") if thumb else None
        title = (thumb.get("title") or "").strip() or (DownloaderBookwalker._img_title(img) if img is not None else "")
        if not title:
            t2 = tile.select_one("a.m-book-item__title[title]")
            if t2 is not None:
                title = (t2.get("title") or "").strip()
        cover = ""
        if img is not None:
            cover = (
                img.get("data-original") or img.get("data-src")
                or img.get("src") or ""
            ).strip()
            if cover.startswith("//"):
                cover = "https:" + cover

        m_ser = _SERIES_RE.search(href)
        if m_ser:
            item = {
                "kind": "series",
                "id": str(int(m_ser.group(1))),
                "slug": f"series/{int(m_ser.group(1))}",
                "title": title,
                "url": f"{BASE_URL}/series/{int(m_ser.group(1))}/list/",
            }
            if cover:
                item["cover"] = cover
            # cids de volúmenes destacados (1巻 / 最新刊)
            cids = [
                m.group(1)
                for a in tile.select("a[href*='/de']")
                for m in [_DE_RE.search(str(a.get("href") or ""))]
                if m
            ]
            if cids:
                item["vol_cids"] = list(dict.fromkeys(cids))
            return item

        m_de = _DE_RE.search(href)
        if m_de:
            cid = m_de.group(1)
            trial_btn = tile.select_one("a.a-icon-btn--trial")
            item = {
                "kind": "book",
                "id": cid,
                "slug": "de" + cid,
                "title": title,
                "url": f"{BASE_URL}/de{cid}/",
                "trial": trial_btn is not None,
            }
            if cover:
                item["cover"] = cover
            return item
        return None

    def _parse_items(self, soup) -> list[dict]:
        items: list[dict] = []
        seen: set[str] = set()
        for tile in soup.select("li.m-tile"):
            it = self._tile_item(tile)
            if it and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
        return items

    def _fetch_search_page(
        self, word: str = "", order: str = "rank", free: str = "", page: int = 1
    ) -> tuple[list[dict], bool, Optional[int]]:
        params: dict = {"order": order, "page": page}
        if word:
            params["word"] = word
        if free:
            params["qpri"] = free
        r = self._get(f"{BASE_URL}/search/", params)
        if r is None:
            return [], False, None
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return [], False, None
        try:
            soup = BeautifulSoup(r.text, "lxml")
        except Exception:
            soup = BeautifulSoup(r.text, "html.parser")
        items = self._parse_items(soup)
        total = None
        m = _TOTAL_RE.search(r.text)
        if m:
            total = int(m.group(3))
        has_more = bool(
            r.text.find(f"page={page + 1}") != -1
            or (total is not None and page * 60 < total)
        )
        return items, has_more, total

    # ── catálogo paginado (buffer por filtro) ────────────────────────────

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list[dict], bool]:
        word = str(kwargs.get("word", "")).strip()
        order = str(kwargs.get("order", "rank")).strip() or "rank"
        free = str(kwargs.get("free", "")).strip()
        key = repr(["bw", word, order, free])

        if getattr(self, "_cat_buf_key", None) != key:
            self._cat_buf = []
            self._cat_buf_key = key
            self._bw_site = 0
            self._bw_finished = False

        need = page * page_size
        while len(self._cat_buf) < need and not self._bw_finished:
            self._bw_site += 1
            items, has_more, _total = self._fetch_search_page(
                word=word, order=order, free=free, page=self._bw_site
            )
            if not items:
                self._bw_finished = True
                break
            self._cat_buf.extend(items)
            if not has_more:
                self._bw_finished = True

        start = (page - 1) * page_size
        end = start + page_size
        chunk = self._cat_buf[start:end]
        has_more = (not self._bw_finished) or len(self._cat_buf) > end
        return chunk, has_more

    # ── búsqueda ─────────────────────────────────────────────────────────

    def search(self, query: str) -> list[dict]:
        q = (query or "").strip()
        if not q:
            return []

        # Acepta URL / UUID / id de serie directamente
        m_de = _DE_RE.search(q) or _UUID_RE.search(q)
        if m_de:
            cid = m_de.group(0) if _UUID_RE.fullmatch(m_de.group(0)) else m_de.group(1)
            it = {
                "kind": "book",
                "id": cid,
                "slug": "de" + cid,
                "title": q,
                "url": f"{BASE_URL}/de{cid}/",
                "trial": True,
            }
            return self._enrich_book_item(it)
        m_ser = _SERIES_RE.search(q)
        if m_ser:
            it = {
                "kind": "series",
                "id": str(int(m_ser.group(1))),
                "slug": f"series/{int(m_ser.group(1))}",
                "title": q,
                "url": f"{BASE_URL}/series/{int(m_ser.group(1))}/list/",
            }
            return [it]

        items, _has_more, _total = self._fetch_search_page(word=q, order="rank", page=1)
        return items

    def _enrich_book_item(self, it: dict) -> list[dict]:
        """Rellena el título real y la portada del/los item(s) del libro."""
        try:
            r = self._get(it["url"])
            if r is None:
                return [it]
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(r.text, "lxml")
            h1 = soup.select_one("h1.t-c-product-main-data__title")
            if h1 is not None and h1.get_text(strip=True):
                it["title"] = h1.get_text(strip=True)
            if not it.get("cover"):
                og = soup.find("meta", attrs={"property": "og:image"})
                if og and og.get("content"):
                    it["cover"] = og["content"]
        except Exception:
            pass
        return [it]

    # ── ficha de serie / libro ───────────────────────────────────────────

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        slug = str(item.get("slug") or "")

        # Libro directo (de{cid})
        if slug.startswith("de") and len(slug) > 2:
            return self._book_series(slug[2:], item)

        iid = str(item.get("id", ""))
        m = _UUID_RE.fullmatch(iid) or _UUID_RE.search(slug)
        if m:
            cid = m.group(0)
            return self._book_series(cid, item)

        if slug.startswith("series/") or iid.isdigit():
            sid = slug.split("/")[1] if slug.startswith("series/") else iid
            return self._series_from_id(sid, item)

        # Fallback: intenta extraer cid/series de la URL
        url = str(item.get("url") or "")
        m_de = _DE_RE.search(url)
        if m_de:
            return self._book_series(m_de.group(1), item)
        m_ser = _SERIES_RE.search(url)
        if m_ser:
            return self._series_from_id(m_ser.group(1), item)
        return {}, []

    def _book_series(self, cid: str, item: dict) -> tuple[dict, list[dict]]:
        title = str(item.get("title") or "").strip()
        cover = str(item.get("cover") or "")
        meta: dict[str, str] = {}
        extras: dict = {}

        try:
            r = self._get(f"{BASE_URL}/de{cid}/")
            if r is not None:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(r.text, "lxml")
                h1 = soup.select_one("h1.t-c-product-main-data__title")
                if h1 is not None and h1.get_text(strip=True):
                    title = h1.get_text(strip=True)
                auth = soup.select_one("dl.t-c-product-main-data__authors")
                if auth is not None and auth.get_text(strip=True):
                    meta["Autor"] = " ".join(auth.get_text(" ", strip=True).split())
                extras = extract_series_extras(soup, BASE_URL)
                if not cover and extras.get("cover"):
                    cover = extras["cover"]
        except Exception:
            pass

        trial_res = self._trial_resolve(cid)
        if trial_res is None and self._ensure_cookies():
            # Sin muestra gratuita: si el tomo es del usuario, se sirve completo
            trial_res = self._member_resolve(cid)
        series = {
            "id": cid,
            "slug": "de" + cid,
            "title": title or f"bookwalker {cid[:8]}",
            "url": f"{BASE_URL}/de{cid}/",
        }
        if cover:
            series["cover"] = cover
        if extras.get("tags"):
            series["tags"] = extras["tags"]
        if extras.get("meta"):
            meta.update(extras["meta"])
        if meta:
            series["meta"] = meta
        if trial_res is None:
            # No hay muestra gratuita disponible (requiere cuenta para este tomo)
            series["meta"] = {**(series.get("meta") or {}), "Estado": "Sin muestra — requiere cuenta"}
            return series, []

        chapters = [
            {
                "id": cid,
                "cid": cid,
                "title": series["title"],
                "url": f"{BASE_URL}/de{cid}/",
                "trial": True,
            }
        ]
        return series, chapters

    def _series_from_id(self, sid: str, item: dict) -> tuple[dict, list[dict]]:
        cover = str(item.get("cover") or "")
        extras: dict = {}
        chapters: list[dict] = []
        authors: set[str] = set()
        tags: set[str] = set()
        first_vol_title = ""

        try:
            r = self._get(f"{BASE_URL}/series/{sid}/list/")
            if r is None:
                return {}, []
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(r.text, "lxml")
            h1 = soup.select_one(".o-contents-section__title")
            item_title = str(item.get("title") or "").strip()
            if item_title:
                series_title = item_title
            elif h1 is not None:
                series_title = _clean_series_title(h1.get_text(strip=True))
            else:
                series_title = ""
            extras = extract_series_extras(soup, BASE_URL)
            if not cover and extras.get("cover"):
                cover = extras["cover"]

            for tile in soup.select("li.m-tile"):
                it = self._tile_item(tile)
                if it is None or it["kind"] != "book":
                    continue
                cid = it["id"]
                vol_title = it.get("title") or first_vol_title
                if not first_vol_title:
                    first_vol_title = vol_title
                chapters.append(
                    {
                        "id": cid,
                        "cid": cid,
                        "title": vol_title,
                        "url": it.get("url"),
                        "trial": bool(it.get("trial")),
                    }
                )
                if not cover and it.get("cover"):
                    cover = it["cover"]
                author_el = tile.select_one(".m-book-item__author")
                if author_el is not None:
                    links = [a.get_text(" ", strip=True) for a in author_el.select("a")]
                    if links:
                        authors.update(l for l in links if l)
                    else:
                        txt = re.sub(
                            r"^(著|著者):?\s*", "",
                            author_el.get_text(" ", strip=True),
                        )
                        if txt:
                            authors.add(txt)
                for sp in tile.select(".m-book-item__tag-box span"):
                    t = sp.get_text(strip=True)
                    if t:
                        tags.add(t)
        except Exception:
            return {}, []

        if not chapters:
            return {}, []

        series = {
            "id": sid,
            "slug": f"series/{sid}",
            "title": str(item.get("title") or "").strip() or first_vol_title,
            "url": f"{BASE_URL}/series/{sid}/list/",
        }
        if cover:
            series["cover"] = cover
        meta: dict[str, str] = {}
        if authors:
            meta["Autor"] = ", ".join(sorted(authors))
        if tags:
            series["tags"] = sorted(tags)
        if extras.get("meta"):
            meta.update(extras["meta"])
        if meta:
            series["meta"] = meta
        return series, chapters

    # ── descarga ─────────────────────────────────────────────────────────

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        cid = (
            chapter.get("cid")
            or chapter.get("id")
            or series.get("id")
            or (str(series.get("slug", "")).removeprefix("de"))
        )
        if not cid:
            return []
        if not chapter.get("trial", True) and not self._ensure_cookies():
            # Tomo sin muestra gratuita y sin cuenta vinculada
            return []

        res = None
        if self._logged:
            # Con cuenta, prefiere el tomo COMPLETO de `member`
            res = self._member_resolve(cid)
        if res is None:
            res = self._trial_resolve(cid)
        if res is None:
            return []
        cfg = self._configuration(res)
        if cfg is None:
            return []
        return self._build_page_urls(res, cfg)

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        for _ in range(3):
            try:
                r = self._sess.get(
                    url,
                    headers={"Referer": referer or TRIAL_REFERER},
                    timeout=CFG.get("timeout", (15, 45)),
                )
                if r.status_code == 200 and r.content:
                    return r.content
                if r.status_code in (403, 404, 410):
                    return None
            except Exception:
                pass
            time.sleep(CFG.get("retry_delay", 2.0))
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        return FILE_REFERER

    def get_image_count(self, chapter: dict, series: dict) -> Optional[int]:
        try:
            return len(self.get_chapter_images(chapter, series))
        except Exception:
            return None

    # ── cuenta (etapa 2: tomos comprados) ────────────────────────────────

    def login(self, **kwargs) -> bool:
        """Inyecta y persiste cookies de sesión de bookwalker.jp (p.ej. `bwmember=…`).

        cookies: str 'k=v; k2=v2' o dict, obtenidas del navegador logueado en
        https://member.bookwalker.jp/ (Google-linked no usa credenciales).
        Se guardan en bookwalker_cookies.txt; con cookies vacías se limpian.
        """
        cookies = kwargs.get("cookies")
        clear = kwargs.get("clear", False)
        if clear or (cookies is not None and not str(cookies).strip()):
            self._logged = False
            save_site_cookies("")
            return True
        if isinstance(cookies, str) and cookies.strip():
            self._inject_cookies(cookies)
            save_site_cookies(cookies)
            return True
        if isinstance(cookies, dict) and cookies:
            self._inject_cookies(cookies)
            if cookies:
                save_site_cookies("; ".join(f"{k}={v}" for k, v in cookies.items()))
            return True
        return False