"""
d_toonkor.py — toonkor downloader (sin menú)
Scrapling para parseo. Auto-detección de dominio. base64 image lists.
"""

from __future__ import annotations

import base64
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, cast

import requests
from common import CFG, BaseDownloader

BASE_URL = "https://tkor098.com/"
TIMEOUT = (10, 15)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    ),
    "Referer": BASE_URL,
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
}

try:
    from scrapling import Selector as _Selector

    def Selector(html: str, url: str = ""):
        return _Selector(html, url=url)
except ImportError:
    from bs4 import BeautifulSoup as _BS4

    class Selector:
        def __init__(self, html, url=""):
            self._s = _BS4(html, "html.parser")

        def css(self, sel):
            class _Wrap:
                def __init__(self, nodes):
                    self._n = nodes

                @property
                def first(self):
                    return _ElemWrap(self._n[0]) if self._n else _ElemWrap(None)

            return _Wrap(self._s.select(sel))

    class _ElemWrap:
        def __init__(self, n):
            self._n = n

        @property
        def text(self):
            return self._n.get_text(strip=True) if self._n else ""

        @property
        def attrib(self):
            return self._n.attrs if self._n else {}

        def css(self, sel):
            class _W:
                def __init__(self, nodes):
                    self._n = nodes

                @property
                def first(self):
                    return _ElemWrap(self._n[0] if self._n else None)

            return _W(self._n.select(sel) if self._n else [])


_NAV_SLUGS = {
    "웹툰",
    "애니",
    "주소안내",
    "단행본",
    "망가",
    "포토툰",
    "코사이트",
    "토토보증업체",
    "notice",
    "bbs",
}
_UI_PATHS = ("/images/", "/bann/", "/img/", "/icons/", "/logo")
_CDN_RE = re.compile(
    r"https?://(?:aws-cloud-no[123]\.site|cdn\.[^\s\"'<>]+)"
    r"/[^\s\"'<>]+\.(?:jpe?g|png|webp|gif)",
    re.I,
)
_TKOR_DOMAIN_RE = re.compile(r"https?://(tkor\d+\.com)", re.I)
_REDIRECT_SOURCES = [
    "https://xn--2h7b95c.net/",
    "https://xn--2h7b95c.kr/",
    "https://xn--2h7b95c.tech/",
]

_CATALOG_SECTIONS = [
    ("웹툰", "웹툰"),
    ("웹툰?fil=인기", "웹툰 인기"),
    ("웹툰?fil=최신", "웹툰 최신"),
    ("웹툰?fil=성인", "웹툰 성인"),
    ("웹툰/완결", "웹툰 완결"),
    ("단행본", "단행본"),
    ("단행본?fil=인기", "단행본 인기"),
    ("망가", "망가"),
    ("망가?fil=인기", "망가 인기"),
    ("포토툰", "포토툰"),
]


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(BASE_URL, timeout=10)
    except Exception:
        pass
    return s


def resolve_domain(sess: requests.Session) -> Optional[str]:
    for url in _REDIRECT_SOURCES:
        try:
            r = sess.get(url, timeout=8, allow_redirects=True)
            m = _TKOR_DOMAIN_RE.search(r.text) or _TKOR_DOMAIN_RE.search(r.url)
            if m:
                return f"https://{m.group(1)}/"
        except Exception:
            continue
    return None


def _fetch_section(args: tuple) -> tuple[str, list[dict]]:
    sess, base, path, label = args
    url = base + path
    try:
        r = sess.get(url, timeout=12)
        if r.status_code != 200:
            return label, []
        page = Selector(r.text, url=url)
        items: list[dict] = []
        seen: set = set()
        for a in page.css("a[href]"):
            attrib = getattr(a, "attrib", {})
            href = str(attrib.get("href", "")).strip("/")
            if not href or href in _NAV_SLUGS or "/" in href:
                continue
            if any(c in href for c in ("?", "#", "http", ".", "board", "search")):
                continue
            text = str(getattr(a, "text", "") or "").strip()
            title = text if text and "더 읽기" not in text else href
            if href not in seen:
                seen.add(href)
                # Clean title: strip leading dash/bullet separators
                title = title.lstrip("- ").strip() or href.replace("-", " ")
                items.append({"id": href, "slug": href, "title": title})
        return label, items
    except Exception:
        return label, []


