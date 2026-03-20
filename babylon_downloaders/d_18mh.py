"""
d_18mh.py — 18mh.org downloader (sin menú)
"""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from common import CFG, BaseDownloader

SITE_URL = "https://18mh.org"
REQUEST_DELAY = 0.4
TIMEOUT = (15, 45)
RETRY_DELAY = 2.0

_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
    "Referer": f"{SITE_URL}/",
}

_EXCLUDE_IMG = ("/logo", "/icon", "/ads", "ad/", "cover/", "avatar", ".gif")


# ── HTTP helpers ──────────────────────────────────────────────────────────────


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_BASE_HEADERS)
    return s


def _get_raw(
    session: requests.Session, url: str, referer: str = "", retries: int = 3
) -> Optional[bytes]:
    hdrs = {"Referer": referer} if referer else {}
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=TIMEOUT, headers=hdrs)
            if r.status_code == 200 and r.content:
                return r.content
            if r.status_code in (403, 404):
                return None
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    return None


def _fetch_html(
    session: requests.Session, url: str, referer: str = ""
) -> Optional[str]:
    time.sleep(REQUEST_DELAY)
    raw = _get_raw(session, url, referer)
    if not raw:
        return None
    sniff = raw[:2048].decode("ascii", errors="ignore")
    m = re.search(r'charset=["\']?([\w\-]+)', sniff, re.I)
    enc = m.group(1) if m else "utf-8"
    return raw.decode(enc, errors="replace")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ── Scraping ──────────────────────────────────────────────────────────────────


def _parse_cards(html: str) -> list[dict]:
    soup = _soup(html)
    # Destruir sección de recomendados
    for h in soup.find_all(["h2", "h3"]):
        if any(
            kw in h.get_text(strip=True)
            for kw in ["您可能喜歡", "猜你喜歡", "推荐", "推薦"]
        ):
            p = h.parent
            (p if p and p.name == "div" else h).decompose()

    results, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"/manga/([^/?#]+)/?$")):
        href = a.get("href", "").rstrip("/")
        slug = href.split("/")[-1]
        if slug in seen or slug == "get":
            continue
        h3 = a.find(["h3", "h4", "p", "span"])
        img = a.find("img")
        title = (
            h3.get_text(strip=True)
            if h3 and h3.get_text(strip=True)
            else img.get("alt", slug)
            if img
            else slug
        )
        seen.add(slug)
        results.append({"id": slug, "slug": slug, "title": title})
    return results


def _parse_series_meta(session: requests.Session, slug: str) -> Optional[dict]:
    html = _fetch_html(session, f"{SITE_URL}/manga/{slug}")
    if not html:
        return None
    soup = _soup(html)
    tag = soup.find("h1")
    title = tag.get_text(strip=True) if tag else slug
    title = re.sub(r"\s*(完結|連載中|连载中|完结)\s*$", "", title).strip()
    mid = None
    m = re.search(r'data-mid="(\d+)"', html)
    if m:
        mid = m.group(1)
    status = "完結" if "完結" in html else "連載中"
    summary = ""
    for p in soup.find_all("p"):
        t = p.get_text(strip=True)
        if len(t) > 20 and not re.search(r"(Copyright|18歲|警告)", t):
            summary = t
            break
    return {
        "slug": slug,
        "id": slug,
        "title": title,
        "mid": mid,
        "status": status,
        "summary": summary,
    }


def _get_chapter_list(session: requests.Session, mid: str) -> list[dict]:
    if not mid:
        return []
    html = _fetch_html(session, f"{SITE_URL}/manga/get?mid={mid}&mode=all")
    if not html:
        return []
    soup = _soup(html)
    chapters = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(strip=True)
        if not title or "javascript" in href or title in ["排序", "最新章節"]:
            continue
        chapters.append({"id": href, "title": title, "url": urljoin(SITE_URL, href)})
    if chapters:
        chapters.reverse()
    return chapters


def _extract_chapter_images(session: requests.Session, chap_url: str) -> list[str]:
    html = _fetch_html(session, chap_url)
    if not html:
        return []
    soup = _soup(html)
    candidates = []
    for img in soup.find_all("img"):
        for attr in ("data-src", "data-original", "data-lazy-src", "src"):
            u = img.get(attr, "")
            if u and not u.startswith("data:") and _valid_img(u):
                if not u.startswith("http"):
                    u = urljoin(SITE_URL, u)
                candidates.append(u)
                break
    if not candidates:
        for u in re.findall(
            r'(https?://[^\s"\'<>]+\.(?:jpe?g|png|webp)(?:\?[^\s"\'<>]*)?)', html, re.I
        ):
            if _valid_img(u):
                candidates.append(u)
    seen: set = set()
    return [u for u in candidates if not (u in seen or seen.add(u))]


