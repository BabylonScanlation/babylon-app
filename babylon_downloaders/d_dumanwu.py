"""
d_dumanwu.py — dumanwu.com downloader (sin menú)
Descifrado XOR+base64, semillas desde all2.js, catálogo /sort/N + AJAX.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, TypedDict

import requests
from common import CFG, BaseDownloader

BASE_URL = "https://dumanwu.com"
TIMEOUT = (15, 45)
RETRY = 2.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": BASE_URL + "/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

_UI_PATHS = (
    "/static/",
    "load.gif",
    "logo.png",
    "prev.png",
    "next.png",
    "nulls.png",
    "user.png",
    "favicon.ico",
)

_SEEDS_FALLBACK_HEX = [
    "736d6b6879323538",
    "736d6b6439356676",
    "6d64343936393532",
    "63646373647771",
    "7662667361323536",
    "b28470300000",
    "6364353663766461",
    "386b69686e7439",
    "70d297b80000",
    "356b6f36706c6879",
]

_SYSTEM_SLUGS = {
    "static",
    "s",
    "list",
    "tag",
    "type",
    "update",
    "rank",
    "new",
    "morechapter",
    "sort",
    "user",
    "track",
    "sortmore",
    "rankmore",
}
_DW_SORTS = {
    1: "冒险",
    2: "热血",
    3: "都市",
    4: "玄幻",
    5: "悬疑",
    6: "耽美",
    7: "恋爱",
    8: "生活",
    9: "搞笑",
    10: "穿越",
    11: "修真",
    12: "后宫",
    13: "女主",
    14: "古风",
    15: "连载",
    16: "完结",
}


# ── Session ───────────────────────────────────────────────────────────────────


def _make_session() -> requests.Session:
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(HEADERS)
    return s


# ── Seeds (XOR) ───────────────────────────────────────────────────────────────


def load_seeds(sess: requests.Session) -> list[bytes]:
    js_urls: list[str] = []
    try:
        r = sess.get(f"{BASE_URL}/", timeout=8)
        m = re.findall(r'src="(/static/js/all2\.js[^"]*)"', r.text)
        js_urls = [BASE_URL + m[0]] if m else [f"{BASE_URL}/static/js/all2.js?v=2.3"]
    except Exception:
        js_urls = [f"{BASE_URL}/static/js/all2.js?v=2.3"]

    for js_url in js_urls:
        try:
            r = sess.get(js_url, timeout=10, headers={**HEADERS, "Accept": "*/*"})
            if r.status_code != 200 or len(r.content) < 100:
                continue
            js = r.text
            m = re.search(
                r'\[\s*"([0-9a-fA-F]{6,})"(?:\s*,\s*"([0-9a-fA-F]{6,})")+\s*\]', js
            )
            if m:
                hexes = re.findall(r'"([0-9a-fA-F]{8,})"', m.group(0))
                seeds = []
                for h in hexes:
                    try:
                        seeds.append(bytes.fromhex(h))
                    except Exception:
                        pass
                if seeds:
                    return seeds
        except Exception:
            continue

    seeds = []
    for h in _SEEDS_FALLBACK_HEX:
        try:
            seeds.append(bytes.fromhex(h))
        except Exception:
            pass
    return seeds


# ── Decryption ────────────────────────────────────────────────────────────────

_B62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _b62_int(token: str, base: int = 62) -> int:
    chars = _B62[:base] if base <= 62 else _B62
    n = 0
    try:
        for ch in token:
            n = n * base + chars.index(ch)
    except ValueError:
        return -1
    return n


def _decode_packer(p: str, base: int, k_str: str) -> str:
    keys = k_str.split("|")

    def replace(m: re.Match) -> str:
        idx = _b62_int(m.group(0), base)
        return keys[idx] if 0 <= idx < len(keys) and keys[idx] else m.group(0)

    return re.sub(r"\b[0-9A-Za-z]+\b", replace, p)


def _extract_packer_args(script: str):
    try:
        start = script.rindex("}(") + 2
        args = script[start:]
        parts = re.findall(r"'((?:[^'\\]|\\.)*)'|(\d+)", args)
        vals = [int(n) if n else s for s, n in parts]
        if len(vals) >= 4:
            return str(vals[0]), int(vals[1]), int(vals[2]), str(vals[3])
    except (ValueError, IndexError):
        pass
    return None


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))


def decrypt_images(html: str, seeds: list[bytes]) -> list[str]:
    scripts = re.findall(
        r"<script[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE
    )
    for script in scripts:
        if "eval(function(p,a,c,k,e,d)" not in script:
            continue
        args = _extract_packer_args(script)
        if not args:
            continue
        p, base, _count, k = args
        decoded = _decode_packer(p, base, k)
        m = re.search(
            r"""var\s+\w+\s*=\s*['"]([A-Za-z0-9+/]{40,}={0,2})['"]""", decoded
        )
        if not m:
            continue
        try:
            raw = base64.b64decode(m.group(1) + "==")
        except Exception:
            continue
        for seed in seeds:
            try:
                xored = _xor(raw, seed)
                final = base64.b64decode(xored + b"==").decode("utf-8", errors="ignore")
                if "http" not in final:
                    continue
                try:
                    data = json.loads(final)
                    if isinstance(data, list):
                        urls = [str(u) for u in data if "http" in str(u)]
                        if urls:
                            return urls
                except (json.JSONDecodeError, ValueError):
                    pass
                raw_urls = re.findall(r"https?://[^\s\"',\[\]]+", final)
                urls2 = [
                    u
                    for u in raw_urls
                    if any(e in u.lower() for e in [".jpg", ".jpeg", ".png", ".webp"])
                    or any(cdn in u for cdn in ["ecombdimg", "shimolife", "tplv"])
                ]
                if urls2:
                    return urls2
            except Exception:
                continue
    return []