def _fetch_section_page(args: tuple) -> tuple[str, list[dict], bool]:
    """Fetch a single page of a catalog section. Returns (label, items, has_more)."""
    sess, base, path, label, page = args
    sep = "&" if "?" in path else "?"
    url = base + path + (f"{sep}page={page}" if page > 1 else "")
    try:
        r = sess.get(url, timeout=12)
        if r.status_code != 200:
            return label, [], False
        from bs4 import BeautifulSoup as _BS

        soup = _BS(r.text, "html.parser")
        items: list[dict] = []
        seen: set = set()
        from urllib.parse import unquote as _uq

        for a in soup.find_all("a", href=True):
            href = _uq(a["href"].strip()).strip("/")
            if not href or href in _NAV_SLUGS or "/" in href:
                continue
            if any(c in href for c in ("?", "#", "http", ".", "board", "search", "=")):
                continue
            text = a.get_text(strip=True)
            title = text if text and "더 읽기" not in text else href
            if not title or len(href) < 2:
                continue
            if href not in seen:
                seen.add(href)
                items.append({"id": href, "slug": href, "title": title})
        # Detectar si hay más páginas
        has_more = bool(
            soup.select_one("a[href*='page=']")
            or soup.select_one(".pagination .next")
            or soup.select_one("a.next")
        )
        return label, items, has_more
    except Exception:
        return label, [], False


def load_catalog(sess: requests.Session, base: str, workers: int = 8) -> list[dict]:
    all_items: list[dict] = []
    seen: set = set()
    # Carga la primera página de cada sección (y páginas adicionales si las hay)
    page = 1
    while True:
        tasks = [(sess, base, path, lbl, page) for path, lbl in _CATALOG_SECTIONS]
        found_any = False
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for lbl, items, has_more in pool.map(_fetch_section_page, tasks):
                for it in items:
                    if it["slug"] not in seen:
                        seen.add(it["slug"])
                        all_items.append(it)
                        found_any = True
        if not found_any or page >= 10:
            break
        page += 1
    return all_items


def search_global(sess: requests.Session, base: str, query: str) -> list[dict]:
    from urllib.parse import unquote, urlencode

    from bs4 import BeautifulSoup as _BS

    results: list[dict] = []
    seen: set = set()
    for pg in range(1, 10):
        params = {"sfl": "wr_subject||wr_content", "stx": query, "page": str(pg)}
        url = base + "bbs/search.php?" + urlencode(params)
        try:
            r = sess.get(url, timeout=15, headers=HEADERS)
            if r.status_code != 200:
                break
            html = r.text
            if any(
                s in html
                for s in [
                    "검색된 자료가 없습니다",
                    "결과가 없습니다",
                    "검색결과가 없습니다",
                ]
            ):
                break
            soup = _BS(html, "html.parser")
            added = 0
            # Intentar selectores del resultado de búsqueda de gnuboard
            for sel in [
                "#bo_list .td_subject a",
                ".list_item .title a",
                "ul.bo_list li a",
                ".wr_subject a",
                "#bo_list td a[href]",
            ]:
                for a in soup.select(sel):
                    href = (a.get("href") or "").strip().strip("/")
                    title = a.get_text(strip=True)
                    if not href or not title or len(title) < 2:
                        continue
                    slug = href.split("/")[-1].split("?")[0]
                    if not slug or slug in _NAV_SLUGS:
                        continue
                    if any(c in slug for c in (".", " ", "#")):
                        continue
                    if slug not in seen:
                        seen.add(slug)
                        results.append({"id": slug, "slug": slug, "title": title})
                        added += 1
            # Fallback: cualquier link que apunte a una sección de serie
            if added == 0:
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if not href.startswith("/") or href.count("/") != 1:
                        continue
                    slug = href.strip("/")
                    if slug in _NAV_SLUGS or not slug or len(slug) < 3:
                        continue
                    if any(c in slug for c in (".", "?", " ", "#", "=")):
                        continue
                    title = a.get_text(strip=True)
                    if not title or len(title) < 2:
                        continue
                    if slug not in seen:
                        seen.add(slug)
                        results.append({"id": slug, "slug": slug, "title": title})
                        added += 1
            if added == 0:
                break
        except Exception:
            break
    return results


