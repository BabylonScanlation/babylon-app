"""
d_manhuagui.py — manhuagui.com downloader (sin menú)
p.a.c.k.e.r + LZString para extraer lista de imágenes.
"""

from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from common import CFG, BaseDownloader

BASE = "https://www.manhuagui.com"
_BASE_ALTS = [
    "https://www.manhuagui.com",
    "https://manhuagui.com",
    "https://tw.manhuagui.com",
    "https://www.manhuashe.net",
]
IMG_HOST = "https://i.hamreus.com"
TIMEOUT = (12, 30)
REQUEST_DELAY = 0.5
MAX_RESULTS = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE + "/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


# ── LZString ──────────────────────────────────────────────────────────────────

_B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_B64_MAP = {ch: i for i, ch in enumerate(_B64_CHARS)}


def lzstring_decompress_base64(compressed: str) -> str:
    if not compressed:
        return ""
    safe = lambda i: _B64_MAP.get(compressed[i], 0) if i < len(compressed) else 0
    dv, dp, di = safe(0), 32, 1
    result: list[str] = []
    dictionary: list = list(range(3))
    enlargeIn, dictSize, numBits = 4, 4, 3

    def rb(maxpower: int) -> int:
        nonlocal dv, dp, di
        bits, p = 0, 1
        while p != maxpower:
            resb = dv & dp
            dp >>= 1
            if dp == 0:
                dp = 32
                dv = safe(di)
                di += 1
            bits |= (1 if resb > 0 else 0) * p
            p <<= 1
        return bits

    nxt = rb(4)
    c = chr(rb(256) if nxt == 0 else rb(65536) if nxt == 1 else 0)
    if nxt not in (0, 1):
        return ""
    dictionary.append(c)
    w = c
    result.append(c)

    while True:
        if di > len(compressed):
            return ""
        c = rb(1 << numBits)
        if c == 0:
            dictionary.append(chr(rb(256)))
            c = dictSize
            dictSize += 1
            enlargeIn -= 1
        elif c == 1:
            dictionary.append(chr(rb(65536)))
            c = dictSize
            dictSize += 1
            enlargeIn -= 1
        elif c == 2:
            return "".join(result)
        if enlargeIn == 0:
            enlargeIn = 1 << numBits
            numBits += 1
        entry = (
            dictionary[c]
            if c < len(dictionary)
            else w + w[0]
            if c == dictSize
            else None
        )
        if entry is None:
            return "".join(result)
        result.append(entry)
        dictionary.append(w + entry[0])
        dictSize += 1
        enlargeIn -= 1
        if enlargeIn == 0:
            enlargeIn = 1 << numBits
            numBits += 1
        w = entry


# ── p.a.c.k.e.r unpacker ─────────────────────────────────────────────────────


class UnpackingError(Exception):
    pass


class _Unbaser:
    ALPHABET = {
        62: "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        95: (
            " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
        ),
    }

    def __init__(self, base):
        self.base = base
        if 2 <= base <= 36:
            self.unbase = lambda s: int(s, base)
        else:
            alphabet = self.ALPHABET.get(base, self.ALPHABET[62])
            self.dictionary = {c: i for i, c in enumerate(alphabet)}
            self.unbase = self._dictunbaser

    def __call__(self, s):
        return self.unbase(s)

    def _dictunbaser(self, s):
        ret = 0
        for idx, ch in enumerate(s[::-1]):
            ret += (self.base**idx) * self.dictionary.get(ch, 0)
        return ret


def _detect_packer(source: str) -> bool:
    return bool(
        re.search(
            r"(eval|window\['eval'\])\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,",
            source,
            re.I,
        )
    )


def _unpack_packer(source: str) -> str:
    source = source.replace('window["\\x65\\x76\\x61\\x6c"]', "eval")
    m = re.search(
        r"}\s*\(\s*'((?:\\'|[^'])*)'\s*,\s*(\d+|\[\])\s*,\s*(\d+)\s*,"
        r"\s*'((?:\\'|[^'])*)'[^,]*?,\s*0\s*,\s*\{\}\s*\)\)",
        source,
        re.I,
    )
    if not m:
        raise UnpackingError("Could not parse p.a.c.k.e.r.")
    a = list(m.groups())
    if a[1] == "[]":
        a[1] = 62
    payload, radix, count = a[0], int(a[1]), int(a[2])
    symtab_str = a[3]
    if "|" not in symtab_str and len(symtab_str) > 20:
        dec = lzstring_decompress_base64(symtab_str)
        if dec:
            symtab_str = dec
    symtab = symtab_str.split("|")
    if count != len(symtab):
        raise UnpackingError("Malformed symtab")
    unbase = _Unbaser(radix)

    def lookup(mm: re.Match) -> str:
        word = mm.group(0)
        try:
            val = unbase(word)
            if val < len(symtab) and symtab[val]:
                return symtab[val]
        except Exception:
            pass
        return word

    payload = payload.replace("\\\\", "\\").replace("\\'", "'")
    return re.sub(r"\b[0-9a-zA-Z]+\b", lookup, payload)


