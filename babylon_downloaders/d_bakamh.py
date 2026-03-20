"""
d_bakamh.py — bakamh.com downloader (sin menú)
WordPress + curl_cffi (fallback a requests).
"""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import quote, unquote, urljoin

from common import CFG, BaseDownloader

BASE_URL = "https://bakamh.com"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"
REQUEST_DELAY = 0.5
GENRE_URL_TYPE = "manga-genre"  # puede cambiar dinámicamente

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE_URL + "/",
}

try:
    from curl_cffi.requests import Session as CurlSession

    _USE_CURL = True
except ImportError:
    CurlSession = None
    _USE_CURL = False

try:
    from bs4 import BeautifulSoup

    _HAS_BS4 = True
except ImportError:
    BeautifulSoup = None
    _HAS_BS4 = False

_UI_BUTTON_TEXTS = {
    "取消回复",
    "观看最新话",
    "观看第一话",
    "发表评论",
    "留言",
    "登录",
    "注册",
    "回复",
    "举报",
    "上一章",
    "下一章",
    "返回目录",
    "cancel reply",
    "leave a reply",
    "first chapter",
    "latest chapter",
    "previous chapter",
    "next chapter",
    "login",
    "register",
    "report",
}


# ── HTTP helpers ──────────────────────────────────────────────────────────────


def _make_session():
    if _USE_CURL:
        s = CurlSession(impersonate="chrome123")
    else:
        import requests as _req

        s = _req.Session()
    s.headers.update(HEADERS)
    return s


def _get(sess, url, params=None, referer=None, retries=3):
    hdrs = {"Referer": referer} if referer else {}
    for i in range(retries):
        try:
            r = sess.get(url, params=params, headers=hdrs, timeout=25)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 404):
                return None
        except Exception:
            pass
        if i < retries - 1:
            time.sleep(2)
    return None


def _post(sess, url, data, referer=None, retries=3):
    hdrs = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }
    if referer:
        hdrs["Referer"] = referer
    for i in range(retries):
        try:
            r = sess.post(url, data=data, headers=hdrs, timeout=25)
            if r.status_code == 200:
                return r
        except Exception:
            pass
        if i < retries - 1:
            time.sleep(2)
    return None


def _soup(html):
    return BeautifulSoup(html, "html.parser") if _HAS_BS4 else None


def _is_ui_button(text):
    if not text:
        return False
    t = text.strip().lower()
    if t in {x.lower() for x in _UI_BUTTON_TEXTS}:
        return True
    return len(t) <= 2 and not re.search(r"\d", t)


# ── Chapter parsing helpers ───────────────────────────────────────────────────


def _manga_id(soup, html):
    el = soup.select_one("#manga-chapters-holder, [data-id]") if soup else None
    if el and el.get("data-id"):
        return el["data-id"]
    m = re.search(r'"manga_id"\s*:\s*"?(\d+)"?', html)
    return m.group(1) if m else ""


def _nonce_from_html(html):
    for pat in [
        r'"wpmangaloadmore"\s*:\s*\{[^}]*"nonce"\s*:\s*"([a-f0-9]+)"',
        r'nonce["\']?\s*:\s*["\']([a-f0-9]{8,})["\']',
    ]:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return ""


def _chapters_from_html(soup, manga_slug=""):
    if soup is None:
        return []
    chapters = []

    for a in soup.select("a[chapter-data-url]"):
        href = a.get("chapter-data-url", "").strip().rstrip("/")
        if not href or "/manga/" not in href:
            continue
        ch_slug = href.split("/")[-1]
        title = a.get_text(strip=True)
        if _is_ui_button(title):
            continue
        chapters.append({"id": ch_slug, "title": title, "url": href, "slug": ch_slug})

    if chapters:
        chapters.reverse()
        return chapters

    for li in soup.select(
        "li.wp-manga-chapter, .chapter-list li, .chapters-list li,"
        " .listing-chapters_wrap li"
    ):
        a = li.select_one("a")
        if not a or not a.get("href"):
            continue
        href = a["href"].rstrip("/")
        ch_slug = href.split("/")[-1]
        title = a.get_text(strip=True)
        if ch_slug and "/manga/" in href:
            chapters.append(
                {"id": ch_slug, "title": title, "url": href, "slug": ch_slug}
            )
    if chapters:
        chapters.reverse()
    return chapters


