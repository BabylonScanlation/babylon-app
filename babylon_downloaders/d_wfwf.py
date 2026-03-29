"""
d_wfwf.py — wfwf448.com downloader (sin menú)
Dual mode: Webtoon (ing/list/view) y Manhwa (cm/cl/cv).

FIXES DEFINITIVOS:
  - RESTAURADO: Lógica original de categorías (_WEBTOON_CATS) para listar las 6000+ series.
  - Estructura unificada para Webtoons y Manhwas.
  - Extracción de imágenes con filtro estricto (LazyLoad/data-original) para no bajar publicidad.
"""

from __future__ import annotations

import base64
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from bs4 import BeautifulSoup
from common import CFG, BaseDownloader

_BASE_CANDIDATES = [f"https://wfwf{n}.com/" for n in range(448, 510)] + [
    "https://wfwf1.com/",
    "https://wfwf2.com/",
    "https://wfwf3.com/",
    "https://wfwf10.com/",
    "https://wfwf20.com/",
    "https://wfwf30.com/",
    "https://wfwf50.com/",
    "https://wfwf100.com/",
    "https://wfwf200.com/",
    "https://wfwf300.com/",
]
BASE_URL = "https://wfwf448.com/"
TIMEOUT = (12, 20)
RETRY = 1.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
}

_UI_PATHS = ("/images/", "/bann/", "/img/", "/icons/", "/logo", "/thumb")
_CDN_RE = re.compile(
    r"https?://[a-z0-9\-]+\.(?:site|com|net|kr)/[^\s\"'<>]+"
    r"\.(?:jpe?g|png|webp|gif)",
    re.I,
)
_NOISE_RE = re.compile(r"^\d+\s*|하루전|방금전|\d+일전|오늘|\d{4}-\d{2}-\d{2}|\s{2,}")

# RESTAURADO: Tu método original que sí extrae las 6000+ series de ambas plataformas.
_WEBTOON_CATS = [f"?o=n&type1=day&type2={i}" for i in range(1, 8)] + [
    "?o=n&type1=day&type2=10",
    "?o=n&type1=day&type2=recent",
    "?o=n&type1=day&type2=new",
    "?o=n&type1=complete",
    "?o=n&type1=hiatus",
    "?o=n",
]
_MANHWA_CATS = [
    f"?o=n&type1=complete&type2={x}" for x in [10, 11, 12, 13, 14, 15, 16, 20]
] + ["?o=n&type1=complete&type2=recent", "?o=n&type1=hiatus", "?o=n"]

_SITE_KEYWORDS = ("toon=", "wfwf", "lng", "ing", "webtoon", "웹툰", "만화", "manhwa")


def _is_valid_wfwf_response(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _SITE_KEYWORDS)


def _detect_base_url(sess: requests.Session) -> str:
    global BASE_URL

    def _try(candidate: str) -> str:
        try:
            r = sess.get(candidate + "ing", timeout=6)
            if r.status_code == 200 and _is_valid_wfwf_response(r.text):
                return candidate
            r2 = sess.get(candidate, timeout=5)
            if r2.status_code == 200 and _is_valid_wfwf_response(r2.text):
                return candidate
        except Exception:
            pass
        return ""

    priority = _BASE_CANDIDATES[:20]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_try, priority))

    for r in results:
        if r:
            BASE_URL = r
            sess.headers.update({"Referer": BASE_URL})
            return r

    for candidate in _BASE_CANDIDATES[20:]:
        found = _try(candidate)
        if found:
            BASE_URL = found
            sess.headers.update({"Referer": BASE_URL})
            return found
    return BASE_URL


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    _detect_base_url(s)
    return s


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _fetch_html(sess: requests.Session, url: str, retries: int = 3) -> Optional[str]:
    for attempt in range(retries):
        try:
            r = sess.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
        except Exception:
            import time as _time

            _time.sleep(RETRY * (attempt + 1))
    return None


class Mode:
    WEBTOON = "webtoon"
    MANHWA = "manhwa"

    def __init__(self, kind):
        if isinstance(kind, Mode):
            self.kind = kind.kind
        else:
            self.kind = kind

    @property
    def main_path(self) -> str:
        return "ing" if self.kind == self.WEBTOON else "cm"

    def series_url(self, toon_id: str, enc_title: str = "") -> str:
        path = "list" if self.kind == self.WEBTOON else "cl"
        return f"{BASE_URL}{path}?toon={toon_id}"

    def chapter_url(self, toon_id: str, num: int, enc_title: str = "") -> str:
        path = "view" if self.kind == self.WEBTOON else "cv"
        return f"{BASE_URL}{path}?toon={toon_id}&num={num}"

    def chapter_href_re(self, toon_id: str) -> re.Pattern:
        amp = r"(?:&amp;|&|\?)"
        return re.compile(rf"toon={re.escape(str(toon_id))}{amp}num=(\d+)", re.I)


def _mode_from_item(item: dict) -> Mode:
    return Mode(item.get("mode", Mode.WEBTOON))