# ── HTTP helpers ──────────────────────────────────────────────────────────────


def _make_session() -> requests.Session:
    global BASE
    s = requests.Session()
    s.headers.update(HEADERS)
    # Find an accessible domain
    for alt in _BASE_ALTS:
        try:
            s.headers.update({"Referer": alt + "/"})
            r = s.get(alt + "/", timeout=8)
            if r.status_code in (200, 301, 302):
                BASE = alt
                break
        except Exception:
            pass
    return s


def _get(
    sess: requests.Session, url: str, is_img: bool = False, retries: int = 3
) -> Optional[bytes]:
    for attempt in range(retries):
        try:
            r = sess.get(url, timeout=20 if is_img else 12)
            if r.status_code == 200:
                return r.content
            if r.status_code in (403, 404):
                return None
        except Exception:
            pass
        time.sleep(1 + attempt)
    return None


def _soup_url(sess: requests.Session, url: str) -> Optional[BeautifulSoup]:
    time.sleep(REQUEST_DELAY)
    raw = _get(sess, url)
    if raw is None:
        return None
    return BeautifulSoup(raw.decode("utf-8", errors="replace"), "lxml")


# ── Catalog / search ──────────────────────────────────────────────────────────


def _build_list_url(region="", genre="", audience="", status="", page: int = 1) -> str:
    parts = [p for p in [region, genre, audience, status] if p]
    slug = "_".join(parts) if parts else ""
    base = f"/list/{slug}/" if slug else "/list/"
    return f"{BASE}{base}" if page == 1 else f"{BASE}{base}index_p{page}.html"


def _browse_page(
    sess: requests.Session, page: int = 1, region="", genre="", audience="", status=""
) -> tuple[list[dict], int]:
    url = _build_list_url(region, genre, audience, status, page)
    soup = _soup_url(sess, url)
    if soup is None:
        return [], 0
    series: list[dict] = []
    seen: set = set()
    # Try multiple selector strategies (site may have been redesigned)
    SELECTORS = [
        "#contList li",
        "div.book-result li",
        "ul.book-list li",
        "ul.list-comic li",
        ".comic-list li",
        ".manga-list li",
        "li.book-item",
    ]
    lis: list = []
    for sel in SELECTORS:
        lis = soup.select(sel)
        if lis:
            break
    # Fallback: find all <a href="/comic/ID/"> directly
    if not lis:
        for a in soup.find_all("a", href=re.compile(r"^/comic/\d+/?$")):
            m = re.search(r"/comic/(\d+)/", a["href"])
            if not m:
                continue
            cid = m.group(1)
            if cid in seen:
                continue
            seen.add(cid)
            title = a.get("title") or a.get_text(strip=True)
            if title:
                series.append({"id": cid, "slug": cid, "title": title[:60]})
    else:
        for li in lis:
            a = li.find("a", href=re.compile(r"/comic/\d+/"))
            if not a:
                continue
            m = re.search(r"/comic/(\d+)/", a["href"])
            if not m:
                continue
            cid = m.group(1)
            if cid in seen:
                continue
            seen.add(cid)
            title = a.get("title") or a.get_text(strip=True)
            series.append({"id": cid, "slug": cid, "title": title[:60]})
    total = page
    for a in soup.select("a[href*='_p'], a[href*='page=']"):
        m = re.search(r"_p(\d+)\.html|page=(\d+)", a.get("href", ""))
        if m:
            n = int(m.group(1) or m.group(2))
            total = max(total, n)
    return series, total


