"""
d_mangafox.py — fanfox.net downloader (sin menú)

FIXES sobre el original:
  - word token ya no está en el HTML → se obtiene del cookie 'word' (servidor lo setea al visitar la página)
  - _api_images filtra loading.gif/placeholders
  - La aceptación de imágenes ya no usa len==n_pages (aceptaba placeholders), usa _is_real_image
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from bs4 import BeautifulSoup
from common import CFG, BaseDownloader

BASE_URL = "https://fanfox.net"
TIMEOUT = (15, 45)
RETRY = 2.0
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL + "/",
    "Accept-Language": "en-US,en;q=0.9",
}

_RE_CHAPTERID = re.compile(r'chapterid\s*=\s*["\']?(\d+)["\']?', re.I)
_RE_IMAGECOUNT = re.compile(r'imagecount\s*=\s*["\']?(\d+)["\']?', re.I)
_RE_WORD = re.compile(
    r'["\']word["\']\s*:\s*["\']([^"\']{3,})["\']'
    r'|var\s+word\s*=\s*["\']([^"\']{3,})["\']',
    re.I,
)
_RE_IMGURL = re.compile(
    r'(https?://(?:fmcdn|img\.mfcdn)[^"\'<>\s]+'
    r'\.(?:jpe?g|png|webp|gif)(?:\?[^"\'<>\s]*)?)',
    re.I,
)
_CHAP_URL_RE = re.compile(r"/manga/[^/]+/(?:v([^/]+)/)?c([^/]+)/\d+\.html")

# Placeholders que fanfox sirve cuando no hay token válido — nunca son imágenes del capítulo
_PLACEHOLDER_PATTERNS = (
    "loading.gif",
    "loading.png",
    "/images/loading",
    "static.fanfox.net",
    "mangafox/images",
    "sprite.png",
    "data:image",
)


def _is_placeholder(url: str) -> bool:
    u = url.lower()
    return any(p in u for p in _PLACEHOLDER_PATTERNS)


def _is_real_image(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    if _is_placeholder(url):
        return False
    if "cover" in url.lower():
        return False
    return any(cdn in url for cdn in ("fmcdn", "mfcdn", "img.mfcdn"))


# ── Credenciales opcionales — completar para habilitar descarga de capítulos ──
FANFOX_USERNAME = "LucasGoldstein"
FANFOX_EMAIL = "hcaulfield@muvilo.net"
FANFOX_PASSWORD = "celavii24"


def _login(sess: requests.Session, email: str, password: str) -> bool:
    """
    Inicia sesión en fanfox.net.
    Intenta con username y email, y con las variantes de URL conocidas.
    """
    if not email or not password:
        return False
    import logging

    log = logging.getLogger(__name__)
    try:
        login_url = f"{BASE_URL}/login/?from=/"
        r = sess.get(login_url, timeout=10)
        if r.status_code != 200:
            log.warning(f"[mangafox] GET {login_url} → {r.status_code}")
            return False
        html = r.text

        # Extraer token CSRF — fanfox usa varios nombres posibles
        import re as _re

        csrf = None
        for pat in [
            r'name="?(?:_token|csrf_token|authenticity_token)"?\s+[^>]*value="([^"]+)"',
            r'value="([^"]+)"\s+name="?(?:_token|csrf_token)"?',
            r'"_token"\s*:\s*"([^"]+)"',
        ]:
            m = _re.search(pat, html, _re.I)
            if m:
                csrf = m.group(1)
                break

        # username es el campo que usa fanfox (no email)
        # el valor puede ser email o username — intentamos ambos
        username = email.split("@")[0] if "@" in email else email

        for login_field, login_val in [
            ("username", username),
            ("email", email),
            ("username", email),
        ]:
            data = {
                login_field: login_val,
                "password": password,
                "remember": "1",
            }
            if csrf:
                data["_token"] = csrf

            r2 = sess.post(
                login_url,
                data=data,
                headers={"Referer": login_url},
                timeout=15,
                allow_redirects=True,
            )
            log.debug(
                f"[mangafox] POST login ({login_field}={login_val!r}) → {r2.status_code} url={r2.url}"
            )

            # Éxito: redirigió fuera del login, o tiene cookie de sesión
            if "login" not in r2.url or any(
                k in sess.cookies
                for k in ("isLogin", "user_id", "uid", "session", "mangafox")
            ):
                log.info(f"[mangafox] login OK con campo {login_field!r}")
                return True

        log.warning(f"[mangafox] login falló — cookies: {list(sess.cookies.keys())}")
        return False
    except Exception as e:
        import logging as _l

        _l.getLogger(__name__).warning(f"[mangafox] login excepción: {e}")
        return False


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(BASE_URL, timeout=10)
    except Exception:
        pass
    # Login automático — intenta username primero, luego email
    if FANFOX_PASSWORD:
        ok = _login(s, FANFOX_USERNAME, FANFOX_PASSWORD) if FANFOX_USERNAME else False
        if not ok:
            ok = _login(s, FANFOX_EMAIL, FANFOX_PASSWORD)
        import logging

        logging.getLogger(__name__).info(
            f"[mangafox] login {'OK' if ok else 'FALLÓ'} — cookies: {list(s.cookies.keys())}"
        )
    return s


def _get_word_from_chapter(
    sess: requests.Session, chap_url: str, chapter_id: str
) -> str:
    """
    Intenta obtener el word token real haciendo el mismo request que chapter_h.js.
    fanfox genera el token via: GET /roll_manga/apiv1/manga/{slug}/chapters/{id}/words/
    """
    import re as _re

    # Extraer slug de la URL del capítulo
    m = _re.search(r"/manga/([^/]+)/", chap_url)
    if not m:
        return ""
    slug = m.group(1)
    for endpoint in [
        f"{BASE_URL}/roll_manga/apiv1/manga/{slug}/chapters/{chapter_id}/words/",
        f"{BASE_URL}/roll_manga/apiv1/manga/{slug}/chapters/{chapter_id}/token/",
    ]:
        try:
            r = sess.get(endpoint, timeout=10, headers={"Referer": chap_url})
            if r.status_code == 200:
                data = r.json()
                word = (
                    data.get("word")
                    or data.get("token")
                    or data.get("data", {}).get("word")
                    if isinstance(data.get("data"), dict)
                    else None
                )
                if word and isinstance(word, str) and len(word) > 5:
                    return word
        except Exception:
            pass
    return sess.cookies.get("word", "")


def _fetch_html(
    sess: requests.Session, url: str, referer: Optional[str] = None
) -> Optional[str]:
    hdrs = {"Referer": referer} if referer else {}
    for attempt in range(3):
        try:
            r = sess.get(url, timeout=TIMEOUT, headers=hdrs)
            if r.status_code == 200:
                return r.text
            if r.status_code in (403, 404):
                return None
        except Exception:
            time.sleep(RETRY * (attempt + 1))
    return None


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ── ORIGINAL _parse_manga_list — NO TOCAR ────────────────────────────────────
def _parse_manga_list(html: str) -> list[dict]:
    soup = _soup(html)
    results: list[dict] = []
    seen: set = set()
    _ML = re.compile(r"/manga/([a-z0-9_\-]+)/?$")

    ITEM_SELS = [
        "ul.manga-list-4-list li",
        "ul.manga-list-4 li",
        "ul.manga-list-2 li",
        "ul.manga-list li",
        ".manga-list li",
    ]
    items = []
    for sel in ITEM_SELS:
        items = soup.select(sel)
        if items:
            break

    for item in items:
        a = item.select_one(
            "p.manga-list-4-item-title a, p.title a, h3 a, .title a"
        ) or item.find("a", href=re.compile(r"/manga/[^/]+/?$"))
        if not a:
            continue
        href = a.get("href", "")
        title = a.get_text(strip=True)
        m = re.search(r"/manga/([^/?#]+)/?", href)
        if not m or not title:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        results.append({"id": slug, "slug": slug, "title": title})

    if not results:
        for a in soup.find_all("a", href=_ML):
            href = a.get("href", "")
            title = (a.get("title") or a.get_text(strip=True)).strip()
            m = _ML.search(href)
            if not m or not title or len(title) < 2:
                continue
            slug = m.group(1)
            if slug in seen:
                continue
            seen.add(slug)
            results.append({"id": slug, "slug": slug, "title": title})
    return results


def _parse_series(sess: requests.Session, slug: str) -> Optional[dict]:
    html = _fetch_html(sess, f"{BASE_URL}/manga/{slug}/")
    if not html:
        return None
    soup = _soup(html)
    title = ""
    for sel in ["span.detail-info-right-title-font", "h1.title", "h1"]:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            title = node.get_text(strip=True)
            break
    title = title or slug.replace("_", " ").title()

    chapters: list[dict] = []
    seen_c: set = set()

    def _harvest(h: str) -> None:
        for a in _soup(h).find_all("a", href=_CHAP_URL_RE):
            href = a.get("href", "")
            m = _CHAP_URL_RE.search(href)
            if not m:
                continue
            chap = m.group(2)
            if chap in seen_c:
                continue
            seen_c.add(chap)
            full_url = BASE_URL + href if href.startswith("/") else href
            label = a.get_text(strip=True) or f"Ch.{chap}"
            if label.lower() in ("read now", "read", "start", ""):
                label = f"Ch.{chap}"
            chapters.append({"id": chap, "title": label, "chap": chap, "url": full_url})

    _harvest(html)

    page = 2
    while page <= 50:
        pg_html = None
        for url in [
            f"{BASE_URL}/manga/{slug}/chapter-list-{page}.html",
            f"{BASE_URL}/manga/{slug}/?page={page}",
            f"{BASE_URL}/manga/{slug}?page={page}",
        ]:
            pg_html = _fetch_html(sess, url)
            if pg_html:
                break
        if not pg_html:
            break
        prev_count = len(chapters)
        _harvest(pg_html)
        if len(chapters) == prev_count:
            break
        page += 1
        time.sleep(0.3)

    def _key(ch):
        try:
            return float(ch["chap"])
        except:
            return 0.0

    chapters.sort(key=_key, reverse=True)
    return {"id": slug, "slug": slug, "title": title, "chapters": chapters}


def _js_vars(html: str) -> tuple:
    soup = _soup(html)
    js_text = "\n".join(s.get_text() for s in soup.find_all("script"))
    combined = js_text + "\n" + html
    m_id = _RE_CHAPTERID.search(combined)
    m_cnt = _RE_IMAGECOUNT.search(combined)
    m_w = _RE_WORD.search(combined)
    chid = m_id.group(1) if m_id else None
    cnt = int(m_cnt.group(1)) if m_cnt else 0
    word = (m_w.group(1) or m_w.group(2)) if m_w else None

    # FIX: fanfox movió el word token al CDN token de og:image
    # <meta name="og:image" content="...?token=WORD&ttl=...">
    if not word:
        og_img = soup.find("meta", attrs={"name": "og:image"}) or soup.find(
            "meta", property="og:image"
        )
        if og_img:
            content = og_img.get("content", "")
            m_tok = re.search(r"[?&]token=([a-f0-9]{20,})", content)
            if m_tok:
                word = m_tok.group(1)

    return chid, cnt, word


def _is_cover_response(images: list[str]) -> bool:
    """
    Detecta cuando la API devuelve la portada repetida N veces (sin token válido).
    La portada tiene 'cover' en la URL o todas las URLs son idénticas.
    """
    if not images:
        return False
    # Todas iguales = respuesta inválida
    if len(set(images)) == 1:
        return True
    # Contiene "cover" en la URL
    if any("cover" in u.lower() for u in images):
        return True
    return False


def _api_images(
    sess: requests.Session,
    slug: str,
    chapter_id: str,
    n_pages: int,
    word: Optional[str],
    chap_url: str = "",
) -> list[str]:
    """
    FIX: obtiene word desde cookie si no está en HTML.
    FIX: filtra placeholders/loading.gif.
    FIX: acepta imágenes reales sin verificar conteo exacto.
    """
    images: list[str] = []
    api = f"{BASE_URL}/roll_manga/apiv1/manga/{slug}/chapters/{chapter_id}/images/"

    # word: HTML → cookie (servidor lo setea al visitar la página)
    token = word or sess.cookies.get("word", "")

    for page in range(1, n_pages + 1):
        params: dict = {"page": page}
        if token:
            params["token"] = token
        try:
            r = sess.get(
                api, params=params, timeout=TIMEOUT, headers={"Referer": BASE_URL + "/"}
            )
            if r.status_code != 200:
                break
            data = r.json()
            if isinstance(data, dict):
                if "images" in data:
                    for img in data["images"]:
                        u = img.get("url", "") if isinstance(img, dict) else str(img)
                        if u and not _is_placeholder(u):
                            images.append(u if u.startswith("http") else "https:" + u)
                    continue
                if "url" in data:
                    u = data["url"]
                    if u and not _is_placeholder(u):
                        images.append(u if u.startswith("http") else "https:" + u)
                    continue
            if isinstance(data, list):
                for item in data:
                    u = item.get("url", "") if isinstance(item, dict) else str(item)
                    if u and not _is_placeholder(u):
                        images.append(u if u.startswith("http") else "https:" + u)
        except Exception:
            break

    return [u for u in images if _is_real_image(u) and "cover" not in u.lower()]


def _page_image(sess: requests.Session, page_url: str, referer: str) -> Optional[str]:
    html = _fetch_html(sess, page_url, referer)
    if not html:
        return None
    soup = _soup(html)
    for sel in [
        "img#image",
        "img.reader-main-img",
        "#viewer img",
        ".read-manga-page img",
        "section.reader-main img",
    ]:
        img = soup.select_one(sel)
        if img:
            for attr in ("data-original", "data-src", "src"):
                src = img.get(attr, "")
                if (
                    src
                    and any(x in src for x in ("fmcdn", "mfcdn"))
                    and not _is_placeholder(src)
                ):
                    return src if src.startswith("http") else "https:" + src
    for m in _RE_IMGURL.finditer(html):
        src = m.group(1)
        if (
            "/logo" not in src
            and "/icon" not in src
            and "cover" not in src.lower()
            and not _is_placeholder(src)
        ):
            return src
    return None


def _get_chapter_images(sess: requests.Session, chap_url: str, slug: str) -> list[str]:
    import logging

    log = logging.getLogger(__name__)

    base_chap = re.sub(r"/\d+\.html$", "", chap_url)

    # Visitar la página — el servidor puede setear el cookie 'word' aquí
    html = _fetch_html(sess, chap_url, BASE_URL)
    if not html:
        return []

    chapter_id, n_pages, word = _js_vars(html)

    # Intentar obtener el word token real via el endpoint AJAX de fanfox
    if not word:
        word = _get_word_from_chapter(sess, chap_url, chapter_id or "") or None

    if not word:
        log.warning(
            "[mangafox] Sin word token — fanfox requiere login para descargar capítulos. "
            "Completá FANFOX_EMAIL y FANFOX_PASSWORD en d_mangafox.py."
        )

    if n_pages == 0:
        soup = _soup(html)
        nums: set = set()
        for a in soup.find_all("a", href=re.compile(r"/\d+\.html$")):
            m = re.search(r"/(\d+)\.html$", a.get("href", ""))
            if m:
                nums.add(int(m.group(1)))
        if nums:
            n_pages = max(nums)

    if chapter_id and n_pages > 0:
        images = _api_images(sess, slug, chapter_id, n_pages, word, chap_url)
        if images and not _is_cover_response(images):
            return images
        if images:
            log.warning(
                "[mangafox] API retornó solo portadas — word token inválido o sin login."
            )

    # Fallback: scrape página por página
    max_scan = n_pages if n_pages > 0 else 60
    imgs_by_page: dict = {}
    with ThreadPoolExecutor(max_workers=4) as exe:
        futs = {
            exe.submit(_page_image, sess, f"{base_chap}/{p}.html", chap_url): p
            for p in range(1, max_scan + 1)
        }
        for fut in as_completed(futs):
            p = futs[fut]
            img = fut.result()
            if img:
                imgs_by_page[p] = img
    return [imgs_by_page[p] for p in sorted(imgs_by_page)]


class DownloaderMangafox(BaseDownloader):
    NAME = "FANFOX  (fanfox.net)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()

    def search(self, query: str) -> list[dict]:
        results, seen = [], set()
        for page in range(1, 80):
            url = f"{BASE_URL}/search?title={requests.utils.quote(query)}"
            if page > 1:
                url += f"&page={page}"
            html = _fetch_html(self._sess, url)
            if not html:
                break
            batch = _parse_manga_list(html)
            added = 0
            for it in batch:
                if it["slug"] not in seen:
                    seen.add(it["slug"])
                    results.append(it)
                    added += 1
            if added == 0:
                break
            time.sleep(0.3)
        return results

    def get_catalog(self, max_pages: int = 143) -> list[dict]:
        results, seen = [], set()
        for page in range(1, max_pages + 1):
            html = None
            for url in [
                f"{BASE_URL}/directory/{page}.html",
                f"{BASE_URL}/directory/?page={page}",
            ]:
                html = _fetch_html(self._sess, url)
                if html:
                    break
            if not html:
                break
            batch = _parse_manga_list(html)
            nuevos = 0
            for it in batch:
                if it["slug"] not in seen:
                    seen.add(it["slug"])
                    results.append(it)
                    nuevos += 1
            if nuevos == 0 and page > 2:
                break
            time.sleep(0.35)
        return results

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list, bool]:
        key = "fanfox"
        if getattr(self, "_cat_buf_key", None) != key:
            self._cat_buf = []
            self._cat_buf_key = key
            self._cat_srv_page = 0
            self._cat_exhausted = False
            self._cat_seen = set()

        start = (page - 1) * page_size
        end = start + page_size

        while len(self._cat_buf) < end and not self._cat_exhausted:
            self._cat_srv_page += 1
            html = None
            for url in [
                f"{BASE_URL}/directory/{self._cat_srv_page}.html",
                f"{BASE_URL}/directory/?page={self._cat_srv_page}",
            ]:
                html = _fetch_html(self._sess, url)
                if html:
                    break
            if not html:
                self._cat_exhausted = True
                break
            batch = _parse_manga_list(html)
            added = 0
            for it in batch:
                if it["slug"] not in self._cat_seen:
                    self._cat_seen.add(it["slug"])
                    self._cat_buf.append(it)
                    added += 1
            if added == 0:
                self._cat_exhausted = True
            time.sleep(0.25)

        chunk = self._cat_buf[start:end]
        has_more = (not self._cat_exhausted) or (end < len(self._cat_buf))
        return chunk, has_more

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        slug = item.get("slug") or item.get("id", "")
        data = _parse_series(self._sess, slug)
        if not data:
            return {}, []
        chapters = data.pop("chapters", [])
        return data, chapters

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        url = chapter.get("url", "")
        slug = series.get("slug", series.get("id", ""))
        if not url:
            return []
        return _get_chapter_images(self._sess, url, slug)

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        for attempt in range(3):
            try:
                r = self._sess.get(
                    url, timeout=TIMEOUT, headers={"Referer": BASE_URL + "/"}
                )
                if r.status_code == 200 and r.content:
                    return r.content
            except Exception:
                time.sleep(RETRY * (attempt + 1))
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        return BASE_URL + "/"