# ── Catálogo (Usando tu lógica original) ──────────────────────────────────────


def _parse_series_from_html(html: str, mode: Mode) -> list[dict]:
    soup = _soup(html)
    items: list[dict] = []
    seen: set = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "num=" in href:
            continue

        m = re.search(r"[?&]toon=(\d+)", href)
        if not m:
            continue

        toon_id = m.group(1)
        if toon_id in seen:
            continue

        real_mode = mode.kind
        if "cl" in href or "cm" in href or "cv" in href:
            real_mode = Mode.MANHWA
        elif "list" in href or "ing" in href or "view" in href or "end" in href:
            real_mode = Mode.WEBTOON

        title = ""
        img = a.find("img")
        if img and img.get("alt"):
            title = img.get("alt").strip()

        if not title:
            txt_box = a.find(class_="txt")
            if txt_box:
                p_tags = txt_box.find_all("p")
                if p_tags:
                    title = p_tags[0].get_text(strip=True)

        if not title:
            text = a.get_text(" ", strip=True)
            if text and "더 읽기" not in text and len(text) > 1:
                title = text.split("/")[0].strip()

        enc_title = ""
        m_title = re.search(r"[?&]title=([^&\s<>]+)", href)
        if m_title:
            enc_title = m_title.group(1)
            if not title:
                title = urllib.parse.unquote(enc_title)
        else:
            enc_title = urllib.parse.quote(title) if title else ""

        seen.add(toon_id)
        items.append(
            {
                "id": toon_id,
                "toon_id": toon_id,
                "encoded_title": enc_title,
                "title": title or f"Toon {toon_id}",
                "mode": real_mode,
            }
        )
    return items


def _fetch_cat(args: tuple) -> list[dict]:
    sess, url, mode = args
    html = _fetch_html(sess, url)
    return _parse_series_from_html(html, mode) if html else []


def fetch_series_list(
    sess: requests.Session, mode: Mode, workers: int = 10
) -> list[dict]:
    cats = _WEBTOON_CATS if mode.kind == Mode.WEBTOON else _MANHWA_CATS
    main = mode.main_path
    all_urls = [f"{BASE_URL}{main}"] + [f"{BASE_URL}{main}{c}" for c in cats]
    series: list[dict] = []
    seen: set = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for items in pool.map(_fetch_cat, [(sess, u, mode) for u in all_urls]):
            for it in items:
                if it["toon_id"] not in seen:
                    seen.add(it["toon_id"])
                    series.append(it)
    return series


def fetch_full_catalog(sess: requests.Session, workers: int = 10) -> list[dict]:
    mode_wt = Mode(Mode.WEBTOON)
    mode_mh = Mode(Mode.MANHWA)
    cats_wt = [f"{BASE_URL}{mode_wt.main_path}"] + [
        f"{BASE_URL}{mode_wt.main_path}{c}" for c in _WEBTOON_CATS
    ]
    cats_mh = [f"{BASE_URL}{mode_mh.main_path}"] + [
        f"{BASE_URL}{mode_mh.main_path}{c}" for c in _MANHWA_CATS
    ]
    tasks = [(sess, u, mode_wt) for u in cats_wt] + [
        (sess, u, mode_mh) for u in cats_mh
    ]

    all_series: list[dict] = []
    seen: set = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for items in pool.map(_fetch_cat, tasks):
            for it in items:
                key = f"{it['mode']}_{it['toon_id']}"
                if key not in seen:
                    seen.add(key)
                    all_series.append(it)

    all_series.sort(key=lambda s: (s["mode"], s["title"].lower()))
    return all_series


def _parse_series_page(
    html: str, toon_id: str, enc_title: str, mode: Mode
) -> tuple[str, list[dict]]:
    soup = _soup(html)
    title = urllib.parse.unquote(enc_title) if enc_title else ""

    for sel in ["h1", ".toon-title", ".series-title", "#toon_title", ".title", "h2"]:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True) and len(node.get_text(strip=True)) > 1:
            title = node.get_text(strip=True)
            break

    chap_re = mode.chapter_href_re(toon_id)
    seen_nums: set = set()
    chapters: list[dict] = []

    def _add_from_html(html_text: str) -> None:
        s = _soup(html_text)
        for a in s.find_all("a", href=True):
            href = a["href"]
            mm = chap_re.search(href)
            if not mm:
                continue

            num = int(mm.group(1))
            if num in seen_nums or num == 0:
                continue
            seen_nums.add(num)

            subject_div = a.find(class_="subject")
            if subject_div:
                raw_text = subject_div.get_text(" ", strip=True)
            else:
                raw_text = a.get_text(" ", strip=True)

            chap_title = _NOISE_RE.sub(" ", raw_text).strip() or f"Cap {num}"

            full_url = ""
            if href.startswith("http"):
                full_url = href
            elif not href.startswith("javascript"):
                full_url = urllib.parse.urljoin(BASE_URL, href)

            chapters.append(
                {"id": str(num), "num": num, "title": chap_title, "url": full_url}
            )

    _add_from_html(html)

    more_pat = re.compile(r"[?&](?:p|page)=(\d+)", re.I)
    max_page = 1
    for a in soup.find_all("a", href=True):
        mm = more_pat.search(a["href"])
        if mm:
            max_page = max(max_page, int(mm.group(1)))

    if max_page > 1:
        for page_num in range(2, max_page + 1):
            sep = "&" if "?" in mode.series_url(toon_id) else "?"
            page_url = mode.series_url(toon_id) + f"{sep}p={page_num}"
            page_html = _fetch_html(_current_sess, page_url)
            if page_html:
                _add_from_html(page_html)

    chapters.sort(key=lambda c: c["num"], reverse=True)
    return title, chapters