def _load_all_pages(
    sess: requests.Session,
    region="",
    genre="",
    audience="",
    status="",
    workers: int = 8,
) -> list[dict]:
    first, total = _browse_page(sess, 1, region, genre, audience, status)
    if not first:
        return []
    all_series: list[dict] = []
    seen: set = set()
    for s in first:
        if s["id"] not in seen:
            seen.add(s["id"])
            all_series.append(s)
    if total <= 1:
        return all_series

    def _fetch(pg: int) -> list[dict]:
        items, _ = _browse_page(sess, pg, region, genre, audience, status)
        return items

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_fetch, pg): pg for pg in range(2, total + 1)}
        for fut in as_completed(futs):
            for s in fut.result():
                if s["id"] not in seen:
                    seen.add(s["id"])
                    all_series.append(s)
    return all_series


def _search(
    sess: requests.Session, query: str, page: int = 1
) -> tuple[list[dict], int]:
    url = (
        f"{BASE}/s/{quote(query)}.html"
        if page == 1
        else f"{BASE}/s/{quote(query)}_p{page}.html"
    )
    soup = _soup_url(sess, url)
    if soup is None:
        return [], 0
    results: list[dict] = []
    seen: set = set()
    comic_links: list = []
    for sel in [
        "#contList li a[href*='/comic/']",
        "div.book-result li a[href*='/comic/']",
        "ul.list-comic li a[href*='/comic/']",
        ".list-comic a[href*='/comic/']",
        "li a[href*='/comic/']",
        "a[href*='/comic/']",
    ]:
        comic_links = soup.select(sel)
        if comic_links:
            break
    for a in comic_links:
        m = re.search(r"/comic/(\d+)/", a.get("href", ""))
        if not m:
            continue
        cid = m.group(1)
        if cid in seen:
            continue
        seen.add(cid)
        title = (a.get("title") or a.get_text(strip=True) or "").strip()
        if title:
            results.append({"id": cid, "slug": cid, "title": title[:60]})
    total = page
    for a in soup.select("a[href*='_p']"):
        m = re.search(r"_p(\d+)\.html", a["href"])
        if m:
            total = max(total, int(m.group(1)))
    return results, total


# ── Comic info ────────────────────────────────────────────────────────────────


def _get_comic(sess: requests.Session, comic_id: str) -> dict:
    soup = _soup_url(sess, f"{BASE}/comic/{comic_id}/")
    if soup is None:
        return {}
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        tt = soup.find("title")
        if tt:
            title = re.sub(r"漫画|在线看|看漫画.*$", "", tt.string or "").strip()

    chapters: list[dict] = []
    vs_tag = soup.find("input", id="__VIEWSTATE")
    if vs_tag and vs_tag.get("value"):
        vs_html = lzstring_decompress_base64(vs_tag["value"])
        if vs_html:
            vs_soup = BeautifulSoup(vs_html, "lxml")
            chapters = _parse_chapters(vs_soup, comic_id)
    if not chapters:
        chapters = _parse_chapters(soup, comic_id)

    return {
        "id": comic_id,
        "slug": comic_id,
        "title": title or f"Comic {comic_id}",
        "chapters": chapters,
    }


def _parse_chapters(soup: BeautifulSoup, comic_id: str) -> list[dict]:
    chapters: list[dict] = []
    seen: set = set()
    for section in soup.select(
        ".chapter-list, ul.chapter-list, .chapter_list, #chapterList"
    ) or [soup]:
        for a in section.find_all(
            "a", href=re.compile(rf"/comic/{comic_id}/\d+\.html")
        ):
            m = re.search(rf"/comic/{comic_id}/(\d+)\.html", a["href"])
            if not m:
                continue
            chid = m.group(1)
            if chid in seen:
                continue
            seen.add(chid)
            title = a.get("title") or a.get_text(strip=True)
            chapters.append(
                {
                    "id": chid,
                    "slug": chid,
                    "title": title or f"Cap {len(chapters) + 1}",
                    "url": f"{BASE}/comic/{comic_id}/{chid}.html",
                }
            )
    chapters.reverse()
    return chapters


# ── Images ────────────────────────────────────────────────────────────────────


