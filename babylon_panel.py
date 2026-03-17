"""
babylon_panel.py  —  Panel multi-sitio para BBSL
Integración correcta y específica para cada scraper.

Funciones usadas por sitio:
  18mh      : fetch_html() + _parse_cards()  |  secciones: hots/dayup/newss/manhwa
  bakamh    : search(q) / get_catalog(genre,sort)  |  get_genres() dinámico
  baozimh   : search_series(q) / _fetch_api_page(mirror,type,region,state,page)
  dumanwu   : DumanwuLogic.search(q) / _load_sort(sort_id, sort_name)
  hitomi    : search_query(q) / fetch_catalog_ids(language) + load_meta_batch()
  mangafox  : search_series(q) / fetch_full_catalog(max_pages=2)
  manhuagui : search(q) / browse_page(region,genre,audience,status)
  picacomic : search(q,sort) / get_comics_by_category(cat,sort) + get_categories() dinámico
  toonkor   : search_global(q) / load_full_catalog()
  wfwf      : search_series(q,mode) / fetch_series_list(mode) / fetch_full_catalog()
"""

from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, cast

from config import Config, resource_path
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE FILTROS POR SITIO
#  Cada entrada: {"id": str, "label": str, "options": [(display, value), ...]}
#  Las marcadas con "dynamic": True se rellenan desde la red al abrir el panel.
# ══════════════════════════════════════════════════════════════════════════════

SITE_FILTER_CONFIG: Dict[str, List[Dict]] = {
    "18mh": [
        {
            "id": "section",
            "label": "Sección",
            "options": [
                ("Populares", "hots"),
                ("Actualizados", "dayup"),
                ("Recientes", "newss"),
                ("Manhwa", "manga-genre/hanman"),
            ],
        }
    ],
    "bakamh": [
        {
            "id": "sort",
            "label": "Ordenar",
            "options": [
                ("Recientes", "latest"),
                ("A-Z", "alphabet"),
                ("Mejor rating", "rating"),
                ("Tendencia", "trending"),
                ("Más vistos", "views"),
                ("Nuevos", "new-manga"),
            ],
        },
        {
            "id": "genre",
            "label": "Género",
            "options": [("Cargando…", "")],
            "dynamic": True,
        },
    ],
    "baozimh": [
        {
            "id": "region",
            "label": "Región",
            "options": [
                ("Todos", "all"),
                ("China", "cn"),
                ("Japón", "jp"),
                ("Corea", "kr"),
                ("Occidente", "en"),
            ],
        },
        {
            "id": "state",
            "label": "Estado",
            "options": [
                ("Todos", "all"),
                ("En curso", "serial"),
                ("Completo", "pub"),
            ],
        },
        {
            "id": "type_",
            "label": "Género",
            "options": [
                ("Todos", "all"),
                ("Romance", "lianai"),
                ("Amor puro", "chunai"),
                ("Antiguo", "gufeng"),
                ("Poderes", "yineng"),
                ("Suspenso", "xuanyi"),
                ("Drama", "juqing"),
                ("Sci-Fi", "kehuan"),
                ("Fantasía", "qihuan"),
                ("Xuan Huan", "xuanhuan"),
                ("Isekai", "chuanyue"),
                ("Aventura", "mouxian"),
                ("Misterio", "tuili"),
                ("Artes M.", "wuxia"),
                ("Pelea", "gedou"),
                ("Guerra", "zhanzheng"),
                ("Acción", "rexie"),
                ("Comedia", "gaoxiao"),
                ("Prot. F.", "danuzhu"),
                ("Ciudad", "dushi"),
                ("CEO", "zongcai"),
                ("Harén", "hougong"),
                ("Cotidiano", "richang"),
                ("Manhwa", "hanman"),
                ("Shonen", "shaonian"),
                ("Otros", "qita"),
            ],
        },
    ],
    "dumanwu": [
        {
            "id": "sort_id",
            "label": "Categoría",
            "options": [
                ("Aventura", "1"),
                ("Acción", "2"),
                ("Ciudad", "3"),
                ("Xuan Huan", "4"),
                ("Suspenso", "5"),
                ("BL", "6"),
                ("Romance", "7"),
                ("Vida", "8"),
                ("Comedia", "9"),
                ("Isekai", "10"),
                ("Cultivo", "11"),
                ("Harén", "12"),
                ("Prot. F.", "13"),
                ("Antiguo", "14"),
                ("En curso", "15"),
                ("Completo", "16"),
            ],
        }
    ],
    "hitomi": [
        {
            "id": "language",
            "label": "Idioma",
            "options": [
                ("Todos", "all"),
                ("Japonés", "japanese"),
                ("Inglés", "english"),
                ("Chino", "chinese"),
                ("Coreano", "korean"),
                ("Español", "spanish"),
                ("Francés", "french"),
                ("Alemán", "german"),
                ("Italiano", "italian"),
                ("Ruso", "russian"),
                ("Tailandés", "thai"),
                ("Indonesio", "indonesian"),
                ("Vietnamita", "vietnamese"),
            ],
        }
    ],
    "mangafox": [],
    "manhuagui": [
        {
            "id": "region",
            "label": "Región",
            "options": [
                ("Todos", ""),
                ("Japón", "japan"),
                ("Corea", "korea"),
                ("China", "china"),
                ("HK/TW", "hongkong"),
                ("Occidente", "europe"),
                ("Otros", "other"),
            ],
        },
        {
            "id": "genre",
            "label": "Género",
            "options": [
                ("Todos", ""),
                ("Acción", "rexue"),
                ("Aventura", "maoxian"),
                ("Fantasía", "mohuan"),
                ("Comedia", "gaoxiao"),
                ("Cute", "mengxi"),
                ("Romance", "aiqing"),
                ("Sci-Fi", "kehuan"),
                ("Magia", "mofa"),
                ("Pelea", "gedou"),
                ("Artes M.", "wuxia"),
                ("Guerra", "zhanzheng"),
                ("Deportes", "jingji"),
                ("Escolar", "xiaoyuan"),
                ("Vida", "shenghuo"),
                ("Historia", "lishi"),
                ("BL", "danmei"),
                ("GL", "baihe"),
                ("Harén", "hougong"),
                ("Terror", "kongbu"),
                ("Detective", "tuili"),
                ("Suspenso", "xuanyi"),
                ("4-Koma", "sige"),
                ("Social", "shehui"),
            ],
        },
        {
            "id": "audience",
            "label": "Público",
            "options": [
                ("Todos", ""),
                ("Chicas", "shaonv"),
                ("Chicos", "shaonian"),
                ("Jóvenes", "qingnian"),
                ("Niños", "ertong"),
                ("General", "tongyong"),
            ],
        },
        {
            "id": "status",
            "label": "Estado",
            "options": [
                ("Todos", ""),
                ("En curso", "lianzai"),
                ("Completo", "wanjie"),
            ],
        },
    ],
    "picacomic": [
        {
            "id": "sort",
            "label": "Ordenar",
            "options": [
                ("Más nuevos", "dd"),
                ("Por defecto", "ua"),
                ("Más viejos", "da"),
                ("Más likes", "ld"),
                ("Más vistos", "vd"),
            ],
        },
        {
            "id": "category",
            "label": "Categoría",
            "options": [("Cargando…", "")],
            "dynamic": True,
        },
    ],
    "toonkor": [],
    "wfwf": [
        {
            "id": "mode",
            "label": "Tipo",
            "options": [
                ("Ambos", "both"),
                ("Webtoon", "webtoon"),
                ("Manhwa", "manhwa"),
            ],
        }
    ],
}