_current_sess: Optional[requests.Session] = None

# ── Images ────────────────────────────────────────────────────────────────────


def _extract_images(html: str) -> list[str]:
    soup = _soup(html)
    candidates = []
    VALID = (".jpg", ".jpeg", ".png", ".webp", ".gif")

    view_container = (
        soup.select_one(".image-view")
        or soup.select_one("#toon_img")
        or soup.select_one(".view-wrap")
    )
    img_tags = (
        view_container.find_all("img") if view_container else soup.find_all("img")
    )

    for img in img_tags:
        src = img.get("data-original") or img.get("data-src") or img.get("src") or ""
        src = src.strip()
        if not src:
            continue

        if not view_container:
            classes = img.get("class", [])
            is_chapter_img = (
                img.has_attr("data-original")
                or img.has_attr("data-src")
                or "v-img" in classes
                or "lazyload" in classes
            )
            if not is_chapter_img:
                continue

        if (
            src.startswith("http")
            and not any(p in src for p in _UI_PATHS)
            and any(src.lower().split("?")[0].endswith(e) for e in VALID)
        ):
            candidates.append(src)

    if candidates:
        return list(dict.fromkeys(candidates))

    return []


# ══════════════════════════════════════════════════════════════════════════════


class DownloaderWfwf(BaseDownloader):
    NAME = "WFWF  (wfwf448.com)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()
        global _current_sess
        _current_sess = self._sess

    def search(self, query: str) -> list[dict]:
        q = query.lower().strip()
        if not q:
            return []

        direct = self._direct_search(q)
        if direct:
            return direct

        if not hasattr(self, "_full_catalog") or not self._full_catalog:
            print(f"  Cargando catálogo para búsqueda…", end=" ", flush=True)
            self._full_catalog = fetch_full_catalog(self._sess)
            print(f"{len(self._full_catalog)} series")

        results = []
        for s in self._full_catalog:
            title_low = s.get("title", "").lower()
            enc_low = urllib.parse.unquote(s.get("encoded_title", "")).lower()
            if q in title_low or q in enc_low:
                results.append(s)
        return results

    def _direct_search(self, query: str) -> list[dict]:
        results: list[dict] = []
        seen: set = set()
        for path in [
            f"search?s={urllib.parse.quote(query)}",
            f"?s={urllib.parse.quote(query)}",
            f"search?q={urllib.parse.quote(query)}",
        ]:
            try:
                r = self._sess.get(BASE_URL + path, timeout=10)
                if r.status_code != 200:
                    continue
                html = r.text
                for mode in [Mode(Mode.WEBTOON), Mode(Mode.MANHWA)]:
                    for it in _parse_series_from_html(html, mode):
                        key = f"{it['mode']}_{it['toon_id']}"
                        if key not in seen:
                            seen.add(key)
                            results.append(it)
                if results:
                    return results
            except Exception:
                pass
        return []

    def get_catalog(self) -> list[dict]:
        return fetch_full_catalog(self._sess)

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        toon_id = item.get("toon_id", item.get("id", ""))
        enc_title = item.get("encoded_title", "")
        mode = _mode_from_item(item)

        html = _fetch_html(self._sess, mode.series_url(toon_id, enc_title))
        if not html:
            return {}, []

        title, chapters = _parse_series_page(html, toon_id, enc_title, mode)
        meta = {
            "id": toon_id,
            "slug": toon_id,
            "title": title,
            "toon_id": toon_id,
            "encoded_title": enc_title,
            "mode": mode.kind,
        }
        return meta, chapters

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        url = chapter.get("url")
        if not url:
            toon_id = series.get("toon_id", series.get("id", ""))
            enc_title = series.get("encoded_title", "")
            num = chapter.get("num", int(chapter.get("id", 0)))
            mode = _mode_from_item(series)
            url = mode.chapter_url(toon_id, num, enc_title)

        html = _fetch_html(self._sess, url)
        return _extract_images(html) if html else []

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        import time as _time

        for attempt in range(3):
            try:
                r = self._sess.get(url, timeout=TIMEOUT)
                if r.status_code == 200 and r.content:
                    return r.content
            except Exception:
                _time.sleep(RETRY * (attempt + 1))
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        return BASE_URL