def _parse_series_page(
    sess: requests.Session, base: str, slug: str
) -> tuple[dict, list[dict]]:
    from urllib.parse import quote as _q
    from urllib.parse import unquote as _uq

    slug_dec = _uq(slug)
    # Try decoded first, then URL-encoded variants
    r = None
    for attempt_slug in [
        slug_dec,
        slug,
        _q(slug_dec, safe="-_"),
        _q(slug_dec, safe=""),
    ]:
        try:
            _r = sess.get(base + attempt_slug, timeout=15, headers=HEADERS)
            if _r.status_code == 200:
                r = _r
                break
        except Exception:
            pass
    if r is None:
        return {}, []
    html = r.text
    from bs4 import BeautifulSoup as _BS4

    soup = _BS4(html, "html.parser")
    # Title
    title = slug.replace("-", " ")
    for sel in [
        "h1",
        ".toon-title",
        ".series-title",
        "#toon_title",
        ".title",
        "h2",
        ".comicinfo .title",
        ".view-title",
    ]:
        node = soup.select_one(sel)
        if node:
            t = node.get_text(strip=True)
            if t and len(t) > 1:
                title = t
                break

    # Collect all chapter URLs from <a> tags with href containing 화/회/\.html
    chap_urls: dict[int, str] = {}  # num -> href

    # Pass 1: look for <a> hrefs that look like chapter links
    for a in soup.find_all("a", href=True):
        raw_href = a["href"]
        href = _uq(raw_href)
        # match /SLUG_N화.html or /SLUG-N화.html or /ANYTHING_N화.html
        m = re.search(r"[/_-](\d+)[화회]?\.html", href, re.I)
        if not m:
            # also match ?no=N or &no=N patterns
            m = re.search(r"[?&]no=(\d+)", href, re.I)
        if m:
            num = int(m.group(1))
            if num > 0 and num not in chap_urls:
                chap_urls[num] = raw_href

    # Pass 2: raw HTML scan (catches JS-rendered or data-href)
    if not chap_urls:
        slug_esc = re.escape(slug)
        for patt in [
            rf"/{slug_esc}[_-]?(\d+)[화회]?\.html",
            r"/[A-Za-z0-9가-힣_%-]+[_-](\d+)[화회]?\.html",
            r"['`/](\d+)[화회]?\.html",
        ]:
            for mm in re.finditer(patt, html, re.I):
                num = int(mm.group(1))
                if num > 0 and num not in chap_urls:
                    chap_urls[num] = mm.group(0).strip("\"'")

    meta = {"id": slug, "slug": slug, "title": title}
    chapters_out = [
        {"id": str(n), "num": n, "title": f"Capítulo {n}", "href": h}
        for n, h in sorted(chap_urls.items(), reverse=True)
    ]
    return meta, chapters_out