def _get_images(sess: requests.Session, comic_id: str, chapter_id: str) -> list[str]:
    url = f"{BASE}/comic/{comic_id}/{chapter_id}.html"
    raw = _get(sess, url)
    if raw is None:
        return []
    html = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    packed = None
    for script in soup.find_all("script"):
        if script.string:
            content = script.string.strip().replace(
                'window["\\x65\\x76\\x61\\x6c"]', "eval"
            )
            if _detect_packer(content):
                packed = content
                break
    if not packed:
        return []
    try:
        unpacked = _unpack_packer(packed)
    except Exception:
        return []

    files, path, e_val, m_val = [], "", "", ""
    m_json = re.search(r'(\{.*?"files".*?\})', unpacked, re.DOTALL | re.I)
    if m_json:
        try:
            data = json.loads(m_json.group(1))
            files = data.get("files", [])
            path = data.get("path", "")
            sl = data.get("sl", {})
            e_val = sl.get("e", "")
            m_val = sl.get("m", "")
        except Exception:
            pass
    if not files:
        mf = re.search(r'"files"\s*:\s*\[(.*?)\]', unpacked, re.I)
        if mf:
            files = [f.strip("\"' ") for f in mf.group(1).split(",")]
        mp = re.search(r'"path"\s*:\s*"([^"]+)"', unpacked, re.I)
        if mp:
            path = mp.group(1)
        me = re.search(r'"e"\s*:\s*(\d+|"[^"]+")', unpacked, re.I)
        if me:
            e_val = me.group(1).replace('"', "")
        mm = re.search(r'"m"\s*:\s*"([^"]+)"', unpacked, re.I)
        if mm:
            m_val = mm.group(1)

    urls = []
    for fname in files:
        if not fname:
            continue
        img_url = f"{IMG_HOST}{path}{fname}"
        if e_val or m_val:
            img_url += f"?e={e_val}&m={m_val}"
        urls.append(img_url)
    return urls


# ══════════════════════════════════════════════════════════════
#  CLASE PÚBLICA
# ══════════════════════════════════════════════════════════════
class DownloaderManhuagui(BaseDownloader):
    NAME = "MANHUAGUI  (manhuagui.com)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()

    def search(self, query: str) -> list[dict]:
        results, _ = _search(self._sess, query)
        return results

    def get_catalog(
        self, region="", genre="", audience="", status="", max_pages: int = 50
    ) -> list[dict]:
        """Carga hasta max_pages páginas (cap de seguridad). Usar get_catalog_page para lazy."""
        first, total = _browse_page(self._sess, 1, region, genre, audience, status)
        if not first:
            return []
        results = list(first)
        seen = {s["id"] for s in results}
        limit = min(total, max_pages)

        def _fetch(pg):
            items, _ = _browse_page(self._sess, pg, region, genre, audience, status)
            return items

        with ThreadPoolExecutor(max_workers=4) as pool:
            for batch in pool.map(_fetch, range(2, limit + 1)):
                for s in batch:
                    if s["id"] not in seen:
                        seen.add(s["id"])
                        results.append(s)
        return results

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list, bool]:
        """Paginación real en el servidor: 1 request por página del menú."""
        region = kwargs.get("region", "")
        genre = kwargs.get("genre", "")
        audience = kwargs.get("audience", "")
        status = kwargs.get("status", "")
        key = f"{region}|{genre}|{audience}|{status}"
        if getattr(self, "_cat_buf_key", None) != key:
            self._cat_buf = []
            self._cat_buf_key = key
            self._cat_srv_page = 0
            self._cat_total = None
            self._cat_exhausted = False
            self._cat_seen = set()

        start = (page - 1) * page_size
        end = start + page_size

        while len(self._cat_buf) < end and not self._cat_exhausted:
            self._cat_srv_page += 1
            items, total = _browse_page(
                self._sess, self._cat_srv_page, region, genre, audience, status
            )
            if self._cat_total is None:
                self._cat_total = total
            if not items:
                self._cat_exhausted = True
                break
            added = 0
            for s in items:
                if s["id"] not in self._cat_seen:
                    self._cat_seen.add(s["id"])
                    self._cat_buf.append(s)
                    added += 1
            if added == 0 or self._cat_srv_page >= (self._cat_total or 1):
                self._cat_exhausted = True

        chunk = self._cat_buf[start:end]
        has_more = (not self._cat_exhausted) or (end < len(self._cat_buf))
        return chunk, has_more

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        cid = item.get("id", "")
        data = _get_comic(self._sess, cid)
        if not data:
            return {}, []
        chapters = data.pop("chapters", [])
        return data, chapters

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        comic_id = series.get("id", "")
        chapter_id = chapter.get("id", "")
        return _get_images(self._sess, comic_id, chapter_id)

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        hdrs = {"Referer": referer or url, "User-Agent": HEADERS["User-Agent"]}
        for attempt in range(3):
            try:
                r = self._sess.get(url, timeout=20, headers=hdrs)
                if r.status_code == 200:
                    return r.content
            except Exception:
                pass
            time.sleep(1 + attempt)
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        cid = series.get("id", "")
        return f"{BASE}/comic/{cid}/"