def _valid_img(u: str) -> bool:
    return bool(u and u.startswith("http") and not any(x in u for x in _EXCLUDE_IMG))


def _search(session: requests.Session, query: str) -> list[dict]:
    path = f"/s/{quote(query)}"
    html = _fetch_html(session, f"{SITE_URL}{path}")
    return _parse_cards(html) if html else []


def _get_catalog_page(session: requests.Session, path: str, page: int) -> list[dict]:
    urls = (
        [f"{SITE_URL}{path}"]
        if page == 1
        else [
            f"{SITE_URL}{path}?page={page}",
            f"{SITE_URL}{path}/{page}",
            f"{SITE_URL}{path}/page/{page}",
        ]
    )
    for url in urls:
        html = _fetch_html(session, url)
        if html:
            cards = _parse_cards(html)
            if cards:
                return cards
    return []


# ══════════════════════════════════════════════════════════════
#  CLASE PÚBLICA
# ══════════════════════════════════════════════════════════════
class Downloader18mh(BaseDownloader):
    NAME = "18MH  (18mh.org)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    CATALOG_SECTIONS = {
        "hots": "人氣推薦 (Recomendadas)",
        "dayup": "熱門更新 (Populares)",
        "newss": "最新上架 (Recientes)",
        "manga-genre/hanman": "韓漫 (Manhwa)",
    }

    def __init__(self):
        self._sess = _make_session()
        try:
            self._sess.get(SITE_URL + "/", timeout=8)
        except Exception:
            pass

    # ── BaseDownloader ────────────────────────────────────────
    def search(self, query: str) -> list[dict]:
        return _search(self._sess, query)

    def get_catalog(self, max_pages: int = 50) -> list[dict]:
        results, seen = [], set()
        for section in self.CATALOG_SECTIONS:
            page = 1
            while page <= max_pages:
                cards = _get_catalog_page(self._sess, f"/{section}", page)
                if not cards:
                    break
                added = 0
                for c in cards:
                    if c["slug"] not in seen:
                        seen.add(c["slug"])
                        results.append(c)
                        added += 1
                if added == 0:
                    break
                page += 1
        return results

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list, bool]:
        """Carga el catálogo progresivamente: sección a sección, página a página."""
        secs = list(self.CATALOG_SECTIONS.keys())
        key = "18mh_all"
        if getattr(self, "_cat_buf_key", None) != key:
            self._cat_buf = []
            self._cat_buf_key = key
            self._cat_sec_idx = 0
            self._cat_sec_pg = 0
            self._cat_exhausted = False
            self._cat_seen = set()

        start = (page - 1) * page_size
        end = start + page_size

        while len(self._cat_buf) < end and not self._cat_exhausted:
            idx = self._cat_sec_idx
            if idx >= len(secs):
                self._cat_exhausted = True
                break
            self._cat_sec_pg += 1
            cards = _get_catalog_page(self._sess, f"/{secs[idx]}", self._cat_sec_pg)
            if not cards:
                self._cat_sec_idx += 1
                self._cat_sec_pg = 0
            else:
                added = 0
                for c in cards:
                    if c["slug"] not in self._cat_seen:
                        self._cat_seen.add(c["slug"])
                        self._cat_buf.append(c)
                        added += 1
                if added == 0:
                    self._cat_sec_idx += 1
                    self._cat_sec_pg = 0

        chunk = self._cat_buf[start:end]
        has_more = (not self._cat_exhausted) or (end < len(self._cat_buf))
        return chunk, has_more

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        slug = item.get("slug") or item.get("id", "")
        meta = _parse_series_meta(self._sess, slug)
        if not meta:
            return {}, []
        chapters = _get_chapter_list(self._sess, meta.get("mid", ""))
        return meta, chapters

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        url = chapter.get("url", "")
        if not url:
            return []
        return _extract_chapter_images(self._sess, url)

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        return _get_raw(self._sess, url, referer or SITE_URL)

    def get_referer(self, chapter: dict, series: dict) -> str:
        return SITE_URL
