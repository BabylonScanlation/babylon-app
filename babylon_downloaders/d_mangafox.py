"""
d_mangafox.py — fanfox.net downloader (Modularizado)

ACTUALIZACIÓN:
  - Eliminado el requerimiento de login, tokens CSRF y extracción de 'word'.
  - Se utiliza la versión móvil (m.fanfox.net) para evadir las protecciones de la API.
  - Se extraen las imágenes directamente del DOM usando selectores verificados.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from bs4 import BeautifulSoup
from common import CFG, BaseDownloader

BASE_URL = "https://fanfox.net"
MOBILE_URL = "https://m.fanfox.net"
TIMEOUT = (15, 45)
RETRY = 2.0
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": f"{BASE_URL}/",
}

_CHAP_URL_RE = re.compile(r"/manga/[^/]+/(?:v([^/]+)/)?c([^/]+)/\d+\.html")


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


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


# ─────────────────────────────────────────────
# EXTRAER IMAGEN DE LA PÁGINA (Lógica Nueva)
# ─────────────────────────────────────────────
def _extract_image_from_page(soup: BeautifulSoup) -> Optional[str]:
    selectors = [
        "img#image",
        "img.reader-image",
        "img.manga-page",
        ".reader-main img",
        "#viewer img",
        "img[src*='compressed']",
        "img[src*='zjcdn']",
        "img[src*='mangafox']",
        "img[data-src]",
        "img[width][height][src*='.jpg'], img[width][height][src*='.png']",
    ]
    for sel in selectors:
        img_tag = soup.select_one(sel)
        if img_tag:
            src = img_tag.get("data-src") or img_tag.get("src") or ""
            if src:
                if src.startswith("//"):
                    src = "https:" + src
                elif not src.startswith("http"):
                    src = BASE_URL + src
                return src
    return None


# ─────────────────────────────────────────────
# PARSERS ORIGINALES (Búsqueda y Directorio)
# ─────────────────────────────────────────────
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


# ─────────────────────────────────────────────
# OBTENCIÓN DE IMÁGENES (Versión Móvil)
# ─────────────────────────────────────────────
def _get_chapter_images(sess: requests.Session, chap_url: str) -> list[str]:
    mobile_base = chap_url.replace(BASE_URL, MOBILE_URL)
    if not mobile_base.endswith(".html"):
        mobile_base = mobile_base.rstrip("/") + "/1.html"
    else:
        mobile_base = re.sub(r"/\d+\.html$", "/1.html", mobile_base)

    html = _fetch_html(sess, mobile_base, BASE_URL)
    if not html:
        return []

    soup = _soup(html)
    page_count = 1

    # Detección robusta de páginas
    text = soup.get_text()
    m = re.search(r"/\s*(\d+)", text)
    if m:
        page_count = int(m.group(1))

    m = re.search(r"Page\s*\d+\s*of\s*(\d+)", text, re.I)
    if m and int(m.group(1)) > page_count:
        page_count = int(m.group(1))

    select = soup.find("select", {"name": "page"}) or soup.find(
        "select", id=re.compile(r"page", re.I)
    )
    if select:
        opts = select.find_all("option")
        if opts and len(opts) > page_count:
            page_count = len(opts)

    pagination_block = (
        soup.select_one(".pager, .pagination, .page-nav, .chapter-page-nav") or soup
    )
    nums = [
        int(t)
        for t in re.findall(r"\b(\d{1,3})\b", pagination_block.get_text())
        if 1 <= int(t) <= 999
    ]
    if nums:
        max_num = max(nums)
        if max_num > page_count:
            page_count = max_num

    base_mobile = re.sub(r"/\d+\.html$", "", mobile_base)
    imgs_by_page: dict = {}

    def _get_page_img(p: int) -> Optional[str]:
        pg_url = f"{base_mobile}/{p}.html"
        ph = _fetch_html(sess, pg_url, mobile_base)
        if ph:
            return _extract_image_from_page(_soup(ph))
        return None

    # Parseo concurrente para acelerar la extracción desde el sitio móvil
    with ThreadPoolExecutor(max_workers=4) as exe:
        futs = {exe.submit(_get_page_img, p): p for p in range(1, page_count + 1)}
        for fut in as_completed(futs):
            p = futs[fut]
            img = fut.result()
            if img:
                imgs_by_page[p] = img

    return [imgs_by_page[p] for p in sorted(imgs_by_page)]


# ─────────────────────────────────────────────
# CLASE BASE PARA EL FRAMEWORK
# ─────────────────────────────────────────────
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
        if not url:
            return []
        return _get_chapter_images(self._sess, url)

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
