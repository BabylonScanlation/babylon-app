"""
d_baozimh.py — baozimh.org / baozimh.com downloader (sin menú)
Metadatos → baozimh.org  |  Catálogo API → baozimh.com  |  Imágenes → mirrors
"""

from __future__ import annotations

import base64
import json
import re
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from common import CFG, BaseDownloader

SITE_ORG = "https://baozimh.org"
APIKK_HOST = "https://v2.apikk.top"

socket.setdefaulttimeout(8)
COM_MIRRORS = [
    "https://www.webmota.com",
    "https://cn.webmota.com",
    "https://tw.webmota.com",
    "https://www.kukuc.co",
    "https://cn.kukuc.co",
    "https://www.baozimh.com",
    "https://baozimh.com",
    "https://www.twmanga.com",
    "https://www.czmanga.com",
]
TIMEOUT = (8, 20)
RETRY_DELAY = 1.5
REQUEST_DELAY = 0.3

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "text/html,application/xhtml+xml,application/xml;"
    "q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
}

_SKIP_TITLES = {
    "開始閱讀",
    "开始阅读",
    "收藏",
    "立即閱讀",
    "立即阅读",
    "查看所有章節",
    "查看所有章节",
}
_NAV_HREFS = re.compile(
    r"/(hots|dayup|newss|manga|donate|bookmark|classify|list|app_gb|user/bookshelf)"
)
_BAD_SLUGS = {
    "sitemap",
    "classify",
    "hots",
    "dayup",
    "newss",
    "bookmark",
    "list",
    "app_gb",
    "donate",
    "privacy",
    "dmca",
    "user",
    "search",
    "comic",
    "manga",
    "about",
    "contact",
    "index",
}
_EXCLUDE_IMG = ("/cover/", "/ui/", "logo", "icon")
_IMG_RE = re.compile(
    r'(https?://[^\s"\'<>]+\.(?:jpe?g|png|webp)(?:\?[^\s"\'<>]*)?)', re.I
)


# ── Sessions ──────────────────────────────────────────────────────────────────


def _make_session(base_url: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(_BASE_HEADERS)
    s.headers["Referer"] = base_url + "/"
    try:
        s.get(base_url + "/", timeout=8)
    except Exception:
        pass
    return s


def _find_active_mirror(sess_com: requests.Session) -> str:
    """Try mirrors in priority order; return first that serves comic pages."""

    s = requests.Session()
    s.headers.update(_BASE_HEADERS)

    for mirror in COM_MIRRORS:
        s.headers["Referer"] = mirror + "/"
        try:
            r = s.get(mirror + "/", timeout=5)
            if r.status_code not in (200, 301, 302):
                continue
            if r.status_code == 200 and len(r.content) > 500:
                sess_com.headers.update(s.headers)
                return mirror
        except Exception:
            continue
    return COM_MIRRORS[0]  # fallback to first


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


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ── Catalog API ───────────────────────────────────────────────────────────────


def _fetch_api_page(
    sess_com: requests.Session,
    mirror: str,
    type_: str,
    region: str,
    state: str,
    page: int,
) -> list[dict]:
    url = (
        f"{mirror}/api/bzmhq/amp_comic_list"
        f"?type={type_}&region={region}&state={state}"
        f"&filter=*&page={page}&limit=36&language=tw"
    )
    try:
        hdrs = {"Accept": "application/json, */*", "Referer": mirror + "/classify"}
        r = sess_com.get(url, timeout=(10, 30), headers=hdrs)
        if r.status_code != 200:
            return []
        data = r.json()
        items = (
            data
            if isinstance(data, list)
            else data.get("items") or data.get("list") or data.get("data") or []
        )
        out = []
        for c in items:
            cid = c.get("comic_id") or c.get("id") or c.get("slug") or ""
            name = c.get("name") or c.get("title") or cid
            if cid:
                out.append({"id": str(cid), "slug": str(cid), "title": str(name)})
        return out
    except Exception:
        return []


def fetch_catalog_api(
    sess_com: requests.Session,
    mirror: str,
    type_: str = "all",
    region: str = "all",
    state: str = "all",
    workers: int = 12,
) -> list[dict]:
    first = _fetch_api_page(sess_com, mirror, type_, region, state, 1)
    if not first:
        return []
    all_items: list[dict] = []
    seen: set = set()

    def _add(items):
        for it in items:
            if it["id"] not in seen:
                seen.add(it["id"])
                all_items.append(it)

    _add(first)
    if len(first) < 36:
        return all_items

    page = 2
    empty = False
    while not empty:
        batch = list(range(page, page + workers))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(
                    _fetch_api_page, sess_com, mirror, type_, region, state, pg
                ): pg
                for pg in batch
            }
            for fut in as_completed(futs):
                items = fut.result()
                if not items:
                    empty = True
                else:
                    _add(items)
        page += workers
    return all_items