# ── Series / chapters ─────────────────────────────────────────────────────────


def _cap_sort_key(cap: dict) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", cap.get("title", ""))
    return float(m.group(1)) if m else 0.0


def _parse_series_page(
    sess: requests.Session, slug: str, seeds: list[bytes]
) -> tuple[dict, list[dict]]:
    url = f"{BASE_URL}/{slug}/"
    r = sess.get(url, timeout=15)
    if r.status_code != 200:
        return {}, []
    html = r.text
    h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
    title = h1.group(1).strip() if h1 else slug

    slug_esc = re.escape(slug)
    caps: list[dict] = []
    seen: set = set()
    for m2 in re.finditer(
        rf'href="(/{slug_esc}/([A-Za-z0-9]+)\.html)"[^>]*>([^<]*)</a>', html
    ):
        href, cap_slug, a_text = m2.group(1), m2.group(2), m2.group(3).strip()
        if cap_slug not in seen and "阅读" not in a_text:
            seen.add(cap_slug)
            caps.append(
                {
                    "id": cap_slug,
                    "slug": cap_slug,
                    "title": a_text or cap_slug,
                    "url": f"{BASE_URL}{href}",
                }
            )

    try:
        r2 = sess.post(f"{BASE_URL}/morechapter", data={"id": slug}, timeout=10)
        if r2.status_code == 200:
            data = r2.json()
            if str(data.get("code", "")) == "200" and "data" in data:
                for item in data["data"]:
                    if not isinstance(item, dict):
                        continue
                    cid = item.get("chapterid")
                    cname = item.get("chaptername", "")
                    if cid and str(cid) not in seen:
                        seen.add(str(cid))
                        caps.append(
                            {
                                "id": str(cid),
                                "slug": str(cid),
                                "title": str(cname) if cname else str(cid),
                                "url": f"{BASE_URL}/{slug}/{cid}.html",
                            }
                        )
    except Exception:
        pass

    caps.sort(key=_cap_sort_key)
    meta = {"id": slug, "slug": slug, "title": title}
    return meta, caps


def _get_chapter_images(
    sess: requests.Session, chap_url: str, series_slug: str, seeds: list[bytes]
) -> list[str]:
    referer = f"{BASE_URL}/{series_slug}/"
    html = None
    for _ in range(3):
        try:
            r = sess.get(chap_url, timeout=15, headers={**HEADERS, "Referer": referer})
            if r.status_code == 200:
                html = r.text
                if "eval(function(p,a,c,k,e,d)" in html:
                    break
        except Exception:
            pass
        time.sleep(1.5)
    if not html:
        return []
    urls = decrypt_images(html, seeds)
    if urls:
        return [
            u
            for u in urls
            if "scl3phc04j" not in u and not any(p in u.lower() for p in _UI_PATHS)
        ]
    # Fallback
    seen: set = set()
    fallback = []
    for pat in [r'data-src="(https?://[^"]+)"', r'data-original="(https?://[^"]+)"']:
        for m in re.finditer(pat, html):
            src = m.group(1)
            if (
                src not in seen
                and not any(p in src.lower() for p in _UI_PATHS)
                and "scl3phc04j" not in src
            ):
                seen.add(src)
                fallback.append(src)
    return fallback


# ── Catalog ───────────────────────────────────────────────────────────────────


