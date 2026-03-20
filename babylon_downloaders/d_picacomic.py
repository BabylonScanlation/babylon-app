"""
d_picacomic.py — picacomic.com downloader (sin menú)
API REST con firma HMAC. Requiere login.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from common import CFG, BaseDownloader

BASE_URL = "https://picaapi.picacomic.com"

# ── Credenciales (extraídas del script original) ──────────────────────────────
PICACOMIC_EMAIL = "lucaaaa09"
PICACOMIC_PASSWORD = "Aa0!Bb2?Cc4_"
# Token JWT de respaldo (puede estar expirado; si falla se usa email+password):
PICACOMIC_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiI2OWFmYWM1MGZiMzgxZGM2ZjU5OWI1ZDYiLCJlbWFpbCI6Imx1Y2FhYWEwOSIsInJvbGUiOiJtZW1iZXIiLCJuYW1lIjoiTHVjYXMgR29sZHN0ZWluIiwidmVyc2lvbiI6IjIuMi4xLjIuMy4zIiwiYnVpbGRWZXJzaW9uIjoiNDQiLCJwbGF0Zm9ybSI6ImFuZHJvaWQiLCJpYXQiOjE3NzMxMjQ4MDQsImV4cCI6MTc3MzcyOTYwNH0.XHxBVgHxzhwnuRhLgABtlsmmVIx4NLY4WcALOBbW7F0"
API_KEY = "C69BAF41DA5ABD1FFEDC6D2FEA56B"
SECRET = r"~d}$Q7$eIni=V)9\RK/P.RM4;9[7|@/CA}b~OW!3?EV`:<>M7pddUBL5n|0/*Cn"
APP_VER = "2.2.1.2.3.3"
BUILD_VER = "44"

try:
    from curl_cffi.requests import Session as CurlSession

    _USE_CURL = True
except ImportError:
    CurlSession = None
    _USE_CURL = False

if not _USE_CURL:
    import requests as _req


# ── Auth helpers ──────────────────────────────────────────────────────────────


def _sign(path: str, ts: str, nonce: str, method: str) -> str:
    raw = (path.lstrip("/") + ts + nonce + method + API_KEY).lower()
    return hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _build_headers(path: str, method: str, token: str) -> dict:
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    h = {
        "api-key": API_KEY,
        "accept": "application/vnd.picacomic.com.v1+json",
        "app-channel": "2",
        "app-version": APP_VER,
        "app-uuid": "defaultUuid",
        "app-platform": "android",
        "app-build-version": BUILD_VER,
        "time": ts,
        "nonce": nonce,
        "signature": _sign(path, ts, nonce, method),
        "image-quality": "original",
        "Content-Type": "application/json; charset=UTF-8",
        "User-Agent": "okhttp/3.8.1",
    }
    if token:
        h["authorization"] = token
    return h


def _make_session():
    if _USE_CURL:
        return CurlSession()
    s = _req.Session()
    s.headers.update({"User-Agent": "okhttp/3.8.1"})
    return s


# ── API calls ─────────────────────────────────────────────────────────────────


def _api_get(
    sess, path: str, token: str, params=None, retries: int = 3
) -> Optional[dict]:
    time.sleep(0.3)
    url = BASE_URL + "/" + path.lstrip("/")
    for attempt in range(retries):
        try:
            sign_path = (
                path.lstrip("/") + "?" + "&".join(f"{k}={v}" for k, v in params.items())
                if params
                else path
            )
            r = sess.get(
                url,
                params=params,
                headers=_build_headers(sign_path, "GET", token),
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code in (400, 401, 403, 404):
                return None
        except Exception:
            pass
        time.sleep(1 + attempt)
    return None


def _api_post(
    sess, path: str, token: str, body: dict, retries: int = 3
) -> Optional[dict]:
    time.sleep(0.3)
    url = BASE_URL + "/" + path.lstrip("/")
    for attempt in range(retries):
        try:
            r = sess.post(
                url,
                json=body,
                headers=_build_headers(path.lstrip("/"), "POST", token),
                timeout=15,
            )
            try:
                data = r.json()
            except Exception:
                data = None
            if r.status_code == 200:
                return data
            if r.status_code in (400, 401, 403, 404):
                return data
        except Exception:
            pass
        time.sleep(1 + attempt)
    return None


# ── Login ─────────────────────────────────────────────────────────────────────


def do_login(sess, email: str, password: str) -> str:
    """Returns token string or ''."""
    resp = _api_post(sess, "/auth/sign-in", "", {"email": email, "password": password})
    if not resp:
        return ""
    token = (
        (resp.get("data") or {}).get("token")
        or resp.get("token")
        or ((resp.get("data") or {}).get("user") or {}).get("token")
        or ""
    )
    if not token:
        hdrs = resp.get("_resp_headers") or {}
        token = (
            hdrs.get("authorization")
            or hdrs.get("Authorization")
            or hdrs.get("token")
            or ""
        )
    return token.removeprefix("Bearer ").strip()


# ── Catalog / search ──────────────────────────────────────────────────────────


def _parse_comic_stub(d: dict) -> dict:
    return {
        "id": d.get("_id", ""),
        "slug": d.get("_id", ""),
        "title": d.get("title", "")[:70],
        "author": d.get("author", ""),
        "eps": d.get("epsCount", 1),
        "finished": d.get("finished", False),
        "categories": d.get("categories", []),
    }


def get_categories(sess, token: str) -> list[dict]:
    resp = _api_get(sess, "/categories", token)
    if not resp or resp.get("code") != 200:
        return []
    cats = (resp.get("data") or {}).get("categories", [])
    return [
        {
            "id": c.get("title", ""),
            "slug": c.get("title", ""),
            "title": c.get("title", ""),
        }
        for c in cats
        if not c.get("isWeb")
    ]


def search_comics(
    sess, token: str, keyword: str, page: int = 1, sort: str = "dd"
) -> tuple[list[dict], int]:
    body = {"keyword": keyword, "sort": sort, "page": page}
    resp = _api_post(sess, f"/comics/advanced-search?page={page}", token, body)
    if not resp or resp.get("code") != 200:
        return [], 0
    data = ((resp.get("data") or {}).get("comics")) or {}
    docs = data.get("docs", [])
    pages = data.get("pages", 1)
    return [_parse_comic_stub(d) for d in docs], pages


def _fetch_global_page(
    sess, token: str, page: int, sort: str
) -> tuple[int, list[dict], int]:
    resp = _api_get(sess, "/comics", token, params={"page": page, "s": sort})
    if not resp or resp.get("code") != 200:
        return page, [], 0
    outer = (resp.get("data") or {}).get("comics") or {}
    docs = outer.get("docs", [])
    total = outer.get("pages", 1)
    return page, [_parse_comic_stub(d) for d in docs], total


def fetch_full_catalog(
    sess,
    token: str,
    sort: str = "dd",
    workers: int = 20,
    page_limit: Optional[int] = None,
) -> list[dict]:
    _, first, total = _fetch_global_page(sess, token, 1, sort)
    if not first:
        return []
    if page_limit:
        total = min(total, page_limit)
    all_comics: list[dict] = list(first)
    remaining = list(range(2, total + 1))
    BATCH = workers * 5
    for batch_start in range(0, len(remaining), BATCH):
        batch = remaining[batch_start : batch_start + BATCH]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(_fetch_global_page, sess, token, pg, sort): pg
                for pg in batch
            }
            for fut in as_completed(futs):
                _, comics, _ = fut.result()
                all_comics.extend(comics)
    return all_comics


# ── Comic info / episodes / pages ─────────────────────────────────────────────


def get_comic_info(sess, token: str, comic_id: str) -> dict:
    resp = _api_get(sess, f"/comics/{comic_id}", token)
    if not resp or resp.get("code") != 200:
        return {}
    c = (resp.get("data") or {}).get("comic") or {}
    if not c:
        return {}
    return {
        "id": c.get("_id", comic_id),
        "slug": c.get("_id", comic_id),
        "title": c.get("title", ""),
        "author": c.get("author", ""),
        "desc": c.get("description", "")[:200],
        "eps": c.get("epsCount", 1),
        "categories": c.get("categories", []),
    }


def get_episodes(sess, token: str, comic_id: str) -> list[dict]:
    eps = []
    page = 1
    while True:
        resp = _api_get(sess, f"/comics/{comic_id}/eps", token, {"page": page})
        if not resp or resp.get("code") != 200:
            break
        data = ((resp.get("data") or {}).get("eps")) or {}
        eps.extend(data.get("docs", []))
        if page >= data.get("pages", 1):
            break
        page += 1
    eps.sort(key=lambda e: e.get("order", 0))
    return [
        {
            "id": str(e.get("order", i + 1)),
            "order": e.get("order", i + 1),
            "title": e.get("title", f"Cap {i + 1}"),
        }
        for i, e in enumerate(eps)
    ]


def get_pages(sess, token: str, comic_id: str, ep_order: int) -> list[dict]:
    pages = []
    page = 1
    while True:
        resp = _api_get(
            sess, f"/comics/{comic_id}/order/{ep_order}/pages", token, {"page": page}
        )
        if not resp or resp.get("code") != 200:
            break
        data = ((resp.get("data") or {}).get("pages")) or {}
        pages.extend(data.get("docs", []))
        if page >= data.get("pages", 1):
            break
        page += 1
    return pages


def _img_url(media: dict) -> str:
    server = media.get("fileServer", "").rstrip("/")
    path = media.get("path", "")
    if path.startswith("http"):
        return path
    return f"{server}/static/{path}"


# ══════════════════════════════════════════════════════════════
#  CLASE PÚBLICA
# ══════════════════════════════════════════════════════════════
class DownloaderPicacomic(BaseDownloader):
    NAME = "PICACOMIC  (picacomic.com)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()
        self._token = ""
        # Try JWT token first (fast, no network call)
        if PICACOMIC_TOKEN:
            self._token = PICACOMIC_TOKEN
            print("  PicaComic: token JWT cargado")
        # Always also try email+password to get a fresh token (token may be expired)
        if PICACOMIC_EMAIL and PICACOMIC_PASSWORD:
            print("  PicaComic: login…", end=" ", flush=True)
            t = do_login(self._sess, PICACOMIC_EMAIL, PICACOMIC_PASSWORD)
            if t:
                self._token = t  # fresh token overrides old JWT
                print("OK")
            elif not self._token:
                print("FALLÓ")

    @property
    def NEEDS_LOGIN(self):
        return not bool(self._token)

    def login(self, email: str = "", password: str = "", token: str = "") -> bool:
        if token:
            self._token = token
            return True
        if email and password:
            t = do_login(self._sess, email, password)
            if t:
                self._token = t
                return True
        return False

    def search(self, query: str) -> list[dict]:
        results, _ = search_comics(self._sess, self._token, query)
        return results

    def get_catalog(
        self, sort: str = "dd", page_limit: Optional[int] = None
    ) -> list[dict]:
        return fetch_full_catalog(self._sess, self._token, sort, page_limit=page_limit)

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        cid = item.get("id", "")
        info = get_comic_info(self._sess, self._token, cid)
        if not info:
            return {}, []
        eps = get_episodes(self._sess, self._token, cid)
        return info, eps

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        cid = series.get("id", "")
        ep_order = int(chapter.get("order", chapter.get("id", 1)))
        pages = get_pages(self._sess, self._token, cid, ep_order)
        return [_img_url(pg.get("media", {})) for pg in pages]

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        hdrs = {"Referer": referer or BASE_URL, "User-Agent": "okhttp/3.8.1"}
        for attempt in range(3):
            try:
                r = self._sess.get(url, headers=hdrs, timeout=30)
                if r.status_code == 200:
                    return r.content
            except Exception:
                pass
            time.sleep(1 + attempt)
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        return BASE_URL
