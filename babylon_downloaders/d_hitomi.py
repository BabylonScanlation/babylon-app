"""
d_hitomi.py — hitomi.la downloader (sin menú)
Índices binarios nozomi, gg.js para URLs, sin scraping HTML.
Completamente distinto al resto: no tiene "series" ni "capítulos",
solo galerías con un ID numérico.
"""

from __future__ import annotations

import hashlib
import re
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from common import CFG, BaseDownloader

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://hitomi.la/",
}

LANGUAGES = {
    "all": "Todos",
    "japanese": "Japonés",
    "english": "Inglés",
    "chinese": "Chino",
    "korean": "Coreano",
    "spanish": "Español",
    "french": "Francés",
    "german": "Alemán",
    "italian": "Italiano",
    "russian": "Ruso",
    "thai": "Tailandés",
}


# ── Session ───────────────────────────────────────────────────────────────────


def _make_session() -> requests.Session:
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(HEADERS)
    return s


# ── gg.js ─────────────────────────────────────────────────────────────────────


class HitomiGG:
    def __init__(self, sess: requests.Session):
        self.m_default = 0
        self.m_map: dict[int, int] = {}
        self.b_val = ""
        self._load(sess)

    def _load(self, sess: requests.Session) -> None:
        try:
            body = sess.get(
                "https://ltn.gold-usergeneratedcontent.net/gg.js", timeout=10
            ).text
            m_o = re.search(r"var o = (\d)", body)
            self.m_default = int(m_o.group(1)) if m_o else 0
            o_m = re.search(r"o = (\d); break;", body)
            o_v = int(o_m.group(1)) if o_m else self.m_default
            for c in re.findall(r"case (\d+):", body):
                self.m_map[int(c)] = o_v
            m_b = re.search(r"b: '(.+)'", body)
            self.b_val = m_b.group(1) if m_b else ""
            if self.b_val and not self.b_val.endswith("/"):
                self.b_val += "/"
        except Exception:
            pass

    def get_url(self, h: str, ext: str) -> str:
        g = int(h[-1] + h[-3:-1], 16) if h else 0
        m = self.m_map.get(g, self.m_default)
        sub = f"{'a' if ext == 'avif' else 'w'}{1 + m}"
        return f"https://{sub}.gold-usergeneratedcontent.net/{self.b_val}{g}/{h}.{ext}"


# ── Nozomi (binary index) ─────────────────────────────────────────────────────