def _fetch_chapters_apikk(
    sess_org: requests.Session, org_slug: str
) -> tuple[Optional[str], list[dict]]:
    """Chapters via la API nativa del frontend (baozimh.org) o el proxy apikk.

    Devuelve (mid, chapters). El id REAL del capítulo es `ch.id` (p.ej. "747800");
    `attributes.slug` solo es el orden (0,1,2…) y NO sirve para URLs.
    """
    mid = None
    try:
        raw = _get_raw(sess_org, f"{SITE_ORG}/manga/{org_slug}", SITE_ORG, retries=2)
        if raw:
            html = raw.decode("utf-8", errors="replace")
            m = re.search(r'data-mid="(\d+)"', html)
            if m:
                mid = m.group(1)
    except Exception:
        pass
    if not mid:
        return None, []

    payload = []
    for base in (f"{SITE_ORG}/api/manga/get", f"{APIKK_HOST}/api/manga/get"):
        raw = _get_raw(sess_org, f"{base}?mid={mid}&mode=all", SITE_ORG, retries=2)
        if not raw:
            continue
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
            chapters_raw = (data.get("data") or {}).get("chapters") or []
            if chapters_raw:
                payload = chapters_raw
                break
        except Exception:
            continue
    if not payload:
        return mid, []

    chapters: list[dict] = []
    seen: set = set()
    for ch in payload:
        cid = str(ch.get("id") or "")
        attrs = ch.get("attributes") or {}
        if not cid:
            continue
        if cid in seen:
            continue
        seen.add(cid)
        chapters.append(
            {
                "id": cid,
                "slug": cid,
                "key": cid,
                "mid": mid,
                "title": str(attrs.get("title") or cid),
            }
        )
    return mid, chapters


# ── Imágenes (obfuscación J7r del reader apikk) ───────────────────────────────

_IMG_STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_IMG_CUSTOM = "_-9876543210abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_IMG_PREFIX, _IMG_SUFFIX, _IMG_M1, _IMG_M2 = "J7r", "nQ", "kD", "W4s"


def _b64_decode_std(s: str) -> str:
    t = s.replace("-", "+").replace("_", "/")
    t += "=" * (-len(t) % 4)
    return base64.b64decode(t).decode("utf-8", "replace")