def _extract_images(chapter_html: str) -> list[str]:
    # Strategy 1: base64 var
    m64 = re.search(r"var toon_img\s*=\s*'([^']+)';", chapter_html)
    if m64:
        try:
            decoded = base64.b64decode(m64.group(1)).decode("utf-8", errors="replace")
            page = Selector(decoded)
            urls = []
            for img in page.css("img[src]"):
                src = str((getattr(img, "attrib", {}) or {}).get("src", ""))
                if src.startswith("http") and not any(p in src for p in _UI_PATHS):
                    urls.append(src)
            if urls:
                return urls
        except Exception:
            pass
    # Strategy 2: CDN regex
    cdn = [
        u
        for u in dict.fromkeys(_CDN_RE.findall(chapter_html))
        if not any(p in u for p in _UI_PATHS)
    ]
    if cdn:
        return cdn
    # Strategy 3: img tags
    page = Selector(chapter_html)
    candidates = []
    for img in page.css("img"):
        attrib = getattr(img, "attrib", {}) or {}
        src = str(attrib.get("src") or attrib.get("data-src") or "")
        if (
            src.startswith("https://")
            and not any(p in src for p in _UI_PATHS)
            and any(ext in src.lower() for ext in (".jpg", ".jpeg", ".png", ".webp"))
        ):
            candidates.append(src)
    return list(dict.fromkeys(candidates))


class DownloaderToonkor(BaseDownloader):
    NAME = "TOONKOR  (tkonkorXXX.com)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()
        detected = resolve_domain(self._sess)
        if detected:
            global BASE_URL
            BASE_URL = detected
            self._sess.headers.update({"Referer": BASE_URL})
        self._base = BASE_URL
        print(f"  Dominio: {self._base.rstrip('/')}")

    def search(self, query: str) -> list[dict]:
        return search_global(self._sess, self._base, query)

    def get_catalog(self) -> list[dict]:
        return load_catalog(self._sess, self._base)

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list, bool]:
        key = self._base
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
            tasks = [
                (self._sess, self._base, path, lbl, self._cat_srv_page)
                for path, lbl in _CATALOG_SECTIONS
            ]
            found = 0
            with ThreadPoolExecutor(max_workers=4) as pool:
                for lbl, items, _ in pool.map(_fetch_section_page, tasks):
                    for it in items:
                        if it["slug"] not in self._cat_seen:
                            self._cat_seen.add(it["slug"])
                            self._cat_buf.append(it)
                            found += 1
            if found == 0 or self._cat_srv_page >= 10:
                self._cat_exhausted = True

        chunk = self._cat_buf[start:end]
        has_more = (not self._cat_exhausted) or (end < len(self._cat_buf))
        return chunk, has_more

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        from urllib.parse import quote as _q
        from urllib.parse import unquote as _uq

        slug = item.get("slug") or item.get("id", "")
        # Slugs may contain brackets or special chars; try decoded then encoded
        meta, chapters = _parse_series_page(self._sess, self._base, _uq(slug))
        if not chapters:
            # Try URL-encoding special chars (brackets, spaces)
            meta, chapters = _parse_series_page(
                self._sess, self._base, _q(_uq(slug), safe="-_")
            )
        return meta, chapters

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        from urllib.parse import unquote as _uq

        slug = series.get("slug", series.get("id", ""))
        num = chapter.get("num", int(chapter.get("id", 0)))
        href = chapter.get("href", "")

        # Build candidate URLs: stored href first, then common patterns
        candidates: list[str] = []
        if href:
            if href.startswith("http"):
                candidates.append(href)
            else:
                candidates.append(self._base.rstrip("/") + "/" + _uq(href).lstrip("/"))
        # Common toonkor URL patterns
        for fmt in [
            f"{self._base}{slug}_{num}화.html",
            f"{self._base}{slug}-{num}화.html",
            f"{self._base}{slug}_{num}.html",
            f"{self._base}{slug}-{num}.html",
        ]:
            if fmt not in candidates:
                candidates.append(fmt)

        for url in candidates:
            try:
                r = self._sess.get(url, timeout=TIMEOUT, headers=HEADERS)
                if r.status_code == 200:
                    imgs = _extract_images(r.text)
                    if imgs:
                        return imgs
            except Exception:
                pass
        return []

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        for attempt in range(3):
            try:
                r = self._sess.get(url, timeout=TIMEOUT)
                if r.status_code == 200:
                    return r.content
            except Exception:
                time.sleep(attempt + 1)
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        return self._base