def _nozomi_ids(sess: requests.Session, url: str) -> list[int]:
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200 or len(r.content) < 4:
            return []
        data = r.content
        # IDs como unsigned int32 big-endian
        return [
            struct.unpack(">I", data[i * 4 : (i + 1) * 4])[0]
            for i in range(len(data) // 4)
        ]
    except Exception:
        return []


def _term_url(term: str) -> str:
    base = "https://ltn.gold-usergeneratedcontent.net"
    term = term.replace("_", " ").strip()
    if ":" in term:
        ns, v = term.split(":", 1)
        if ns in ("female", "male"):
            return f"{base}/n/tag/{term}-all.nozomi"
        if ns == "language":
            return f"{base}/n/index-{v}.nozomi"
        return f"{base}/n/{ns}/{v}-all.nozomi"
    return f"{base}/n/tag/{term}-all.nozomi"


# ── Tag-index (trie sha256) ───────────────────────────────────────────────────
# hitomi migró la búsqueda a un B-tree en tagindex.hitomi.la: el término se
# hashea con sha256 (primeros 4 bytes) y se recorre el .index por rangos de
# bytes; las hojas apuntan a rangos de un .data con los gallery IDs.

TAG_INDEX_DOMAIN = "tagindex.hitomi.la"
LTN_DOMAIN = "ltn.gold-usergeneratedcontent.net"
INDEX_BUILD_B = 16
MAX_NODE_SIZE = 464

_VER_CACHE: dict[str, Optional[str]] = {}


def _curl_session():
    """Sesión browser-like para los endpoints de índice que exigen TLS real."""
    try:
        from curl_cffi.requests import Session as _Curl

        s = _Curl(impersonate="chrome124")
        s.headers["Referer"] = "https://hitomi.la/search.html"
        return s
    except Exception:
        import requests as _r

        s = _r.Session()
        s.headers.update(HEADERS)
        return s


def _index_version(sess: requests.Session, name: str) -> Optional[str]:
    """Versión vigente de un índice (p.ej. 'galleriesindex', 'tagindex')."""
    if name not in _VER_CACHE:
        try:
            r = sess.get(f"https://{LTN_DOMAIN}/{name}/version", timeout=15)
            _VER_CACHE[name] = (
                r.text.strip() if r.status_code == 200 and r.text.strip() else None
            )
            if not _VER_CACHE[name]:
                sess = _curl_session()
                r = sess.get(f"https://{LTN_DOMAIN}/{name}/version", timeout=15)
                _VER_CACHE[name] = (
                    r.text.strip() if r.status_code == 200 and r.text.strip() else None
                )
        except Exception:
            _VER_CACHE[name] = None
    return _VER_CACHE[name]


def _range_get(
    sess: requests.Session, url: str, offset: int, length: int
) -> Optional[bytes]:
    try:
        r = sess.get(
            url,
            headers={"Range": f"bytes={offset}-{offset + length - 1}"},
            timeout=20,
        )
        if r.status_code in (200, 206) and r.content:
            return r.content
    except Exception:
        pass
    return None


def _decode_node(data: bytes) -> Optional[dict]:
    if len(data) < 8:
        return None
    try:
        pos = 0
        n_keys = struct.unpack_from(">i", data, pos)[0]
        pos += 4
        if n_keys > INDEX_BUILD_B or n_keys < 0:
            return None
        keys = []
        for _ in range(n_keys):
            ks = struct.unpack_from(">i", data, pos)[0]
            pos += 4
            if ks <= 0 or ks > 32 or pos + ks > len(data):
                return None
            keys.append(data[pos : pos + ks])
            pos += ks
        n_datas = struct.unpack_from(">i", data, pos)[0]
        pos += 4
        if n_datas > INDEX_BUILD_B or n_datas < 0:
            return None
        datas = []
        for _ in range(n_datas):
            off = struct.unpack_from(">Q", data, pos)[0]
            pos += 8
            ln = struct.unpack_from(">i", data, pos)[0]
            pos += 4
            datas.append((off, ln))
        subnodes = []
        for _ in range(INDEX_BUILD_B + 1):
            subnodes.append(struct.unpack_from(">Q", data, pos)[0])
            pos += 8
        return {"keys": keys, "datas": datas, "subs": subnodes}
    except Exception:
        return None


def _node_at(
    sess: requests.Session,
    index_dir: str,
    version: str,
    field: str,
    address: int,
) -> Optional[dict]:
    url = f"https://{LTN_DOMAIN}/{index_dir}/{field}.{version}.index"
    raw = _range_get(sess, url, address, MAX_NODE_SIZE)
    if not raw:
        return None
    if len(raw) < 8:
        sess = _curl_session()
        raw = _range_get(sess, url, address, MAX_NODE_SIZE)
    return _decode_node(raw) if raw else None


def _bsearch_gallery(
    sess: requests.Session,
    version: str,
    key: bytes,
) -> Optional[tuple[int, int]]:
    """Devuelve (offset, length) en el .data de galleries para el key, o None."""
    node = _node_at(sess, "galleriesindex", version, "galleries", 0)
    while node:
        keys = node["keys"]
        if not keys:
            return None
        where = 0
        there = False
        for i, k in enumerate(keys):
            if key <= k:
                if key == k:
                    there = True
                where = i
                break
        else:
            where = len(keys)
        if there:
            off, ln = node["datas"][where]
            return (off, ln)
        if not any(node["subs"]):
            return None
        addr = node["subs"][where]
        if addr == 0:
            return None
        node = _node_at(sess, "galleriesindex", version, "galleries", addr)
    return None


def _gallery_ids_from_data(
    sess: requests.Session, version: str, data: tuple[int, int]
) -> list[int]:
    off, ln = data
    url = f"https://{LTN_DOMAIN}/galleriesindex/galleries.{version}.data"
    raw = _range_get(sess, url, off, ln)
    if not raw or len(raw) < 4:
        if raw is not None:
            sess = _curl_session()
            raw = _range_get(sess, url, off, ln)
    if not raw or len(raw) < 4:
        return []
    try:
        count = struct.unpack_from(">i", raw, 0)[0]
        if count <= 0 or 4 + count * 4 != len(raw) or count > 10_000_000:
            return []
        return list(struct.unpack_from(f">{count}i", raw, 4))
    except Exception:
        return []


def _term_ids(sess: requests.Session, term: str) -> list[int]:
    """IDs de galerías para un término de texto plano (vía el trie sha256)."""
    if ":" in term:
        return _nozomi_ids(sess, _term_url(term))
    ver = _index_version(sess, "galleriesindex")
    if not ver:
        return []
    key = hashlib.sha256(term.replace("_", " ").encode("utf-8")).digest()[:4]
    data = _bsearch_gallery(sess, ver, key)
    if not data:
        return []
    return _gallery_ids_from_data(sess, ver, data)


def fetch_catalog_ids(
    sess: requests.Session,
    language: str = "all",
    sort_terms: Optional[list[str]] = None,
) -> list[int]:
    base = "https://ltn.gold-usergeneratedcontent.net"
    ids = _nozomi_ids(sess, f"{base}/index-{language}.nozomi")
    if not ids:
        ids = _nozomi_ids(sess, f"{base}/n/index-{language}.nozomi")
    if ids and sort_terms:
        ids = _apply_sort(sess, ids, sort_terms)
    return ids


def _apply_sort(
    sess: requests.Session, base_ids: list[int], sort_terms: list[str]
) -> list[int]:
    if not sort_terms:
        return base_ids
    base_set = set(base_ids)
    sort_ordered = _nozomi_ids(sess, _term_url(sort_terms[0]))
    for extra in sort_terms[1:]:
        extra_set = {
            struct.unpack(">i", r.content[i * 4 : (i + 1) * 4])[0]
            for r in [sess.get(_term_url(extra), timeout=15)]
            if r.status_code == 200
            for i in range(len(r.content) // 4)
        }
        sort_ordered = [i for i in sort_ordered if i in extra_set]
    seen: set = set()
    result: list[int] = []
    for gid in sort_ordered:
        if gid in base_set:
            result.append(gid)
            seen.add(gid)
    for gid in base_ids:
        if gid not in seen:
            result.append(gid)
    return result


def search_ids(
    sess: requests.Session, query: str, sort_terms: Optional[list[str]] = None
) -> list[int]:
    q = query.strip().lower()
    parts = [p for p in q.split() if p]
    if not parts:
        return []
    ids: set[int] = set()
    # Primer término inicializa el set (solo términos positivos)
    for p in parts:
        if p.startswith("-") or p.startswith("sort") or p.startswith("order"):
            continue
        ids = set(_term_ids(sess, p.replace("_", " ")))
        break
    if not ids:
        return []
    # Términos adicionales positivos: intersección
    for p in parts:
        if p == parts[0]:
            continue
        if p.startswith("-") or p.startswith("sort") or p.startswith("order"):
            continue
        t = set(_term_ids(sess, p.replace("_", " ")))
        if t:
            ids.intersection_update(t)
    if not ids:
        return []
    if sort_terms:
        return _apply_sort(sess, list(ids), sort_terms)
    return sorted(ids, reverse=True)


# ── Metadata ──────────────────────────────────────────────────────────────────
_META_CACHE: dict[str, dict] = {}


def load_meta(sess: requests.Session, gid: int) -> None:
    if str(gid) in _META_CACHE:
        return
    try:
        r = sess.get(
            f"https://ltn.gold-usergeneratedcontent.net/galleries/{gid}.js", timeout=5
        )
        import json

        _META_CACHE[str(gid)] = json.loads(r.text.split("var galleryinfo = ")[1])
    except Exception:
        pass


def load_meta_batch(
    sess: requests.Session, gids: list[int], max_workers: int = 20
) -> None:
    to_load = [g for g in gids if str(g) not in _META_CACHE]
    if not to_load:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        list(exe.map(lambda g: load_meta(sess, g), to_load))


def gallery_title(gid: int) -> str:
    m = _META_CACHE.get(str(gid), {})
    return str(m.get("title", str(gid)))[:55] if m else str(gid)


def gallery_files(gid: int) -> list[dict]:
    m = _META_CACHE.get(str(gid), {})
    return [f for f in m.get("files", []) if isinstance(f, dict)] if m else []


# ── Image URLs ────────────────────────────────────────────────────────────────


def get_image_urls(gg: HitomiGG, gid: int) -> list[str]:
    files = gallery_files(gid)
    urls = []
    for f in files:
        h = str(f.get("hash", ""))
        ext = "avif" if f.get("hasavif") else "webp"
        urls.append(gg.get_url(h, ext))
    return urls


# ══════════════════════════════════════════════════════════════
#  CLASE PÚBLICA
#
#  Hitomi es diferente: no tiene "series/capítulos".
#  Cada galería ES una unidad.  El menu.py lo trata distinto:
#    - search()       → lista de IDs como dicts {id, title}
#    - get_catalog()  → ídem
#    - get_series()   → devuelve meta de la galería + un único "capítulo"
#    - get_chapter_images() → URLs de las imágenes
# ══════════════════════════════════════════════════════════════
class DownloaderHitomi(BaseDownloader):
    NAME = "HITOMI  (hitomi.la)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()
        print("  Cargando gg.js…", end=" ", flush=True)
        self._gg = HitomiGG(self._sess)
        print("ok")

    # items = [{"id": str(gid), "title": ...}]
    def search(self, query: str) -> list[dict]:
        query = query.strip()
        # Si es un ID numérico puro, descarga directa
        if query.isdigit():
            gid = int(query)
            load_meta(self._sess, gid)
            return [{"id": str(gid), "title": gallery_title(gid)}]
        ids = search_ids(self._sess, query)
        load_meta_batch(self._sess, ids[:50])
        return [{"id": str(g), "title": gallery_title(g)} for g in ids]

    def get_catalog(self, language: str = "all") -> list[dict]:
        ids = fetch_catalog_ids(self._sess, language)
        # Pre-load first 200 for quick display; rest loaded on-demand when accessed
        load_meta_batch(self._sess, ids[:200])
        return [{"id": str(g), "title": gallery_title(g)} for g in ids]

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list, bool]:
        language = kwargs.get("language", "all")
        key = f"hitomi_{language}"
        if getattr(self, "_cat_buf_key", None) != key:
            ids = fetch_catalog_ids(self._sess, language)
            # Store all IDs; load metadata lazily per page
            self._cat_ids: list[int] = ids
            self._cat_buf_key: str = key

        ids = self._cat_ids
        start = (page - 1) * page_size
        end = min(start + page_size, len(ids))
        # Load metadata for this page
        load_meta_batch(self._sess, ids[start:end])
        chunk = [{"id": str(g), "title": gallery_title(g)} for g in ids[start:end]]
        has_more = end < len(ids)
        return chunk, has_more

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        gid = int(item["id"])
        load_meta(self._sess, gid)
        title = gallery_title(gid)
        series = {"id": str(gid), "slug": str(gid), "title": title}
        # Una sola "entrada" = la galería completa
        chapter = {"id": str(gid), "title": title}
        return series, [chapter]

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        gid = int(chapter.get("id", series.get("id", 0)))
        load_meta(self._sess, gid)
        return get_image_urls(self._gg, gid)

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        for _ in range(3):
            try:
                r = self._sess.get(url, headers=HEADERS, timeout=15)
                if r.status_code == 200:
                    return r.content
            except Exception:
                time.sleep(1)
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        return "https://hitomi.la/"

    # ── helpers extras expuestos al menú ─────────────────────
    @property
    def languages(self) -> dict[str, str]:
        return LANGUAGES

    def preload_batch(self, items: list[dict]) -> None:
        gids = [int(it["id"]) for it in items if it.get("id", "").isdigit()]
        load_meta_batch(self._sess, gids)