def _decode_apikk_images(blob: str) -> list[str]:
    """Revierte el transform del decoder cliente (sin clave):
    quitar J7r..nQ → trocear/reescribir segmentos → invertir bloques de 7 →
    re-mapear alfabeto custom → base64url → JSON [{order, url}, …]."""
    G = 7
    body = blob[len(_IMG_PREFIX):len(blob) - len(_IMG_SUFFIX)]
    pl = len(body) - len(_IMG_M1) - len(_IMG_M2)
    a = pl // 3
    b = (pl - a) // 2
    c = pl - a - b
    p1 = body[0:b]
    p2 = body[b + len(_IMG_M1):b + len(_IMG_M1) + c]
    p3 = body[b + len(_IMG_M1) + c + len(_IMG_M2):]
    reordered = p3 + p1 + p2
    sb = "".join(
        (reordered[i:i + G][::-1] if (i // G) % 2 == 1 else reordered[i:i + G])
        for i in range(0, len(reordered), G)
    )
    remapped = "".join(
        _IMG_STD[_IMG_CUSTOM.index(ch)] if ch in _IMG_CUSTOM else ch for ch in sb
    )
    try:
        arr = json.loads(_b64_decode_std(remapped))
    except Exception:
        return []
    out: list[str] = []
    for item in arr:
        if isinstance(item, dict):
            u = item.get("url")
        elif isinstance(item, str):
            u = item
        else:
            u = ""
        if u and u not in out:
            out.append(u)
    return out


def _fetch_chapter_images_apikk(
    sess_org: requests.Session, mid: str, cid: str
) -> list[str]:
    """Páginas del capítulo vía la API oficial del reader (apikk) + decode."""
    for host in (APIKK_HOST, "https://api-get-v3.mgsearcher.com"):
        raw = _get_raw(
            sess_org, f"{host}/api/v2/chapter/getinfo?m={mid}&c={cid}", SITE_ORG, retries=2
        )
        if not raw:
            continue
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
            info = (data.get("data") or {}).get("info") or {}
            blob = info.get("images")
            if isinstance(blob, dict):
                blob = blob.get("images")
            if not blob:
                continue
            rel = _decode_apikk_images(blob)
            if not rel:
                continue
            line = info.get("line")
            cdn = f"https://c-nd{line}-1.6wm.top" if line else "https://c-nd3-1.6wm.top"
            return [u if u.startswith("http") else cdn + u for u in rel]
        except Exception:
            continue
    return []


# ── Search (baozimh.org) ──────────────────────────────────────────────────────


def _parse_org_cards(html: str) -> list[dict]:
    soup = _soup(html)
    results = []
    seen: set = set()
    for a in soup.find_all("a", href=re.compile(r"^/manga/[^/]+$")):
        m = re.match(r"^/manga/([^/]+)$", a.get("href", ""))
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        h3 = a.find(["h3", "h4", "p"])
        img = a.find("img")
        title = (
            h3.get_text(strip=True)
            if h3
            else (img.get("alt", "") if img else slug) or slug
        )
        results.append({"id": slug, "slug": slug, "title": title})
    return results


def _search_org(sess_org: requests.Session, query: str) -> list[dict]:
    url = f"{SITE_ORG}/s?q={requests.utils.quote(query)}"
    raw = _get_raw(sess_org, url, SITE_ORG)
    return _parse_org_cards(raw.decode("utf-8", errors="replace")) if raw else []


# ── Series meta ───────────────────────────────────────────────────────────────


def _parse_series_meta_org(sess_org: requests.Session, slug: str) -> Optional[dict]:
    raw = _get_raw(sess_org, f"{SITE_ORG}/manga/{slug}", SITE_ORG)
    if not raw:
        return None
    html = raw.decode("utf-8", errors="replace")
    soup = _soup(html)
    title = slug
    for node in soup.find_all("h1"):
        t = node.get_text(strip=True)
        if t and "包子" not in t and len(t) > 1:
            title = t
            break
    title = re.sub(r"\s*(完結|連載中|连载中|完结)\s*$", "", title).strip()
    return {"id": slug, "slug": slug, "title": title}


# ── Chapters ──────────────────────────────────────────────────────────────────


def _resolve_com_slug(
    sess_com: requests.Session, mirror: str, org_slug: str, title: str = ""
) -> str:
    # Try direct match
    raw = _get_raw(sess_com, f"{mirror}/comic/{org_slug}", mirror + "/", retries=1)
    if raw and f"comic_id={org_slug}" in raw.decode("utf-8", errors="replace"):
        return org_slug
    # Fallback: use org_slug as-is
    return org_slug


def _parse_com_chapters(html: str, slug: str) -> list[dict]:
    soup = _soup(html)
    chapters = []
    seen: set = set()
    for a in soup.find_all("a", href=re.compile(r"page_direct")):
        href = a.get("href", "")
        if _NAV_HREFS.search(href):
            continue
        qs = parse_qs(urlparse(href).query)
        comic_id = qs.get("comic_id", [""])[0]
        ss = qs.get("section_slot", ["0"])[0]
        cs = qs.get("chapter_slot", ["-1"])[0]
        if cs == "-1":
            continue
        title = a.get_text(strip=True)
        if not title or title in _SKIP_TITLES:
            continue
        key = f"{ss}_{cs}"
        if key in seen:
            continue
        seen.add(key)
        chapters.append(
            {
                "id": key,
                "title": title,
                "section_slot": ss,
                "chapter_slot": cs,
                "key": key,
            }
        )

    def _sk(c):
        try:
            return (int(c["section_slot"]), int(c["chapter_slot"]))
        except ValueError:
            return (0, 0)

    chapters.sort(key=_sk)
    return chapters


def _fetch_chapters_api(
    sess_com: requests.Session, mirror: str, com_slug: str
) -> list[dict]:
    """Fallback: fetch chapter list from baozimh JSON API."""
    try:
        url = (
            f"{mirror}/api/bzmhq/amp_comic_chapter_list?comic_id={com_slug}&language=tw"
        )
        r = sess_com.get(
            url,
            timeout=10,
            headers={"Accept": "application/json", "Referer": mirror + "/"},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        raw_chapters = (
            data
            if isinstance(data, list)
            else data.get("list") or data.get("chapters") or data.get("data") or []
        )
        chapters = []
        seen: set = set()
        for ch in raw_chapters:
            ss = str(ch.get("section_slot", ch.get("ss", "0")))
            cs = str(ch.get("chapter_slot", ch.get("cs", ch.get("id", "-1"))))
            if cs == "-1":
                continue
            key = f"{ss}_{cs}"
            title = ch.get("chapter_name") or ch.get("name") or ch.get("title") or key
            if key in seen or not title or title in _SKIP_TITLES:
                continue
            seen.add(key)
            chapters.append(
                {
                    "id": key,
                    "title": str(title),
                    "section_slot": ss,
                    "chapter_slot": cs,
                    "key": key,
                }
            )

        def _sk(c):
            try:
                return (int(c["section_slot"]), int(c["chapter_slot"]))
            except:
                return (0, 0)

        chapters.sort(key=_sk)
        return chapters
    except Exception:
        return []


def _get_chapter_list_com(
    sess_com: requests.Session, mirror: str, com_slug: str
) -> list[dict]:
    raw = _get_raw(sess_com, f"{mirror}/comic/{com_slug}", mirror + "/", retries=1)
    if not raw:
        return []
    html = raw.decode("utf-8", errors="replace")
    chapters = _parse_com_chapters(html, com_slug)
    if not chapters:
        chapters = _fetch_chapters_api(sess_com, mirror, com_slug)
    return chapters


# ── Images ────────────────────────────────────────────────────────────────────


def _images_from_mirror(
    sess_com: requests.Session, slug: str, key: str, mirror: str
) -> list[str]:
    for base in ([mirror] if mirror else []) + COM_MIRRORS:
        url = f"{base}/comic/chapter/{slug}/{key}.html"
        raw = _get_raw(sess_com, url, base + "/")
        if not raw:
            continue
        imgs = _extract_content_imgs(raw.decode("utf-8", errors="replace"))
        if imgs:
            return imgs
    return []


def _extract_content_imgs(html: str) -> list[str]:
    soup = _soup(html)
    candidates = []
    for img in soup.find_all("img"):
        for attr in ("data-src", "data-original", "src"):
            u = img.get(attr, "")
            if u and not u.startswith("data:"):
                if not u.startswith("http"):
                    u = "https:" + u
                if not any(x in u for x in _EXCLUDE_IMG):
                    candidates.append(u)
                    break
    if not candidates:
        for u in _IMG_RE.findall(html):
            if not any(x in u for x in _EXCLUDE_IMG):
                candidates.append(u)
    seen: set = set()
    return [u for u in candidates if not (u in seen or seen.add(u))]


# ══════════════════════════════════════════════════════════════
#  CLASE PÚBLICA
# ══════════════════════════════════════════════════════════════
class DownloaderBaozimh(BaseDownloader):
    NAME = "BAOZIMH  (baozimh.org/com)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess_org = _make_session(SITE_ORG)
        self._sess_com = requests.Session()
        self._sess_com.headers.update(_BASE_HEADERS)
        print("  Buscando mirror activo…", end=" ", flush=True)
        self._mirror = self._find_mirror_deadline()
        print(self._mirror or "ninguno")

    def _find_mirror_deadline(self, seconds: int = 8) -> Optional[str]:
        """Mirror probe with a hard wall-clock deadline so init never hangs."""

        import queue as _queue

        q: "_queue.Queue" = _queue.Queue(maxsize=1)

        def _probe():
            try:
                q.put(_find_active_mirror(self._sess_com))
            except Exception:
                pass

        threading.Thread(target=_probe, daemon=True).start()
        try:
            return q.get(timeout=seconds)
        except _queue.Empty:
            return None

    def search(self, query: str) -> list[dict]:
        return _search_org(self._sess_org, query)

    def get_catalog(
        self, type_: str = "all", region: str = "all", state: str = "all"
    ) -> list[dict]:
        mirror = self._mirror or COM_MIRRORS[0]
        return fetch_catalog_api(self._sess_com, mirror, type_, region, state, workers=4)

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list[dict], bool]:
        mirror = self._mirror or COM_MIRRORS[0]
        typ = kwargs.get("type_", "all")
        region = kwargs.get("region", "all")
        state = kwargs.get("state", "all")
        items = self._api_page_deadline(mirror, typ, region, state, page)
        if not items and page > 1:
            items = self._api_page_deadline(mirror, typ, region, state, page)
        return items[:page_size], len(items) >= 36

    def _api_page_deadline(self, mirror, typ, region, state, page, seconds=15):
        import queue as _queue

        q: "_queue.Queue" = _queue.Queue(maxsize=1)

        def _fetch():
            try:
                q.put(_fetch_api_page(self._sess_com, mirror, typ, region, state, page))
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()
        try:
            return q.get(timeout=seconds) or []
        except _queue.Empty:
            return []

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        slug = item.get("slug") or item.get("id", "")
        meta = _parse_series_meta_org(self._sess_org, slug) or dict(item)
        mirror = self._mirror or COM_MIRRORS[0]
        title = meta.get("title", item.get("title", ""))
        mid, chapters = _fetch_chapters_apikk(self._sess_org, slug)
        if mid and chapters:
            meta["mid"] = mid
            meta["com_slug"] = slug
            return meta, chapters
        com_slug = _resolve_com_slug(self._sess_com, mirror, slug, title)
        chapters = _get_chapter_list_com(self._sess_com, mirror, com_slug)
        if not chapters and com_slug != slug:
            chapters = _get_chapter_list_com(self._sess_com, mirror, slug)
            if chapters:
                com_slug = slug
        meta["com_slug"] = com_slug
        return meta, chapters

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        mid = chapter.get("mid") or series.get("mid")
        cid = chapter.get("key") or chapter.get("slug") or chapter.get("id", "")
        if mid and cid:
            urls = _fetch_chapter_images_apikk(self._sess_org, mid, cid)
            if urls:
                return urls
        com_slug = series.get("com_slug", series.get("slug", series.get("id", "")))
        key = chapter.get("key", "")
        if not key:
            return []
        return _images_from_mirror(self._sess_com, com_slug, key, self._mirror)

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        return _get_raw(self._sess_com, url, referer or self._mirror or SITE_ORG)

    def get_referer(self, chapter: dict, series: dict) -> str:
        if series.get("mid"):
            return SITE_ORG
        return self._mirror or COM_MIRRORS[0]