def _chapters_ajax(sess, manga_slug, manga_id, nonce):
    ref = f"{BASE_URL}/manga/{quote(manga_slug, safe='')}/"
    for action in [
        "manga_get_chapters",
        "wp_manga_get_chapters",
        "manga_get_chapter_list",
    ]:
        if not manga_id and "list" not in action:
            continue
        data = {"action": action, "_wpnonce": nonce}
        if manga_id:
            data["manga"] = manga_id
        r = _post(sess, AJAX_URL, data=data, referer=ref)
        if not r:
            continue
        try:
            j = r.json()
            frag = j.get("data", "") if isinstance(j, dict) else r.text
        except Exception:
            frag = r.text
        if not frag or len(frag) < 50:
            continue
        s2 = _soup(frag)
        chapters = _chapters_from_html(s2, manga_slug)
        if chapters:
            return chapters
    return []


# ── Manga info ────────────────────────────────────────────────────────────────


def _get_manga_info(sess, slug):
    encoded = quote(slug, safe="")
    url = f"{BASE_URL}/manga/{encoded}/"
    time.sleep(REQUEST_DELAY)
    r = _get(sess, url, referer=BASE_URL + "/")
    if not r:
        return None, []
    html = r.text
    soup = _soup(html)
    if not soup:
        return None, []

    title = ""
    for sel in [".post-title h1", ".post-title h3", "h1.entry-title", "h1"]:
        el = soup.select_one(sel)
        if el:
            title = el.get_text(strip=True)
            break
    if not title:
        title = slug

    status = ""
    for sel in [".post-status .summary-content", ".manga-status .summary-content"]:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(strip=True)
            if txt and len(txt) < 20:
                status = txt
                break

    meta = {"id": slug, "slug": slug, "title": title, "status": status}
    chapters = _chapters_from_html(soup, slug)
    if not chapters:
        mid = _manga_id(soup, html)
        nonce = _nonce_from_html(html)
        chapters = _chapters_ajax(sess, slug, mid, nonce)
    return meta, chapters


# ── Images ────────────────────────────────────────────────────────────────────


def _get_chapter_images(sess, manga_slug, chapter_slug):
    import json as _json

    encoded = quote(manga_slug, safe="")
    ch_url = f"{BASE_URL}/manga/{encoded}/{chapter_slug}/"
    time.sleep(REQUEST_DELAY)
    r = _get(sess, ch_url, referer=f"{BASE_URL}/manga/{encoded}/")
    if not r:
        return []
    html = r.text
    soup = _soup(html)
    urls = []

    if soup:
        for img in soup.select(
            "div.reading-content img, div.page-break img,"
            " img.wp-manga-chapter-img, .reading-content noscript img"
        ):
            src = (
                img.get("data-lazy-src") or img.get("data-src") or img.get("src") or ""
            ).strip()
            if src and not src.startswith("data:"):
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = BASE_URL + src
                if src not in urls:
                    urls.append(src)

    if urls:
        return urls

    m = re.search(r"var\s+imageLinks\s*=\s*(\[[^\]]+\])", html, re.DOTALL)
    if m:
        try:
            for item in _json.loads(m.group(1)):
                item = item.strip()
                if item.startswith("http"):
                    urls.append(item)
        except Exception:
            pass

    return urls


# ── Catalog ───────────────────────────────────────────────────────────────────


def _parse_manga_cards(soup):
    items = []
    seen = set()
    if soup is None:
        return items

    for a in soup.find_all("a", href=re.compile(r"/manga/[^/\s]")):
        href = a.get("href", "").rstrip("/")
        if re.search(r"/manga/[^/]+/[^/]+$", href):
            continue
        text = a.get_text(strip=True)
        if not text or len(text) < 2:
            continue
        m = re.search(r"/manga/([^/?#]+)", href)
        if not m:
            continue
        slug = unquote(m.group(1))
        if slug in seen:
            continue
        seen.add(slug)
        items.append({"id": slug, "slug": slug, "title": text})
    return items


