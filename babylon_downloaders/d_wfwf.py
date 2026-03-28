"""
d_wfwf.py — wfwf448.com downloader (sin menú)
Dual mode: Webtoon (ing/list/view) y Manhwa (cm/cl/cv).

FIXES 2026-03:
  - Detección de dominio más robusta (no requiere "toon=" en homepage)
  - Lista de candidatos ampliada (448-490 + dominios alternativos)
  - Descubrimiento secuencial de dominio cuando fallan los candidatos
  - Regex de capítulos acepta &amp; en HTML crudo
  - Regex de capítulos: &title= ahora es opcional
  - Paginación real de capítulos (fetches hasta max_page)
  - get_series / get_chapter_images aceptan mode como str o Mode object
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

# Expandimos la lista: dominio actual es wfwf448, rota ~cada 5 días
_BASE_CANDIDATES = (
    # Números secuenciales desde el conocido (448) con margen amplio
    [f"https://wfwf{n}.com/" for n in range(448, 510)]
    + [
        # Dominios alternativos documentados
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
)
BASE_URL = "https://wfwf448.com/"  # se sobreescribe en _detect_base_url
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

# Indicadores de que la página es una página real del sitio wfwf
_SITE_KEYWORDS = ("toon=", "wfwf", "lng", "ing", "webtoon", "웹툰", "만화", "manhwa")


def _is_valid_wfwf_response(text: str) -> bool:
    """
    Verifica si una respuesta HTML parece ser una página real de wfwf.
    Más leniente que la comprobación original de solo 'toon='.
    """
    lower = text.lower()
    return any(kw in lower for kw in _SITE_KEYWORDS)


def _detect_base_url(sess: requests.Session) -> str:
    """
    Detecta el dominio activo de wfwf probando candidatos en paralelo.
    Acepta cualquier página 200 que parezca ser del sitio wfwf.
    Si todos fallan, intenta descubrimiento secuencial.
    """
    global BASE_URL

    def _try(candidate: str) -> str:
        try:
            r = sess.get(candidate + "ing", timeout=6)
            if r.status_code == 200 and _is_valid_wfwf_response(r.text):
                return candidate
            # También probar la raíz
            r2 = sess.get(candidate, timeout=5)
            if r2.status_code == 200 and _is_valid_wfwf_response(r2.text):
                return candidate
        except Exception:
            pass
        return ""

    # Probar los primeros 20 candidatos en paralelo (prioridad a los más recientes)
    priority = _BASE_CANDIDATES[:20]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_try, priority))

    for r in results:
        if r:
            BASE_URL = r
            sess.headers.update({"Referer": BASE_URL})
            return r

    # Si fallan, probar el resto secuencialmente
    for candidate in _BASE_CANDIDATES[20:]:
        found = _try(candidate)
        if found:
            BASE_URL = found
            sess.headers.update({"Referer": BASE_URL})
            return found

    # Último recurso: descubrimiento secuencial desde el último número conocido
    m = re.search(r"wfwf(\d+)", BASE_URL)
    if m:
        last_num = int(m.group(1))
        for offset in range(1, 30):
            candidate = f"https://wfwf{last_num + offset}.com/"
            found = _try(candidate)
            if found:
                BASE_URL = found
                sess.headers.update({"Referer": BASE_URL})
                return found

    return BASE_URL  # fallback al default


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


# ── Mode ──────────────────────────────────────────────────────────────────────


class Mode:
    WEBTOON = "webtoon"
    MANHWA = "manhwa"

    def __init__(self, kind):
        # Acepta tanto string como objeto Mode (evita el bug de double-conversion)
        if isinstance(kind, Mode):
            self.kind = kind.kind
        else:
            assert kind in (self.WEBTOON, self.MANHWA), (
                f"Mode inválido: {kind!r}. Debe ser 'webtoon' o 'manhwa'."
            )
            self.kind = kind

    @property
    def main_path(self) -> str:
        return "ing" if self.kind == self.WEBTOON else "cm"

    def series_url(self, toon_id: str, enc_title: str) -> str:
        path = "list" if self.kind == self.WEBTOON else "cl"
        safe = urllib.parse.quote(enc_title, safe="%+")
        return f"{BASE_URL}{path}?toon={toon_id}&title={safe}"

    def chapter_url(self, toon_id: str, num: int, enc_title: str) -> str:
        path = "view" if self.kind == self.WEBTOON else "cv"
        safe = urllib.parse.quote(enc_title, safe="%+")
        return f"{BASE_URL}{path}?toon={toon_id}&num={num}&title={safe}{num}%C8%AD"

    def chapter_href_re(self, toon_id: str) -> re.Pattern:
        """
        Regex que acepta tanto & como &amp; y hace &title= opcional.
        Esto permite match tanto en HTML crudo (con &amp;) como en atributos
        decodificados por BS4 (con &).
        """
        path = "view" if self.kind == self.WEBTOON else "cv"
        amp = r"(?:&amp;|&)"
        return re.compile(
            rf"{path}\?toon={re.escape(toon_id)}{amp}num=(\d+)(?:{amp}title=)?",
            re.I,
        )

    def __str__(self) -> str:
        return "Webtoon" if self.kind == self.WEBTOON else "Manhwa"


def _mode_from_item(item: dict) -> Mode:
    """
    Extrae el modo de un dict de forma segura, aceptando strings o objetos Mode.
    """
    mode_val = item.get("mode", Mode.WEBTOON)
    return Mode(mode_val)  # Mode.__init__ ya maneja ambos casos


# ── Series list ───────────────────────────────────────────────────────────────


def _parse_series_from_html(html: str, mode: Mode) -> list[dict]:
    path_kw = "list" if mode.kind == Mode.WEBTOON else "cl"
    main_kw = mode.main_path
    _nq = r"[^&\s<>]+"
    pat = re.compile(
        r"/"
        + re.escape(path_kw)
        + r"[?&]toon=(\d+)"
        + _nq
        + r"[?&]title=("
        + _nq
        + r")",
        re.I,
    )
    pat2 = re.compile(
        r"/"
        + re.escape(main_kw)
        + r"[?&]toon=(\d+)"
        + _nq
        + r"[?&]title=("
        + _nq
        + r")",
        re.I,
    )
    soup = _soup(html)
    items: list[dict] = []
    seen: set = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = pat.search(href) or pat2.search(href)
        if not m:
            continue
        toon_id, enc_title = m.group(1), m.group(2)
        if toon_id in seen:
            continue
        seen.add(toon_id)
        text = a.get_text(" ", strip=True)
        title = urllib.parse.unquote(enc_title)
        if text and "더 읽기" not in text and len(text) > 1:
            title = text.split("/")[0].strip() or title
        items.append(
            {
                "id": toon_id,
                "toon_id": toon_id,
                "encoded_title": enc_title,
                "title": title,
                "mode": mode.kind,
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


# ── Series page ───────────────────────────────────────────────────────────────


def _parse_series_page(
    html: str, toon_id: str, enc_title: str, mode: Mode
) -> tuple[str, list[dict]]:
    """
    Parsea la página de una serie y extrae título + lista de capítulos.
    Estrategias de extracción:
    1. BS4: busca <a href="..."> con el patrón correcto (ampersand decodificado)
    2. Raw HTML scan: busca el patrón con &amp; (HTML crudo)
    Ambas estrategias usan el regex mejorado que acepta ambos encodings.
    """
    soup = _soup(html)
    title = urllib.parse.unquote(enc_title)
    for sel in ["h1", ".toon-title", ".series-title", "#toon_title", ".title", "h2"]:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True) and len(node.get_text(strip=True)) > 1:
            title = node.get_text(strip=True)
            break

    chap_re = mode.chapter_href_re(toon_id)
    seen_nums: set = set()
    chapters: list[dict] = []

    def _add_from_html(html_text: str) -> None:
        # Estrategia 1: atributos href decodificados por BS4
        s = _soup(html_text)
        for a in s.find_all("a", href=True):
            mm = chap_re.search(a["href"])
            if not mm:
                continue
            num = int(mm.group(1))
            if num in seen_nums or num == 0:
                continue
            seen_nums.add(num)
            raw_text = a.get_text(" ", strip=True)
            chap_title = _NOISE_RE.sub(" ", raw_text).strip() or f"Cap {num}"
            chapters.append({"id": str(num), "num": num, "title": chap_title})

        # Estrategia 2: scan del HTML crudo (captura JS-rendered o atributos sin parsear)
        # El regex ya acepta &amp; gracias a (?:&amp;|&) en chapter_href_re
        for mm in chap_re.finditer(html_text):
            num = int(mm.group(1))
            if num not in seen_nums and num != 0:
                seen_nums.add(num)
                chapters.append({"id": str(num), "num": num, "title": f"Cap {num}"})

    _add_from_html(html)

    # Detectar paginación y obtener páginas adicionales si existen
    more_pat = re.compile(r"[?&]p=(\d+)", re.I)
    max_page = 1
    for a in soup.find_all("a", href=True):
        mm = more_pat.search(a["href"])
        if mm:
            max_page = max(max_page, int(mm.group(1)))

    # FIX: ahora SÍ se buscan las páginas adicionales (bug original: solo leía max_page)
    if max_page > 1:
        for page_num in range(2, max_page + 1):
            sep = "&" if "?" in mode.series_url(toon_id, enc_title) else "?"
            page_url = mode.series_url(toon_id, enc_title) + f"{sep}p={page_num}"
            page_html = _fetch_html(_current_sess, page_url)
            if page_html:
                _add_from_html(page_html)

    chapters.sort(key=lambda c: c["num"], reverse=True)
    return title, chapters


# Variable de sesión global para que _parse_series_page pueda hacer requests adicionales
_current_sess: Optional[requests.Session] = None


# ── Images ────────────────────────────────────────────────────────────────────


def _extract_images(html: str) -> list[str]:
    m64 = re.search(r"var\s+toon_img\s*=\s*['\"]([A-Za-z0-9+/=]+)['\"];", html)
    if m64:
        try:
            decoded = base64.b64decode(m64.group(1)).decode("utf-8", errors="replace")
            soup2 = _soup(decoded)
            urls = [
                str(img["src"])
                for img in soup2.find_all("img", src=True)
                if str(img["src"]).startswith("http")
                and not any(p in str(img["src"]) for p in _UI_PATHS)
            ]
            if urls:
                return list(dict.fromkeys(urls))
        except Exception:
            pass
    cdn = [
        u
        for u in dict.fromkeys(_CDN_RE.findall(html))
        if not any(p in u for p in _UI_PATHS)
    ]
    if cdn:
        return cdn
    soup = _soup(html)
    scope = soup.select_one("#toon_img") or soup
    VALID = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    candidates = []
    for img in scope.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if (
            src.startswith("http")
            and not any(p in src for p in _UI_PATHS)
            and any(src.lower().endswith(e) for e in VALID)
        ):
            candidates.append(src)
    return list(dict.fromkeys(candidates))


# ══════════════════════════════════════════════════════════════════════════════
#  CLASE PÚBLICA
# ══════════════════════════════════════════════════════════════════════════════
class DownloaderWfwf(BaseDownloader):
    NAME = "WFWF  (wfwf448.com)"
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()
        # Registrar la sesión globalmente para que _parse_series_page pueda usarla
        global _current_sess
        _current_sess = self._sess

    def search(self, query: str) -> list[dict]:
        """Busca en catálogo completo (webtoon + manhwa). Cache entre búsquedas."""
        q = query.lower().strip()
        if not q:
            return []

        # Intentar endpoint de búsqueda directo primero
        direct = self._direct_search(q)
        if direct:
            return direct

        # Fallback: filtrar sobre catálogo completo cacheado
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
        """Intenta buscar directamente en la web."""
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
        # FIX: _mode_from_item acepta tanto string como objeto Mode
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
            "mode": mode.kind,  # siempre guardamos el string, no el objeto
        }
        return meta, chapters

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        toon_id = series.get("toon_id", series.get("id", ""))
        enc_title = series.get("encoded_title", "")
        num = chapter.get("num", int(chapter.get("id", 0)))
        # FIX: _mode_from_item acepta tanto string como objeto Mode
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
