"""
d_mangafox.py — fanfox.net downloader (sin menú)
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


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(BASE_URL, timeout=10)
    except Exception:
        pass
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
            # skip non-chapter links like "Read Now" buttons
            if label.lower() in ("read now", "read", "start", ""):
                label = f"Ch.{chap}"
            chapters.append({"id": chap, "title": label, "chap": chap, "url": full_url})

    _harvest(html)

    # Try dedicated chapter-list pages (fanfox paginates long chapter lists)
    # These pages have ?page=N or chapter-list-N format
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
    return chid, cnt, word


def _api_images(
    sess: requests.Session,
    slug: str,
    chapter_id: str,
    n_pages: int,
    word: Optional[str],
) -> list[str]:
    images = []
    api = f"{BASE_URL}/roll_manga/apiv1/manga/{slug}/chapters/{chapter_id}/images/"
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
                        u = img.get("url", "")
                        if u:
                            images.append(u if u.startswith("http") else "https:" + u)
                    continue
                if "url" in data:
                    u = data["url"]
                    if u:
                        images.append(u if u.startswith("http") else "https:" + u)
                    continue
            if isinstance(data, list):
                for item in data:
                    u = item.get("url", "") if isinstance(item, dict) else str(item)
                    if u:
                        images.append(u if u.startswith("http") else "https:" + u)
        except Exception:
            break
    return images


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
                if src and any(x in src for x in ("fmcdn", "mfcdn")):
                    return src if src.startswith("http") else "https:" + src
    for m in _RE_IMGURL.finditer(html):
        src = m.group(1)
        if "/logo" not in src and "/icon" not in src:
            return src
    return None


def _get_chapter_images(sess: requests.Session, chap_url: str, slug: str) -> list[str]:
    base_chap = re.sub(r"/\d+\.html$", "", chap_url)
    html = _fetch_html(sess, chap_url, BASE_URL)
    if not html:
        return []
    chapter_id, n_pages, word = _js_vars(html)
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
        images = _api_images(sess, slug, chapter_id, n_pages, word)
        if len(images) == n_pages:
            return images
    # Fallback: scrape cada página
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
        """Paginación lazy: carga UNA página del servidor por pedido del menú."""
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