def _parse_series_html(html: str) -> list[dict]:
    items: list[dict] = []
    seen: set = set()
    for m in re.finditer(
        r'<a\s[^>]*href="(?:https?://dumanwu\.com)?/([A-Za-z0-9]{5,10})/"[^>]*>'
        r"([\s\S]{0,400}?)</a>",
        html,
    ):
        slug = m.group(1)
        inner = m.group(2)
        if slug in _SYSTEM_SLUGS or slug in seen:
            continue
        h2 = re.search(r"<h2[^>]*>([^<]{1,100})</h2>", inner)
        if not h2:
            continue
        title = re.sub(r"<[^>]+>", "", h2.group(1)).strip()
        if not title:
            continue
        seen.add(slug)
        items.append({"id": slug, "slug": slug, "title": title})
    return items


def _sortmore(sess: requests.Session, type_id: int, page: int) -> list[dict]:
    try:
        r = sess.post(
            f"{BASE_URL}/sortmore",
            data={"type": type_id, "page": page},
            headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"},
            timeout=10,
        )
        if r.status_code != 200 or len(r.content) < 50:
            return []
        ct = r.headers.get("Content-Type", "")
        if "json" in ct:
            try:
                data = r.json()
                if str(data.get("code", "")) == "200" and isinstance(
                    data.get("data"), list
                ):
                    return [
                        {
                            "id": str(row.get("id", "")),
                            "slug": str(row.get("id", "")),
                            "title": str(row.get("name", "")),
                        }
                        for row in data["data"]
                        if row.get("id")
                    ]
            except Exception:
                pass
        return _parse_series_html(r.text)
    except Exception:
        return []


def _load_sort(sess: requests.Session, sort_id: int) -> list[dict]:
    items: list[dict] = []
    seen: set = set()
    try:
        r = sess.get(f"{BASE_URL}/sort/{sort_id}", timeout=15, headers=HEADERS)
        if r.status_code == 200:
            for it in _parse_series_html(r.text):
                if it["slug"] not in seen:
                    seen.add(it["slug"])
                    items.append(it)
    except Exception:
        pass
    page, consecutive_empty = 2, 0
    while page <= 500:
        more = _sortmore(sess, sort_id, page)
        if not more:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                break
            page += 1
            continue
        consecutive_empty = 0
        added = 0
        for it in more:
            if it["slug"] not in seen:
                seen.add(it["slug"])
                items.append(it)
                added += 1
        if added == 0:
            break
        page += 1
        time.sleep(0.1)
    return items


def load_full_catalog(sess: requests.Session) -> list[dict]:
    all_items: list[dict] = []
    seen: set = set()
    for sort_id, sort_name in _DW_SORTS.items():
        sys.stdout.write(f"  [{sort_id}/{len(_DW_SORTS)}] {sort_name}…\r")
        sys.stdout.flush()
        for it in _load_sort(sess, sort_id):
            if it["slug"] not in seen:
                seen.add(it["slug"])
                all_items.append(it)
    print(f"  ✔ {len(all_items)} series cargadas   ")
    return all_items


# ── Search ────────────────────────────────────────────────────────────────────


def _search(sess: requests.Session, query: str) -> list[dict]:
    try:
        r = sess.post(
            f"{BASE_URL}/s",
            data={"k": query},
            headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if str(data.get("code")) == "200" and isinstance(data.get("data"), list):
                return [
                    {
                        "id": str(it.get("id")),
                        "slug": str(it.get("id")),
                        "title": str(it.get("name", "")),
                    }
                    for it in data["data"]
                    if it.get("id") and it.get("name")
                ]
    except Exception:
        pass
    return []


# ══════════════════════════════════════════════════════════════
#  CLASE PÚBLICA
# ══════════════════════════════════════════════════════════════
class DownloaderDumanwu(BaseDownloader):
    NAME = "DUMANWU  (dumanwu.com)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()
        print("  Cargando semillas XOR…", end=" ", flush=True)
        self._seeds = load_seeds(self._sess)
        print(f"{len(self._seeds)} semillas")

    def search(self, query: str) -> list[dict]:
        return _search(self._sess, query)

    def get_catalog(self) -> list[dict]:
        return load_full_catalog(self._sess)

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        slug = item.get("slug") or item.get("id", "")
        return _parse_series_page(self._sess, slug, self._seeds)

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        url = chapter.get("url", "")
        slug = series.get("slug", series.get("id", ""))
        if not url:
            url = f"{BASE_URL}/{slug}/{chapter.get('id', '')}.html"
        return _get_chapter_images(self._sess, url, slug, self._seeds)

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        for attempt in range(3):
            try:
                r = self._sess.get(url, timeout=(5, 15))
                if r.status_code == 200 and len(r.content) > 5 * 1024:
                    return r.content
            except Exception:
                pass
            time.sleep(attempt + 1)
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        slug = series.get("slug", series.get("id", ""))
        return f"{BASE_URL}/{slug}/"