def _get_catalog_page(sess, page=1, genre_slug="", sort="latest"):
    if genre_slug:
        candidates = [
            f"{BASE_URL}/{GENRE_URL_TYPE}/{genre_slug}/page/{page}/",
        ]
    else:
        candidates = (
            [f"{BASE_URL}/blgl/", f"{BASE_URL}/manga/"]
            if page == 1
            else [f"{BASE_URL}/blgl/page/{page}/", f"{BASE_URL}/manga/page/{page}/"]
        )
    params = {"m_orderby": sort} if sort != "latest" else None
    for url in candidates:
        time.sleep(0.2)
        r = _get(sess, url, params=params, referer=BASE_URL + "/")
        if r:
            soup = _soup(r.text)
            items = _parse_manga_cards(soup)
            if items:
                return items
    return []


# ══════════════════════════════════════════════════════════════
#  CLASE PÚBLICA
# ══════════════════════════════════════════════════════════════
class DownloaderBakamh(BaseDownloader):
    NAME = "BAKAMH  (bakamh.com)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()

    def search(self, query: str) -> list[dict]:
        import requests as _req

        time.sleep(REQUEST_DELAY)
        url = f"{BASE_URL}/"
        params = {"s": query, "post_type": "wp-manga"}
        try:
            r = self._sess.get(
                url, params=params, timeout=20, headers={"Referer": BASE_URL + "/"}
            )
            if r.status_code == 200:
                soup = _soup(r.text)
                items = _parse_manga_cards(soup)
                return items
        except Exception:
            pass
        return []

    def get_catalog(
        self, genre_slug: str = "", sort: str = "latest", max_pages: int = 50
    ) -> list[dict]:
        results, seen, page = [], set(), 1
        while page <= max_pages:
            items = _get_catalog_page(self._sess, page, genre_slug, sort)
            if not items:
                break
            added = 0
            for it in items:
                if it["slug"] not in seen:
                    seen.add(it["slug"])
                    results.append(it)
                    added += 1
            if added == 0:
                break
            page += 1
        return results

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list, bool]:
        genre_slug = kwargs.get("genre_slug", "")
        sort = kwargs.get("sort", "latest")
        # Página del servidor ≈ página del menú (bakamh tiene ~12 items/página)
        # Cargamos suficientes páginas del servidor para llenar page_size items
        key = f"{genre_slug}|{sort}"
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
            items = _get_catalog_page(self._sess, self._cat_srv_page, genre_slug, sort)
            if not items:
                self._cat_exhausted = True
                break
            added = 0
            for it in items:
                if it["slug"] not in self._cat_seen:
                    self._cat_seen.add(it["slug"])
                    self._cat_buf.append(it)
                    added += 1
            if added == 0:
                self._cat_exhausted = True

        chunk = self._cat_buf[start:end]
        has_more = (not self._cat_exhausted) or (end < len(self._cat_buf))
        return chunk, has_more

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        slug = item.get("slug") or item.get("id", "")
        meta, chapters = _get_manga_info(self._sess, slug)
        return (meta or {}), chapters

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        manga_slug = series.get("slug", series.get("id", ""))
        ch_slug = chapter.get("slug", chapter.get("id", ""))
        return _get_chapter_images(self._sess, manga_slug, ch_slug)

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        hdrs = {
            "Referer": referer or BASE_URL,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        for attempt in range(3):
            try:
                r = self._sess.get(url, headers=hdrs, timeout=30)
                if r.status_code == 200 and r.content:
                    return r.content
            except Exception:
                pass
            time.sleep(1.5)
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        manga_slug = series.get("slug", series.get("id", ""))
        ch_slug = chapter.get("slug", chapter.get("id", ""))
        return f"{BASE_URL}/manga/{quote(manga_slug, safe='')}/{ch_slug}/"