# ══════════════════════════════════════════════════════════════════════════════
#  CARGA Y CACHÉ DE MÓDULOS
# ══════════════════════════════════════════════════════════════════════════════

_DL_DIR = os.path.join(os.path.dirname(__file__), "babylon_downloaders")
if _DL_DIR not in sys.path:
    sys.path.insert(0, _DL_DIR)

_module_cache: Dict[str, Any] = {}
_site_init_done: Dict[str, bool] = {}


def _load_downloader(site_type: str, filename: str) -> Any:
    """Carga (con caché) un módulo downloader desde babylon_downloaders/."""
    if site_type in _module_cache:
        return _module_cache[site_type]

    filepath = os.path.join(_DL_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Downloader no encontrado: {filepath}")

    mod_name = f"_babylon_dl_{site_type}"
    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo crear spec para: {filepath}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _module_cache[site_type] = mod
    return mod


def _ensure_site_initialized(site_type: str, mod: Any) -> None:
    """Inicialización avanzada por sitio (se ejecuta solo una vez por sesión)."""
    if _site_init_done.get(site_type):
        return

    try:
        if site_type == "baozimh":
            site_org = getattr(mod, "SITE_ORG", "https://baozimh.org")
            if getattr(mod, "SESSION_ORG", None) is None:
                mod.SESSION_ORG = mod._make_session(site_org)
                logging.info("[Babylon] baozimh: SESSION_ORG iniciada.")
            if not getattr(mod, "_ACTIVE_MIRROR", ""):
                mod._ACTIVE_MIRROR = mod._find_active_mirror()
                logging.info(f"[Babylon] baozimh: mirror={mod._ACTIVE_MIRROR}")
            if getattr(mod, "SESSION_COM", None) is None:
                try:
                    import requests as _r

                    s = _r.Session()
                    s.headers.update(getattr(mod, "_BASE_HEADERS", {}))
                    if mod._ACTIVE_MIRROR:
                        s.get(mod._ACTIVE_MIRROR + "/", timeout=5)
                    mod.SESSION_COM = s
                except Exception:
                    pass

        elif site_type == "dumanwu":
            if not getattr(mod, "_seeds_cache", []):
                mod._load_seeds()
                logging.info(
                    f"[Babylon] dumanwu: {len(mod._seeds_cache)} semillas cargadas."
                )

        elif site_type == "mangafox":
            if getattr(mod, "SESSION", None) is None:
                mod.SESSION = mod.make_session()
                logging.info("[Babylon] mangafox: SESSION iniciada.")

        elif site_type == "picacomic":
            if not getattr(mod, "_token", ""):
                manual = getattr(mod, "MANUAL_TOKEN", "")
                auto_u = getattr(mod, "AUTO_USER", "")
                auto_p = getattr(mod, "AUTO_PASS", "")
                if manual:
                    mod._token = manual
                    logging.info("[Babylon] picacomic: token manual cargado.")
                elif auto_u and auto_p:
                    try:
                        ok = mod.login(auto_u, auto_p)
                        logging.info(
                            f"[Babylon] picacomic: auto-login {'OK' if ok else 'FALLÓ'}."
                        )
                    except Exception as e:
                        logging.warning(f"[Babylon] picacomic: login error: {e}")

        elif site_type == "toonkor":
            if hasattr(mod, "auto_update_domain"):
                try:
                    mod.auto_update_domain()
                    logging.info(f"[Babylon] toonkor: dominio={mod.BASE_URL}")
                except Exception:
                    pass

        elif site_type == "wfwf":
            if getattr(mod, "SESSION", None) is None:
                mod.SESSION = mod.make_session()
                logging.info("[Babylon] wfwf: SESSION iniciada.")

    except Exception as e:
        logging.warning(f"[Babylon] Init parcial {site_type}: {e}")

    _site_init_done[site_type] = True


# ══════════════════════════════════════════════════════════════════════════════
#  FUNCIÓN CENTRAL: search_site
#  Devuelve: [{"title": str, "slug": str, "url": str}]
# ══════════════════════════════════════════════════════════════════════════════


def search_site(
    site: Dict[str, str],
    query: str,
    filters: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    if filters is None:
        filters = {}

    try:
        mod = _load_downloader(site["type"], site["file"])
        _ensure_site_initialized(site["type"], mod)
    except Exception as e:
        logging.error(f"❌ BABYLON [{site['name']}]: {e}")
        return []

    t = site["type"]
    results: List[Dict[str, str]] = []

    try:
        # ── 18MH ─────────────────────────────────────────────────────────────
        # fetch_html(url) → str | None
        # _parse_cards(html) → [{"slug": str, "title": str}]
        # Búsqueda: /s/{query}   |   Catálogo: /{section}
        if t == "18mh":
            from urllib.parse import quote as _q

            site_url: str = getattr(mod, "SITE_URL", "https://18mh.org")

            if query:
                url = f"{site_url}/s/{_q(query)}"
            else:
                section = filters.get("section", "hots")
                url = f"{site_url}/{section}"

            html = mod.fetch_html(url)
            raw = mod._parse_cards(html) if html else []

            results = [
                {
                    "title": r.get("title", r.get("slug", "")),
                    "slug": r.get("slug", ""),
                    "url": f"{site_url}/manga/{r['slug']}",
                }
                for r in raw
                if r.get("slug")
            ]

        # ── BAKAMH ───────────────────────────────────────────────────────────
        # search(query, page=1) → (items_list, total_pages)
        # get_catalog(page=1, genre_slug="", sort="latest") → (items_list, total_pages)
        # items: {"slug": str, "title": str, "latest": str}
        elif t == "bakamh":
            base_url: str = getattr(mod, "BASE_URL", "https://bakamh.com")

            if query:
                items, _ = mod.search(query, 1)
            else:
                genre_slug = filters.get("genre", "")
                sort = filters.get("sort", "latest")
                items, _ = mod.get_catalog(page=1, genre_slug=genre_slug, sort=sort)

            results = [
                {
                    "title": r.get("title", ""),
                    "slug": r.get("slug", ""),
                    "url": f"{base_url}/manga/{r['slug']}/",
                }
                for r in items
                if r.get("slug")
            ]

        # ── BAOZIMH ──────────────────────────────────────────────────────────
        # search_series(query) → [{"slug": str, "title": str}]
        # _fetch_api_page(mirror, type_, region, state, page)
        #   → [{"slug": str, "title": str, "author": str, "genres": list}]
        elif t == "baozimh":
            site_org: str = getattr(mod, "SITE_ORG", "https://baozimh.org")

            if query:
                raw = mod.search_series(query)
                results = [
                    {
                        "title": r.get("title", r.get("slug", "")),
                        "slug": r.get("slug", ""),
                        "url": f"{site_org}/manga/{r['slug']}",
                    }
                    for r in raw
                    if r.get("slug")
                ]
            else:
                mirror = getattr(mod, "_ACTIVE_MIRROR", "") or "https://www.baozimh.com"
                type_ = filters.get("type_", "all")
                region = filters.get("region", "all")
                state = filters.get("state", "all")

                # 3 páginas (~108 series) para una vista rápida con los filtros elegidos
                raw = []
                for pg in range(1, 4):
                    page_items = mod._fetch_api_page(mirror, type_, region, state, pg)
                    if not page_items:
                        break
                    raw.extend(page_items)

                results = [
                    {
                        "title": r.get("title", r.get("name", r.get("slug", ""))),
                        "slug": r.get("slug", ""),
                        "url": f"{site_org}/manga/{r['slug']}",
                    }
                    for r in raw
                    if r.get("slug")
                ]

        # ── DUMANWU ──────────────────────────────────────────────────────────
        # DumanwuLogic.search(query) → [{"slug": str, "title": str}]
        # _load_sort(sort_id: int, sort_name: str)
        #   → [{"slug": str, "title": str, "latest": str}]
        # _DW_SORTS = {1: "冒险", ..., 16: "完结"}
        elif t == "dumanwu":
            base_url = getattr(mod, "BASE_URL", "https://dumanwu.com")
            dw_sorts: Dict[int, str] = getattr(mod, "_DW_SORTS", {})

            if query:
                logic = mod.DumanwuLogic()
                raw = logic.search(query)
            else:
                sort_id_str = filters.get("sort_id", "1")
                sort_id = int(sort_id_str) if sort_id_str.isdigit() else 1
                sort_name = dw_sorts.get(sort_id, "")
                raw = mod._load_sort(sort_id, sort_name)

            results = [
                {
                    "title": r.get("title", r.get("slug", "")),
                    "slug": r.get("slug", ""),
                    "url": f"{base_url}/{r['slug']}/",
                }
                for r in raw
                if r.get("slug")
            ]

        # ── HITOMI ───────────────────────────────────────────────────────────
        # search_query(query) → [int]   (IDs de galería)
        # fetch_catalog_ids(language="all") → [int]
        # load_meta_batch([gids]) → carga METADATA_CACHE
        # _title(gid) → str
        elif t == "hitomi":
            language = filters.get("language", "all")

            if query:
                ids: List[int] = mod.search_query(query)[:40]
            else:
                ids = mod.fetch_catalog_ids(language=language)[:40]

            if ids:
                mod.load_meta_batch(ids)

            results = [
                {
                    "title": mod._title(gid) or f"Gallery #{gid}",
                    "slug": str(gid),
                    "url": f"https://hitomi.la/galleries/{gid}.html",
                }
                for gid in ids
            ]

        # ── MANGAFOX (FANFOX) ────────────────────────────────────────────────
        # search_series(query) → [{"slug": str, "title": str, "rating": str, "status": str}]
        # fetch_full_catalog(max_pages=N) → same
        elif t == "mangafox":
            base_url = getattr(mod, "BASE_URL", "https://fanfox.net")

            if query:
                raw = mod.search_series(query)
            else:
                raw = mod.fetch_full_catalog(max_pages=2)

            results = [
                {
                    "title": r.get("title", ""),
                    "slug": r.get("slug", ""),
                    "url": f"{base_url}/manga/{r['slug']}/",
                }
                for r in raw
                if r.get("slug")
            ]

        # ── MANHUAGUI ────────────────────────────────────────────────────────
        # search(query, page=1) → (results_list, total_pages)
        #   results_list: [{"id": int, "title": str, "last": str}]
        # browse_page(page=1, region="", genre="", audience="", status="")
        #   → ([{"id": int, "title": str, "last": str}], total_pages)
        elif t == "manhuagui":
            base_url = getattr(mod, "BASE", "https://www.manhuagui.com")

            if query:
                series_list, _ = mod.search(query, page=1)
            else:
                region = filters.get("region", "")
                genre = filters.get("genre", "")
                audience = filters.get("audience", "")
                status = filters.get("status", "")
                series_list, _ = mod.browse_page(
                    page=1,
                    region=region,
                    genre=genre,
                    audience=audience,
                    status=status,
                )

            results = [
                {
                    "title": r.get("title", ""),
                    "slug": str(r.get("id", r.get("slug", ""))),
                    "url": f"{base_url}/comic/{r.get('id', r.get('slug', ''))}/",
                }
                for r in series_list
                if r.get("id") or r.get("slug")
            ]

        # ── PICACOMIC ────────────────────────────────────────────────────────
        # search(keyword, page=1, sort="dd", categories=None)
        #   → ([items], pages)  items vía _parse_comic_stub:
        #   {"id","title","author","pages","eps","finished","likes","categories"}
        # get_comics_by_category(category, page=1, sort="dd") → ([items], pages)
        # fetch_full_catalog(sort="dd", page_limit=N) → [items]
        elif t == "picacomic":
            if not getattr(mod, "_token", ""):
                return [
                    {
                        "title": "⚠ PicaComic sin token – configura MANUAL_TOKEN en el script",
                        "slug": "",
                        "url": "https://picacomic.com",
                    }
                ]

            sort = filters.get("sort", "dd")
            category = filters.get("category", "")

            if query:
                cats_arg = [category] if category else None
                items, _ = mod.search(query, page=1, sort=sort, categories=cats_arg)
            elif category:
                items, _ = mod.get_comics_by_category(category, page=1, sort=sort)
            else:
                # Sin query ni categoría: primeras 5 páginas del catálogo global (~100 series)
                items = mod.fetch_full_catalog(sort=sort, page_limit=5)

            results = [
                {
                    "title": r.get("title", ""),
                    "slug": r.get("id", r.get("_id", "")),
                    "url": f"https://picacomic.com/comics/{r.get('id', r.get('_id', ''))}",
                }
                for r in items
                if r.get("id") or r.get("_id")
            ]

        # ── TOONKOR ──────────────────────────────────────────────────────────
        # search_global(query) → [{"slug": str, "title": str}]
        # load_full_catalog(workers=8) → [{"slug": str, "title": str}]
        elif t == "toonkor":
            base = getattr(mod, "BASE_URL", "https://tkor098.com/").rstrip("/")

            if query:
                raw = mod.search_global(query)
            else:
                raw = mod.load_full_catalog(workers=8)

            results = [
                {
                    "title": r.get("title", r.get("slug", "")),
                    "slug": r.get("slug", ""),
                    "url": f"{base}/{r['slug']}",
                }
                for r in raw
                if r.get("slug")
            ]

        # ── WFWF ─────────────────────────────────────────────────────────────
        # Mode("webtoon") | Mode("manhwa")
        # search_series(query, mode) → [{"toon_id","encoded_title","title","mode"}]
        # fetch_series_list(mode, workers=10) → same structure
        # fetch_full_catalog(workers=10) → same + field "mode" para distinguir
        elif t == "wfwf":
            Mode = mod.Mode
            mode_val = filters.get("mode", "both")

            def _norm_wfwf(r: Dict) -> Dict:
                t_id = r.get("toon_id", r.get("slug", ""))
                enc = r.get("encoded_title", "")
                kind = r.get("mode", Mode.WEBTOON)
                try:
                    url_str = Mode(kind).series_url(t_id, enc) if enc else ""
                except Exception:
                    url_str = ""
                # El slug codifica los tres valores para poder reconstruirlos al lanzar
                return {
                    "title": r.get("title", ""),
                    "slug": f"{t_id}|||{enc}|||{kind}",
                    "url": url_str,
                }

            if query:
                if mode_val == "both":
                    raw_w = mod.search_series(query, Mode(Mode.WEBTOON))
                    raw_m = mod.search_series(query, Mode(Mode.MANHWA))
                    raw = raw_w + raw_m
                else:
                    raw = mod.search_series(query, Mode(mode_val))
            else:
                if mode_val == "both":
                    raw = mod.fetch_full_catalog(workers=8)
                else:
                    raw = mod.fetch_series_list(Mode(mode_val), workers=8)

            results = [_norm_wfwf(r) for r in raw if r.get("toon_id") or r.get("slug")]

    except Exception as e:
        import traceback as _tb

        logging.error(f"❌ BABYLON [{site['name']}]: {e}\n{_tb.format_exc()}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
#  WORKER: BÚSQUEDA / LISTADO
# ══════════════════════════════════════════════════════════════════════════════


class _SearchSignals(QObject):
    finished = Signal(list)
    error = Signal(str)


class BabylonSearchWorker(QRunnable):
    def __init__(
        self, site: Dict[str, str], query: str, filters: Dict[str, str]
    ) -> None:
        super().__init__()
        self.site = site
        self.query = query
        self.filters = filters
        self.signals = _SearchSignals()

    def run(self) -> None:
        try:
            res = search_site(self.site, self.query, self.filters)
            self.signals.finished.emit(res)
        except Exception as exc:
            self.signals.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
#  WORKER: OPCIONES DINÁMICAS (géneros bakamh / categorías picacomic)
# ══════════════════════════════════════════════════════════════════════════════


class _DynOptsSignals(QObject):
    finished = Signal(str, list)  # (filter_id, [(display, value), ...])


class BabylonDynamicOptsWorker(QRunnable):
    def __init__(self, site: Dict[str, str]) -> None:
        super().__init__()
        self.site = site
        self.signals = _DynOptsSignals()

    def run(self) -> None:
        t = self.site["type"]
        try:
            mod = _load_downloader(t, self.site["file"])
            _ensure_site_initialized(t, mod)

            if t == "bakamh":
                # get_genres() → [{"name": str, "slug": str}]
                genres = mod.get_genres()
                options = [("Todos", "")] + [(g["name"], g["slug"]) for g in genres]
                self.signals.finished.emit("genre", options)

            elif t == "picacomic":
                if not getattr(mod, "_token", ""):
                    return
                # get_categories() → [{_id, title, isWeb, ...}]
                cats = mod.get_categories()
                options = [("Todas", "")] + [
                    (c.get("title", ""), c.get("title", ""))
                    for c in cats
                    if not c.get("isWeb", False) and c.get("title")
                ]
                self.signals.finished.emit("category", options)

        except Exception as e:
            logging.warning(f"[Babylon] Opciones dinámicas {t}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS: TERMINAL EXTERNO
# ══════════════════════════════════════════════════════════════════════════════


def _open_in_terminal(args: List[str], cwd: str, title: str = "") -> str:
    """Abre el scraper en una nueva ventana de terminal (CLI interactivo)."""
    try:
        if sys.platform == "win32":
            inner = " ".join(
                f'"{a}"' if " " in str(a) and not str(a).startswith('"') else str(a)
                for a in args
            )
            cmd_str = f'start "{title}" cmd /k {inner}'
            subprocess.Popen(
                cmd_str,
                cwd=cwd,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                shell=True,
            )
        else:
            launched = False
            for term in [
                ["gnome-terminal", f"--title={title}", "--"] + args,
                ["xterm", "-title", title, "-e"] + args,
                ["konsole", "--title", title, "-e"] + args,
                ["x-terminal-emulator", "-e"] + args,
            ]:
                try:
                    subprocess.Popen(term, cwd=cwd)
                    launched = True
                    break
                except FileNotFoundError:
                    continue
            if not launched:
                subprocess.Popen(args, cwd=cwd)
        return f"✅ Terminal abierto: {title}"
    except Exception as exc:
        return f"❌ Error al abrir terminal: {exc}"


def _build_launch_args(
    site_type: str, filepath: str, item: Dict[str, str]
) -> List[str]:
    """
    Construye los args del subproceso.
    Solo bakamh admite --url; el resto abre el menú interactivo sin args.
    """
    args = [sys.executable, filepath]
    if site_type == "bakamh" and item.get("url"):
        args += ["--url", item["url"]]
    return args


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL DE DETALLE DE SITIO
# ══════════════════════════════════════════════════════════════════════════════

_SITE_HINTS: Dict[str, str] = {
    "18mh": "Busca por nombre o elige una sección del catálogo y pulsa Listar.",
    "bakamh": "Busca por nombre, o elige género/orden y pulsa Listar.",
    "baozimh": "Busca por nombre, o aplica filtros género/región/estado y pulsa Listar.",
    "dumanwu": "Busca por nombre, o elige una categoría y pulsa Listar.",
    "hitomi": "Elige idioma y pulsa Listar, o escribe tags como 'language:japanese female:yuri'.",
    "mangafox": "Busca por nombre, o aplica filtros y pulsa Listar.",
    "manhuagui": "Busca por nombre, o filtra región/género/público/estado y pulsa Listar.",
    "picacomic": "Busca por nombre, o elige categoría/orden y pulsa Listar.",
    "toonkor": "Busca por nombre, o pulsa Listar para ver todas las series.",
    "wfwf": "Elige Webtoon/Manhwa/Ambos y pulsa Listar, o escribe un nombre.",
}


class BabylonSiteDetailPanel(QWidget):
    back_requested = Signal()

    def __init__(
        self,
        site: Dict[str, str],
        parent: Optional[QWidget] = None,
        title_font: Optional[QFont] = None,
        body_font: Optional[QFont] = None,
    ) -> None:
        super().__init__(parent)
        self.site = site
        self.title_font = title_font
        self.body_font = body_font
        self._results: List[Dict[str, str]] = []
        self._dest_dir: str = ""
        self._filter_combos: Dict[str, QComboBox] = {}
        self._pool = QThreadPool.globalInstance()
        self._build_ui()
        self._load_dynamic_options()

    # ── Construcción de la UI ─────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # Cabecera con Botón Volver y Título
        header_top = QHBoxLayout()
        
        btn_back = QPushButton("← VOLVER")
        if self.body_font:
            btn_back.setFont(self.body_font)
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.setStyleSheet("""
            QPushButton {
                background-color: rgba(60, 60, 60, 150);
                color: white;
                border: 1px solid rgba(150, 0, 150, 100);
                border-radius: 5px;
                padding: 5px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(150, 0, 150, 50);
                border: 1px solid #960096;
            }
        """)
        btn_back.clicked.connect(self.back_requested.emit)
        header_top.addWidget(btn_back)
        header_top.addStretch()

        # Título
        lbl_title = QLabel(f"⬇  {self.site['name']}  —  {self.site.get('url', '')}")
        if self.title_font:
            lbl_title.setFont(self.title_font)
        lbl_title.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        header_top.addWidget(lbl_title)
        root.addLayout(header_top)

        # Pista contextual
        hint = _SITE_HINTS.get(self.site.get("type", ""), "")
        if hint:
            lbl_hint = QLabel(f"ℹ  {hint}")
            lbl_hint.setStyleSheet("color: #888; font-size: 11px;")
            root.addWidget(lbl_hint)

        # Fila de filtros (varía por sitio)
        filter_config = SITE_FILTER_CONFIG.get(self.site.get("type", ""), [])
        if filter_config:
            filter_row = QHBoxLayout()
            filter_row.setSpacing(8)
            for f_def in filter_config:
                lbl = QLabel(f_def["label"] + ":")
                if self.body_font:
                    lbl.setFont(self.body_font)
                combo = QComboBox()
                combo.setMinimumWidth(130)
                for display, val in f_def["options"]:
                    combo.addItem(display, val)
                if self.body_font:
                    combo.setFont(self.body_font)
                self._filter_combos[f_def["id"]] = combo
                filter_row.addWidget(lbl)
                filter_row.addWidget(combo)
            filter_row.addStretch()
            root.addLayout(filter_row)

        # Fila de búsqueda
        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Buscar por nombre…")
        if self.body_font:
            self._search_input.setFont(self.body_font)
        self._search_input.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search_input, 1)

        for label, slot in [
            ("🔍 Buscar", self._do_search),
            ("📋 Listar", self._do_list_all),
        ]:
            btn = QPushButton(label)
            if self.body_font:
                btn.setFont(self.body_font)
            btn.clicked.connect(slot)
            search_row.addWidget(btn)
        root.addLayout(search_row)

        # Estado
        self._lbl_status = QLabel("")
        if self.body_font:
            self._lbl_status.setFont(self.body_font)
        root.addWidget(self._lbl_status)

        # Resultados
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._results_container = QWidget()
        self._results_layout = QVBoxLayout(self._results_container)
        self._results_layout.setSpacing(5)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.addStretch()
        scroll.setWidget(self._results_container)
        root.addWidget(scroll, 1)

        # Pie: carpeta destino
        footer = QHBoxLayout()
        self._lbl_dest = QLabel("📁 Destino: sin seleccionar")
        if self.body_font:
            self._lbl_dest.setFont(self.body_font)
        footer.addWidget(self._lbl_dest, 1)
        btn_dest = QPushButton("📁 Elegir carpeta")
        if self.body_font:
            btn_dest.setFont(self.body_font)
        btn_dest.clicked.connect(self._choose_dest)
        footer.addWidget(btn_dest)
        root.addLayout(footer)

    # ── Opciones dinámicas ────────────────────────────────────────────────────
    def _load_dynamic_options(self) -> None:
        t = self.site.get("type", "")
        if t in ("bakamh", "picacomic"):
            w = BabylonDynamicOptsWorker(self.site)
            w.signals.finished.connect(self._on_dynamic_options)
            self._pool.start(w)

    def _on_dynamic_options(self, filter_id: str, options: List[tuple]) -> None:
        combo = self._filter_combos.get(filter_id)
        if combo is None:
            return
        combo.clear()
        for display, val in options:
            combo.addItem(display, val)
        logging.info(
            f"[Babylon] {self.site['name']}: {len(options)} opciones para '{filter_id}'"
        )

    # ── Slots ─────────────────────────────────────────────────────────────────
    def _do_search(self) -> None:
        query = self._search_input.text().strip()
        if not query:
            return
        self._run_worker(query)

    def _do_list_all(self) -> None:
        self._run_worker("")

    def _get_filters(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for fid, combo in self._filter_combos.items():
            val = combo.currentData()
            out[fid] = val if val is not None else combo.currentText()
        return out

    def _run_worker(self, query: str) -> None:
        self._lbl_status.setText("⏳ Cargando…")
        self._clear_results()
        filters = self._get_filters()
        w = BabylonSearchWorker(self.site, query, filters)
        w.signals.finished.connect(self._on_results)
        w.signals.error.connect(self._on_error)
        self._pool.start(w)

    def _on_results(self, results: List[Dict[str, str]]) -> None:
        self._results = results
        n = len(results)
        s = f"{n} resultado{'s' if n != 1 else ''} — {self.site['name']}"
        self._lbl_status.setText(s)
        self._clear_results()

        if not results:
            lbl = QLabel("Sin resultados.")
            if self.body_font:
                lbl.setFont(self.body_font)
            self._results_layout.insertWidget(0, lbl)
            return

        for item in results:
            self._results_layout.insertWidget(
                self._results_layout.count() - 1,
                self._make_result_card(item),
            )

    def _on_error(self, msg: str) -> None:
        self._lbl_status.setText(f"❌ Error: {msg}")
        logging.error(f"❌ BABYLON [{self.site['name']}]: {msg}")

    def _choose_dest(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Carpeta destino")
        if folder:
            self._dest_dir = folder
            short = folder if len(folder) <= 50 else "…" + folder[-47:]
            self._lbl_dest.setText(f"📁 {short}")

    # ── Helpers UI ────────────────────────────────────────────────────────────
    def _clear_results(self) -> None:
        while self._results_layout.count() > 1:
            item = self._results_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _make_result_card(self, item: Dict[str, str]) -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(10, 5, 10, 5)

        lbl = QLabel(item.get("title", "(sin título)"))
        if self.body_font:
            lbl.setFont(self.body_font)
        lbl.setWordWrap(True)
        lay.addWidget(lbl, 1)

        btn = QPushButton("⬇ Descargar")
        if self.body_font:
            btn.setFont(self.body_font)
        btn.setFixedWidth(120)
        if not item.get("slug"):
            btn.setEnabled(False)
        btn.setToolTip(
            f"Abre {self.site['name']} en terminal interactivo.\n"
            f"Serie: {item.get('title', '')}"
        )
        btn.clicked.connect(lambda _c=False, i=item: self._launch_downloader(i))
        lay.addWidget(btn)
        return card

    def _launch_downloader(self, item: Dict[str, str]) -> None:
        """Abre el script del scraper en una terminal externa."""
        filepath = os.path.join(_DL_DIR, self.site["file"])
        if not os.path.exists(filepath):
            self._lbl_status.setText(f"❌ Script no encontrado: {self.site['file']}")
            return

        title = item.get("title", "descarga")[:50]
        args = _build_launch_args(self.site["type"], filepath, item)
        cwd = self._dest_dir if self._dest_dir else _DL_DIR

        msg = _open_in_terminal(args, cwd=cwd, title=f"{self.site['name']} — {title}")
        self._lbl_status.setText(msg)
        logging.info(f"🚀 BABYLON: {msg}")


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL PRINCIPAL — grilla de sitios
# ══════════════════════════════════════════════════════════════════════════════


class BabylonPanel(QWidget):
    def __init__(
        self,
        parent: Optional[QWidget] = None,
        title_font: Optional[QFont] = None,
        body_font: Optional[QFont] = None,
        adventure_font: Optional[QFont] = None,
    ) -> None:
        super().__init__(parent)
        self.title_font = title_font
        self.body_font = body_font
        self.adventure_font = adventure_font
        self._detail: Optional[BabylonSiteDetailPanel] = None
        self._build_ui()

    def _build_ui(self) -> None:
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)

        self._grid_widget = QWidget()
        gv = QVBoxLayout(self._grid_widget)
        gv.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        cards_w = QWidget()
        cards_w.setStyleSheet("background: transparent; border: none;")
        cards_lay = QVBoxLayout(cards_w)
        cards_lay.setContentsMargins(8, 8, 8, 8)

        grid_container = QWidget()
        self._grid = QGridLayout(grid_container)
        qt_any = cast(Any, Qt)
        self._grid.setAlignment(qt_any.AlignLeft | qt_any.AlignTop)
        self._grid.setSpacing(6)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._fill_grid()

        cards_lay.addWidget(grid_container)
        cards_lay.addStretch()
        scroll.setWidget(cards_w)
        gv.addWidget(scroll, 1)
        self._root.addWidget(self._grid_widget)

    def _fill_grid(self) -> None:
        base_style = (
            "border:1px solid rgba(150,0,150,50); border-radius:8px;"
            " background-color:rgba(30,30,30,150);"
        )
        hover_style = (
            "border:2px solid #960096; border-radius:8px;"
            " background-color:rgba(150,0,150,30);"
        )

        def _enter(l: QLabel, d: QLabel):
            l.setStyleSheet(hover_style)
            d.show()

        def _leave(l: QLabel, d: QLabel):
            l.setStyleSheet(base_style)
            d.hide()

        cols = 6
        sz = (120, 120)
        marg = 25
        qt_any = cast(Any, Qt)

        for i, site in enumerate(Config.BABYLON_SITES):
            try:
                icon_path = resource_path(
                    os.path.join(
                        "BBSL",
                        "herramientas",
                        "ch_downloaders",
                        "babylon",
                        f"{site['type']}.png",
                    )
                )
                pix = QPixmap(icon_path) if os.path.exists(icon_path) else QPixmap()
                if not pix.isNull():
                    scaled = pix.scaled(
                        sz[0] - marg,
                        sz[1] - marg,
                        qt_any.AspectRatioMode.KeepAspectRatio,
                        qt_any.TransformationMode.SmoothTransformation,
                    )
                else:
                    scaled = QPixmap(sz[0] - marg, sz[1] - marg)
                    scaled.fill(Qt.GlobalColor.transparent)

                img_lbl = QLabel()
                img_lbl.setPixmap(scaled)
                if pix.isNull():
                    img_lbl.setText("🌐")
                img_lbl.setFixedSize(*sz)
                img_lbl.setAlignment(qt_any.AlignCenter)
                img_lbl.setStyleSheet(base_style)
                img_lbl.setCursor(qt_any.PointingHandCursor)

                desc_lbl = QLabel(
                    f"<b>{site['name']}</b><br><small>{site['description']}</small>",
                    img_lbl,
                )
                desc_lbl.setFixedSize(*sz)
                desc_lbl.setWordWrap(True)
                desc_lbl.setAlignment(qt_any.AlignCenter)
                desc_lbl.setStyleSheet(
                    "color:white; background-color:rgba(0,0,0,200);"
                    " font-size:11px; padding:8px; border-radius:8px;"
                )
                desc_lbl.hide()
                desc_lbl.setAttribute(
                    qt_any.WidgetAttribute.WA_TransparentForMouseEvents, True
                )

                img_lbl.enterEvent = (  # type: ignore
                    lambda a0, l=img_lbl, d=desc_lbl: _enter(l, d)
                )
                img_lbl.leaveEvent = (  # type: ignore
                    lambda a0, l=img_lbl, d=desc_lbl: _leave(l, d)
                )

                def _make_click(s=site):
                    def _on(_ev):
                        self._open_site(s)

                    return _on

                img_lbl.mousePressEvent = _make_click()  # type: ignore

                row, col = divmod(i, cols)
                self._grid.addWidget(img_lbl, row, col)

            except Exception as e:
                logging.warning(f"[Babylon] Icono {site.get('name', '?')}: {e}")

    def _open_site(self, site: Dict[str, str]) -> None:
        """Abre la vista de detalles para un sitio de Babylon."""
        logging.debug(f"[TRACING] Babylon: Abriendo sitio '{site.get('name')}'")
        if self._detail is not None:
            self._root.removeWidget(self._detail)
            self._detail.deleteLater()
            self._detail = None

        self._detail = BabylonSiteDetailPanel(
            site=site,
            parent=self,
            title_font=self.title_font,
            body_font=self.body_font,
        )
        self._detail.back_requested.connect(self._show_grid)
        self._root.addWidget(self._detail)
        self._grid_widget.hide()
        self._detail.show()

    def _show_grid(self) -> None:
        if self._detail:
            self._detail.hide()
        self._grid_widget.show()
