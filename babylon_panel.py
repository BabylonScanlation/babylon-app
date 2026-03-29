"""
babylon_panel.py  —  Panel multi-sitio COMPLETAMENTE INTEGRADO para BBSL.

Flujo 100% in-app, sin terminales externas:
  Grid → Sitio → Serie → Capítulos → Descarga con barra de progreso

PAGINACIÓN REAL:
  - Catálogo: 1 request por página (fast per-page fetching)
  - Búsqueda: fetch-all una vez, paginar display (sin re-requests)
  - Prev/Next con indicador de página

POR QUÉ ES RÁPIDO AHORA:
  - baozimh: _fetch_api_page(page) → 36 items en ~1s en vez de get_catalog() que tarda minutos
  - dumanwu: GET /sort/N (1 request) + _sortmore(page) en vez de _load_sort() que hace 500 requests
  - wfwf:    caché en memoria → primera carga lenta, páginas 2+ instantáneas
  - resto:   get_catalog_page(page=N) — siempre fue rápido
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import sys
import threading
import zipfile
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple, cast

from config import Config, resource_path
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, QUrl
from PySide6.QtGui import QFont, QPixmap, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

PAGE_SIZE = 20  # Items por página en la UI

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE FILTROS POR SITIO
# ══════════════════════════════════════════════════════════════════════════════

SITE_FILTER_CONFIG: Dict[str, List[Dict]] = {
    "18mh": [],
    "bakamh": [
        {
            "id": "sort",
            "label": "Ordenar",
            "options": [
                ("Recientes", "latest"),
                ("A-Z", "alphabet"),
                ("Rating", "rating"),
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
            "options": [("Todos", "all"), ("En curso", "serial"), ("Completo", "pub")],
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
        },
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
                ("Italiano", "italiano"),
                ("Ruso", "russian"),
                ("Tailandés", "thai"),
                ("Indonesio", "indonesian"),
                ("Vietnamita", "vietnamese"),
            ],
        },
        {
            "id": "type",
            "label": "Tipo",
            "options": [
                ("Todos", ""),
                ("Doujinshi", "doujinshi"),
                ("Manga", "manga"),
                ("Artist CG", "artistcg"),
                ("Game CG", "gamecg"),
                ("Imageset", "imageset"),
            ],
        },
        {
            "id": "order",
            "label": "Orden",
            "options": [
                ("Por defecto", "default"),
                ("Fecha publicación", "date_published"),
                ("Popular: Hoy", "pop_today"),
                ("Popular: Semana", "pop_week"),
                ("Popular: Mes", "pop_month"),
                ("Popular: Año", "pop_year"),
                ("Aleatorio", "random"),
            ],
        },
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
                ("Romance", "aiqing"),
                ("Sci-Fi", "kehuan"),
                ("Pelea", "gedou"),
                ("Artes M.", "wuxia"),
                ("Escolar", "xiaoyuan"),
                ("Vida", "shenghuo"),
                ("Historia", "lishi"),
                ("BL", "danmei"),
                ("GL", "baihe"),
                ("Harén", "hougong"),
                ("Terror", "kongbu"),
                ("Detective", "tuili"),
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
            "options": [("Todos", ""), ("En curso", "lianzai"), ("Completo", "wanjie")],
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
        },
    ],
}

# ══════════════════════════════════════════════════════════════════════════════
#  CARGA Y CACHÉ DE MÓDULOS / INSTANCIAS
# ══════════════════════════════════════════════════════════════════════════════

_DL_DIR = os.path.join(os.path.dirname(__file__), "babylon_downloaders")
if _DL_DIR not in sys.path:
    sys.path.insert(0, _DL_DIR)

_DOWNLOADER_MAP: Dict[str, Tuple[str, str]] = {
    "18mh": ("d_18mh.py", "Downloader18mh"),
    "bakamh": ("d_bakamh.py", "DownloaderBakamh"),
    "baozimh": ("d_baozimh.py", "DownloaderBaozimh"),
    "dumanwu": ("d_dumanwu.py", "DownloaderDumanwu"),
    "hitomi": ("d_hitomi.py", "DownloaderHitomi"),
    "mangafox": ("d_mangafox.py", "DownloaderMangafox"),
    "manhuagui": ("d_manhuagui.py", "DownloaderManhuagui"),
    "picacomic": ("d_picacomic.py", "DownloaderPicacomic"),
    "toonkor": ("d_toonkor.py", "DownloaderToonkor"),
    "wfwf": ("d_wfwf.py", "DownloaderWfwf"),
}

_mod_cache: Dict[str, Any] = {}
_dl_cache: Dict[str, Any] = {}
# Caché para downloaders que no tienen paginación nativa (wfwf, dumanwu search)
_catalog_cache: Dict[str, List[Dict]] = {}
# Última carpeta de destino — persiste entre series durante la sesión
_last_dest_dir: str = os.path.join(os.path.expanduser("~"), "Downloads")


def _load_mod(site_type: str) -> Any:
    if site_type in _mod_cache:
        return _mod_cache[site_type]
    filename, _ = _DOWNLOADER_MAP[site_type]
    filepath = os.path.join(_DL_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Archivo no encontrado: {filepath}")
    spec = importlib.util.spec_from_file_location(f"_bdl_{site_type}", filepath)
    if not spec or not spec.loader:
        raise ImportError(f"No se pudo crear spec para: {filepath}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"_bdl_{site_type}"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _mod_cache[site_type] = mod
    return mod


def get_dl(site_type: str) -> Any:
    if site_type not in _dl_cache:
        mod = _load_mod(site_type)
        _, class_name = _DOWNLOADER_MAP[site_type]
        cls = getattr(mod, class_name)
        logging.info(f"[Babylon] Inicializando {class_name}…")
        _dl_cache[site_type] = cls()
        logging.info(f"[Babylon] {class_name} lista.")
    return _dl_cache[site_type]


# ══════════════════════════════════════════════════════════════════════════════
#  BÚSQUEDA / CATÁLOGO — paginado real, 1 request por página para catálogos
# ══════════════════════════════════════════════════════════════════════════════


def _raw_to_display(site_type: str, raw: Dict) -> Optional[Dict]:
    """
    Convierte item raw a formato display, preservando "_raw" íntegro
    para pasarlo sin modificar a dl.get_series().
    """
    title = str(raw.get("title") or raw.get("name") or "").strip()
    if not title:
        return None
    if site_type == "wfwf":
        t_id = raw.get("toon_id", raw.get("id", ""))
        enc = raw.get("encoded_title", "")
        mode = raw.get("mode", "webtoon")
        slug = f"{t_id}|||{enc}|||{mode}"
    elif site_type == "hitomi":
        slug = str(raw.get("id", ""))
    else:
        slug = raw.get("slug") or str(raw.get("id", ""))
    if not slug:
        return None
    return {"title": title, "slug": slug, "_raw": raw}


def get_series_url(site_type: str, item: Dict) -> str:
    """Retorna la URL completa de la serie en la web original."""
    try:
        mod = _load_mod(site_type)
        base = getattr(mod, "BASE_URL", "").rstrip("/")
        slug = item.get("slug", "")

        if site_type == "wfwf":
            # slug = id|||encoded|||mode
            parts = slug.split("|||")
            if len(parts) == 3:
                t_id, enc, mode = parts
                return f"{base}/{mode}/{t_id}/{enc}"
        elif site_type == "hitomi":
            return f"https://hitomi.la/manga/{slug}.html"
        elif site_type == "baozimh":
            # baozimh suele tener mirrors, usamos el del downloader si existe
            dl = get_dl("baozimh")
            b = (dl._mirror or base).rstrip("/")
            return f"{b}/comic/{slug}"
        elif site_type == "18mh":
            return f"{base}/manga/{slug}.html"
        elif site_type == "bakamh":
            return f"{base}/manga/{slug}/"
        elif site_type == "dumanwu":
            return f"{base}/{slug}/"
        elif site_type == "manhuagui":
            return f"{base}/comic/{slug}/"
        elif site_type == "picacomic":
            return f"https://wikimanga.org/comic/{slug}"  # Picacomic es app-only, link fallback
        elif site_type == "mangafox":
            return f"{base}/manga/{slug}/"
        elif site_type == "toonkor":
            return f"{base}/{slug}"

        return f"{base}/{slug}"
    except Exception:
        return ""


def search_site(
    site: Dict[str, str],
    query: str,
    filters: Optional[Dict[str, str]] = None,
    page: int = 1,
) -> Tuple[List[Dict], bool]:
    """
    Retorna (items_para_esta_pagina, hay_mas_paginas).

    Para CATÁLOGO (query=""): hace 1 request a la red por página → rápido.
    Para BÚSQUEDA (query!=""): fetch-all una vez (cacheado), slice por página.

    Lógica rápida por downloader:
      18mh      → _get_catalog_page(sess, section, page)        — 1 request
      bakamh    → dl.get_catalog_page(page, genre_slug, sort)   — 1 request
      baozimh   → _fetch_api_page(sess_com, mirror, ..., page)  — 1 request (~36 items)
      dumanwu   → GET /sort/N para page=1, _sortmore() para page>1 — 1 request
      hitomi    → dl.get_catalog_page(page, language)           — 1 request
      mangafox  → dl.get_catalog_page(page)                     — 1 request
      manhuagui → dl.get_catalog_page(page, region, genre, ...) — 1 request
      picacomic → dl.get_catalog_page(page) o get_comics_by_category — 1 request
      toonkor   → dl.get_catalog_page(page)                     — 1 request
      wfwf      → caché en memoria, slice                       — instantáneo tras primera carga
    """
    if filters is None:
        filters = {}
    t = site["type"]

    try:
        dl = get_dl(t)
        mod = _load_mod(t)
    except Exception as e:
        logging.error(f"[Babylon] No se pudo cargar {t}: {e}")
        return [], False

    raw_items: List[Dict] = []
    has_more = False
    total_hint = (
        ""  # Texto de total cuando el downloader lo proporciona instantáneamente
    )

    try:
        # ─────────────────────────────────────────────────────────────────────
        # 18MH
        # Búsqueda: dl.search(query) → todo de una, paginamos display
        # Catálogo: dl.get_catalog_page(page, page_size) — el downloader ya tiene
        #   buffer interno con deduplicación que itera secciones secuencialmente.
        #   mod._get_catalog_page(sess, path, page>1) NO funciona: 18mh no soporta
        #   paginación por URL (?page=N, /N, /page/N) y devuelve siempre página 1.
        #   Por eso usamos el método de clase que ya resuelve esto internamente.
        #
        #   El filtro "section" se usa para buscar solo en esa sección en page=1
        #   (fetch rápido directo), pero para page>1 se delega a dl.get_catalog_page
        #   que recorre todas las secciones y acumula en su buffer interno.
        # ─────────────────────────────────────────────────────────────────────
        if t == "18mh":
            if query:
                cache_key = f"18mh_search_{query}"
                if cache_key not in _catalog_cache:
                    _catalog_cache[cache_key] = dl.search(query)
                all_r = _catalog_cache[cache_key]
                start = (page - 1) * PAGE_SIZE
                raw_items = all_r[start : start + PAGE_SIZE]
                has_more = start + PAGE_SIZE < len(all_r)
            else:
                # dl.get_catalog_page tiene su propio buffer interno (_cat_buf)
                # que acumula resultados de todas las secciones y los deduplica.
                # Es el único método que pagina correctamente en 18mh.
                items, has_more = dl.get_catalog_page(page=page, page_size=PAGE_SIZE)
                raw_items = list(items)

        # ─────────────────────────────────────────────────────────────────────
        # BAKAMH
        # Catálogo: dl.get_catalog_page(page, page_size=20, genre_slug, sort) → (items, has_more)
        # Búsqueda: dl.search(query) → todo
        # ─────────────────────────────────────────────────────────────────────
        elif t == "bakamh":
            if query:
                cache_key = f"bakamh_search_{query}"
                if cache_key not in _catalog_cache:
                    _catalog_cache[cache_key] = dl.search(query)
                all_r = _catalog_cache[cache_key]
                start = (page - 1) * PAGE_SIZE
                raw_items = all_r[start : start + PAGE_SIZE]
                has_more = start + PAGE_SIZE < len(all_r)
            else:
                items, has_more = dl.get_catalog_page(
                    page=page,
                    genre_slug=filters.get("genre", ""),
                    sort=filters.get("sort", "latest"),
                )
                raw_items = list(items)

        # ─────────────────────────────────────────────────────────────────────
        # BAOZIMH — CLAVE: usar _fetch_api_page en vez de get_catalog()
        # get_catalog() descarga cientos de páginas en paralelo → tarda MINUTOS
        # _fetch_api_page(sess_com, mirror, type_, region, state, page) → ~1s
        # ─────────────────────────────────────────────────────────────────────
        elif t == "baozimh":
            if query:
                cache_key = f"baozimh_search_{query}"
                if cache_key not in _catalog_cache:
                    _catalog_cache[cache_key] = dl.search(query)
                all_r = _catalog_cache[cache_key]
                start = (page - 1) * PAGE_SIZE
                raw_items = all_r[start : start + PAGE_SIZE]
                has_more = start + PAGE_SIZE < len(all_r)
            else:
                mirror = dl._mirror or mod.COM_MIRRORS[0]
                raw_items = mod._fetch_api_page(
                    dl._sess_com,
                    mirror,
                    filters.get("type_", "all"),
                    filters.get("region", "all"),
                    filters.get("state", "all"),
                    page,
                )
                has_more = len(raw_items) >= 36  # API devuelve 36 por página completa

        # ─────────────────────────────────────────────────────────────────────
        # DUMANWU — CLAVE: _load_sort() hace hasta 500 requests → tarda minutos
        # Fix: page=1 → GET /sort/N (1 request), page>1 → _sortmore() (1 request)
        # ─────────────────────────────────────────────────────────────────────
        elif t == "dumanwu":
            if query:
                cache_key = f"dumanwu_search_{query}"
                if cache_key not in _catalog_cache:
                    _catalog_cache[cache_key] = dl.search(query)
                all_r = _catalog_cache[cache_key]
                start = (page - 1) * PAGE_SIZE
                raw_items = all_r[start : start + PAGE_SIZE]
                has_more = start + PAGE_SIZE < len(all_r)
            else:
                sort_id = (
                    int(filters.get("sort_id", "1"))
                    if filters.get("sort_id", "1").isdigit()
                    else 1
                )
                if page == 1:
                    try:
                        r = dl._sess.get(
                            f"{mod.BASE_URL}/sort/{sort_id}",
                            timeout=15,
                            headers=mod.HEADERS,
                        )
                        raw_items = (
                            mod._parse_series_html(r.text)
                            if r.status_code == 200
                            else []
                        )
                    except Exception:
                        raw_items = []
                else:
                    raw_items = mod._sortmore(dl._sess, sort_id, page)
                has_more = len(raw_items) > 0

        # ─────────────────────────────────────────────────────────────────────
        # HITOMI
        # Búsqueda: dl.search(query) — ID numérico o tags (female:X language:Y …)
        #
        # Catálogo: construimos la URL nozomi según el filtro "order".
        # Cada opción del menú "Orden" corresponde a un endpoint real del CDN:
        #   default       → /index-{language}.nozomi          (Date Added, desc)
        #   date_published→ /date-published-index-{language}.nozomi
        #   pop_today     → /popular/today-index-{language}.nozomi
        #   pop_week      → /popular/week-index-{language}.nozomi
        #   pop_month     → /popular/month-index-{language}.nozomi
        #   pop_year      → /popular/year-index-{language}.nozomi
        #   random        → /index-{language}.nozomi + shuffle
        #
        # type_val aplica _apply_sort(sess, ids, ["type:X"])
        #   → _term_url("type:X") → /n/type/X-all.nozomi
        # ─────────────────────────────────────────────────────────────────────
        elif t == "hitomi":
            import random as _random

            CDN = "https://ltn.gold-usergeneratedcontent.net"
            language = filters.get("language", "all")
            type_val = filters.get("type", "")
            order = filters.get("order", "default")

            # Mapa de orden → URL nozomi del CDN de hitomi
            _ORDER_URL = {
                "default": f"{CDN}/index-{language}.nozomi",
                "date_published": f"{CDN}/date-published-index-{language}.nozomi",
                "pop_today": f"{CDN}/popular/today-index-{language}.nozomi",
                "pop_week": f"{CDN}/popular/week-index-{language}.nozomi",
                "pop_month": f"{CDN}/popular/month-index-{language}.nozomi",
                "pop_year": f"{CDN}/popular/year-index-{language}.nozomi",
                "random": f"{CDN}/index-{language}.nozomi",
            }

            if query:
                # dl.search() maneja ID numérico o tags via search_ids()
                cache_key = f"hitomi_search_{query}"
                if cache_key not in _catalog_cache:
                    _catalog_cache[cache_key] = dl.search(query)
                all_r = _catalog_cache[cache_key]
                start = (page - 1) * PAGE_SIZE
                raw_items = all_r[start : start + PAGE_SIZE]
                has_more = start + PAGE_SIZE < len(all_r)

            elif order == "default" and not type_val:
                # Caso más simple y rápido: delegar al downloader
                # dl.get_catalog_page usa su caché interno (_cat_ids/_cat_buf_key)
                items, has_more = dl.get_catalog_page(page=page, language=language)
                raw_items = list(items)

            else:
                # Todos los demás órdenes: necesitamos la lista completa de IDs
                cache_key = f"hitomi_cat_{language}_{type_val}_{order}"

                if cache_key not in _catalog_cache:
                    nozomi_url = _ORDER_URL.get(order, _ORDER_URL["default"])

                    # Obtener IDs desde el endpoint nozomi correspondiente
                    ids = mod._nozomi_ids(dl._sess, nozomi_url)

                    # Si el endpoint no devolvió nada (URL incorrecta), fallback al base
                    if not ids:
                        logging.warning(
                            f"[Hitomi] {nozomi_url} vacío, usando índice base"
                        )
                        ids = mod._nozomi_ids(
                            dl._sess, f"{CDN}/index-{language}.nozomi"
                        )

                    # Filtrar por tipo si está seleccionado
                    # _apply_sort llama _term_url("type:X") → /n/type/X-all.nozomi
                    if type_val and ids:
                        ids = mod._apply_sort(dl._sess, ids, [f"type:{type_val}"])

                    # Aleatorio: shuffle sobre los IDs ya filtrados/ordenados
                    if order == "random":
                        ids = list(ids)
                        _random.shuffle(ids)

                    # Pre-load metadata primeros 200 para títulos rápidos
                    mod.load_meta_batch(dl._sess, ids[:200])

                    _catalog_cache[cache_key] = [
                        {"id": str(gid), "title": mod.gallery_title(gid)} for gid in ids
                    ]

                all_r = _catalog_cache[cache_key]
                start = (page - 1) * PAGE_SIZE
                raw_items_page = all_r[start : start + PAGE_SIZE]

                # Pre-load metadata de esta página si no estaba en los primeros 200
                gids_page = [int(r["id"]) for r in raw_items_page if r["id"].isdigit()]
                if gids_page:
                    mod.load_meta_batch(dl._sess, gids_page)

                raw_items = [
                    {
                        "id": r["id"],
                        "title": mod.gallery_title(int(r["id"])) or r["title"],
                    }
                    for r in raw_items_page
                ]
                has_more = start + PAGE_SIZE < len(all_r)

        # ─────────────────────────────────────────────────────────────────────
        # MANGAFOX — dl.get_catalog_page(page, page_size) → (items, has_more:bool)
        # ─────────────────────────────────────────────────────────────────────
        elif t == "mangafox":
            if query:
                cache_key = f"mangafox_search_{query}"
                if cache_key not in _catalog_cache:
                    _catalog_cache[cache_key] = dl.search(query)
                all_r = _catalog_cache[cache_key]
                start = (page - 1) * PAGE_SIZE
                raw_items = all_r[start : start + PAGE_SIZE]
                has_more = start + PAGE_SIZE < len(all_r)
                total_hint = f"{len(all_r)} resultados"
            else:
                items, has_more = dl.get_catalog_page(page=page)
                raw_items = list(items)

        # ─────────────────────────────────────────────────────────────────────
        # MANHUAGUI — dl.get_catalog_page → (items, total_pages:int)
        # total_pages viene del HTML (paginador) — lo conocemos instantáneamente
        # ─────────────────────────────────────────────────────────────────────
        elif t == "manhuagui":
            if query:
                cache_key = f"manhuagui_search_{query}"
                if cache_key not in _catalog_cache:
                    _catalog_cache[cache_key] = dl.search(query)
                all_r = _catalog_cache[cache_key]
                start = (page - 1) * PAGE_SIZE
                raw_items = all_r[start : start + PAGE_SIZE]
                has_more = start + PAGE_SIZE < len(all_r)
                total_hint = f"{len(all_r)} resultados"
            else:
                items, total_pages = dl.get_catalog_page(
                    page=page,
                    region=filters.get("region", ""),
                    genre=filters.get("genre", ""),
                    audience=filters.get("audience", ""),
                    status=filters.get("status", ""),
                )
                raw_items = list(items)
                tp = int(total_pages) if total_pages else 0
                has_more = tp > page
                if tp > 0:
                    total_hint = f"~{tp * len(raw_items)} series  ({tp} páginas)"

        # ─────────────────────────────────────────────────────────────────────
        # PICACOMIC — search/get_catalog_page → (items, total_pages:int)
        # ─────────────────────────────────────────────────────────────────────
        elif t == "picacomic":
            if not getattr(dl, "_token", ""):
                return (
                    [
                        {
                            "title": "⚠ PicaComic sin token — configura las credenciales",
                            "slug": "__no_token__",
                            "_raw": {},
                        }
                    ],
                    False,
                    "",
                )
            sort = filters.get("sort", "dd")
            category = filters.get("category", "")
            if query:
                raw_items, total_pages = dl.search(query, page=page, sort=sort)
                tp = int(total_pages) if total_pages else 0
                has_more = tp > page
                if tp > 0:
                    total_hint = f"{tp} páginas de resultados"
            elif category:
                raw_items, total_pages = dl.get_comics_by_category(
                    category, page=page, sort=sort
                )
                tp = int(total_pages) if total_pages else 0
                has_more = tp > page
                if tp > 0:
                    total_hint = f"~{tp * 20} comics  ({tp} páginas)"
            else:
                raw_items, total_pages = dl.get_catalog_page(page=page, sort=sort)
                tp = int(total_pages) if total_pages else 0
                has_more = tp > page
                if tp > 0:
                    total_hint = f"~{tp * 20} comics  ({tp} páginas)"

        # ─────────────────────────────────────────────────────────────────────
        # TOONKOR — dl.get_catalog_page(page, page_size) → (items, has_more)
        # ─────────────────────────────────────────────────────────────────────
        elif t == "toonkor":
            if query:
                cache_key = f"toonkor_search_{query}"
                if cache_key not in _catalog_cache:
                    _catalog_cache[cache_key] = dl.search(query)
                all_r = _catalog_cache[cache_key]
                start = (page - 1) * PAGE_SIZE
                raw_items = all_r[start : start + PAGE_SIZE]
                has_more = start + PAGE_SIZE < len(all_r)
                total_hint = f"{len(all_r)} resultados"
            else:
                items, has_more = dl.get_catalog_page(page=page)
                raw_items = list(items)

        # ─────────────────────────────────────────────────────────────────────
        # WFWF — caché en memoria. Total conocido tras primera carga.
        # ─────────────────────────────────────────────────────────────────────
        elif t == "wfwf":
            Mode = mod.Mode
            mode_val = filters.get("mode", "both")
            cache_key = f"wfwf_catalog_{mode_val}"

            if query:
                search_key = f"wfwf_search_{query}"
                if search_key not in _catalog_cache:
                    _catalog_cache[search_key] = dl.search(query)
                all_r = _catalog_cache[search_key]
            else:
                if cache_key not in _catalog_cache:
                    # get_catalog() hace requests paralelas; más workers = más rápido
                    if mode_val == "both":
                        _catalog_cache[cache_key] = dl.get_catalog()
                    else:
                        _catalog_cache[cache_key] = mod.fetch_series_list(
                            dl._sess, Mode(mode_val), workers=8
                        )
                all_r = _catalog_cache[cache_key]

            start = (page - 1) * PAGE_SIZE
            raw_items = all_r[start : start + PAGE_SIZE]
            has_more = start + PAGE_SIZE < len(all_r)
            total_hint = f"{len(all_r)} series en total"

    except Exception as exc:
        import traceback as _tb

        logging.error(
            f"[Babylon] Error en {t} página {page}: {exc}\n{_tb.format_exc()}"
        )

    # Normalizar preservando _raw
    results: List[Dict] = []
    for raw in raw_items:
        entry = _raw_to_display(t, raw)
        if entry:
            results.append(entry)

    # total_hint para búsquedas cacheadas en downloaders sin total nativo
    if not total_hint and query and raw_items:
        cache_key_search = f"{t}_search_{query}"
        if cache_key_search in _catalog_cache:
            n = len(_catalog_cache[cache_key_search])
            total_hint = f"{n} resultados"

    return results, has_more, total_hint


# ══════════════════════════════════════════════════════════════════════════════
#  SEÑALES
# ══════════════════════════════════════════════════════════════════════════════


class _SearchSignals(QObject):
    finished = Signal(list, bool, str)  # (items, has_more, total_hint)
    error = Signal(str)


class _SeriesSignals(QObject):
    finished = Signal(dict, list)  # (series_meta, chapters)
    error = Signal(str)


class _DownloadSignals(QObject):
    chapter_start = Signal(int, int, str)  # (ch_idx, total, title)
    image_progress = Signal(int, int, int)  # (ch_idx, done, total_imgs)
    chapter_done = Signal(int, int, str)  # (chapters_done, total, zip_path)
    chapter_error = Signal(int, str)  # (ch_idx, error_msg)
    all_done = Signal(int, int)  # (success, total)
    cancelled = Signal()


class _DynOptsSignals(QObject):
    finished = Signal(str, list)  # (filter_id, [(display, value)])


# ══════════════════════════════════════════════════════════════════════════════
#  WORKERS
# ══════════════════════════════════════════════════════════════════════════════


class BabylonSearchWorker(QRunnable):
    def __init__(self, site: Dict, query: str, filters: Dict, page: int = 1) -> None:
        super().__init__()
        self.site = site
        self.query = query
        self.filters = filters
        self.page = page
        self.signals = _SearchSignals()

    def run(self) -> None:
        try:
            items, has_more, total_hint = search_site(
                self.site, self.query, self.filters, self.page
            )
            self.signals.finished.emit(items, has_more, total_hint)
        except Exception as e:
            self.signals.error.emit(str(e))


class BabylonSeriesWorker(QRunnable):
    """
    Carga la ficha + capítulos de una serie.
    Usa item["_raw"] directamente en dl.get_series() — sin modificar nada.
    """

    def __init__(self, site_type: str, item: Dict) -> None:
        super().__init__()
        self.site_type = site_type
        self.item = item
        self.signals = _SeriesSignals()

    def run(self) -> None:
        try:
            dl = get_dl(self.site_type)
            raw_item = self.item.get("_raw") or self.item

            if self.site_type == "wfwf":
                keys = (
                    list(raw_item.keys())
                    if isinstance(raw_item, dict)
                    else type(raw_item).__name__
                )
                logging.info(f"[Babylon/wfwf] get_series raw keys: {keys}")
                # Mode.__init__ en d_wfwf.py acepta str y Mode object.
                # NO convertir aquí — get_series lo hace internamente.

            series, chapters = dl.get_series(raw_item)

            if self.site_type == "wfwf":
                logging.info(
                    f"[Babylon/wfwf] get_series OK — {len(chapters or [])} capítulos"
                )

            self.signals.finished.emit(series or {}, chapters or [])
        except Exception as e:
            logging.error(
                f"[Babylon] SeriesWorker ({self.site_type}): {e}", exc_info=True
            )
            self.signals.error.emit(str(e))


class BabylonDownloadWorker(QRunnable):
    """
    Descarga capítulos imagen a imagen.
    series y chapters son los dicts originales de dl.get_series() — sin modificar.
    """

    def __init__(
        self,
        site_type: str,
        series: Dict,
        chapters: List[Dict],
        output_dir: str,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.site_type = site_type
        self.series = series
        self.chapters = chapters
        self.output_dir = output_dir
        self.cancel_event = cancel_event
        self.signals = _DownloadSignals()

    def run(self) -> None:
        try:
            dl = get_dl(self.site_type)
        except Exception as e:
            logging.error(f"[Babylon] DownloadWorker init: {e}")
            self.signals.all_done.emit(0, len(self.chapters))
            return

        os.makedirs(self.output_dir, exist_ok=True)
        success = 0
        total = len(self.chapters)

        for i, chapter in enumerate(self.chapters):
            if self.cancel_event.is_set():
                self.signals.cancelled.emit()
                return

            title = chapter.get("title", f"Capítulo {i + 1}")
            tmp_dir = os.path.join(self.output_dir, f"_tmp_{i:04d}")
            self.signals.chapter_start.emit(i, total, title)

            try:
                images = dl.get_chapter_images(chapter, self.series)
                if not images:
                    self.signals.chapter_error.emit(i, "Sin imágenes")
                    continue

                os.makedirs(tmp_dir, exist_ok=True)
                referer = dl.get_referer(chapter, self.series)

                for j, url in enumerate(images):
                    if self.cancel_event.is_set():
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                        self.signals.cancelled.emit()
                        return
                    try:
                        raw_bytes = dl.dl_image(url, referer)
                        if raw_bytes:
                            ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
                            if ext not in ("jpg", "jpeg", "png", "webp", "gif", "avif"):
                                ext = "jpg"
                            _save_image(
                                raw_bytes, os.path.join(tmp_dir, f"{j + 1:04d}.{ext}")
                            )
                    except Exception as img_err:
                        logging.warning(f"[Babylon] img {j + 1}: {img_err}")
                    self.signals.image_progress.emit(i, j + 1, len(images))

                safe = re.sub(r'[\\/:*?"<>|]', "", title).strip()[:60] or f"cap_{i + 1}"
                zpath = os.path.join(self.output_dir, f"{i + 1:04d} - {safe}.zip")
                with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                    for fname in sorted(os.listdir(tmp_dir)):
                        zf.write(os.path.join(tmp_dir, fname), fname)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                success += 1
                self.signals.chapter_done.emit(success, total, zpath)

            except Exception as ch_err:
                logging.error(f"[Babylon] Cap {i + 1}: {ch_err}", exc_info=True)
                self.signals.chapter_error.emit(i, str(ch_err)[:80])
                shutil.rmtree(tmp_dir, ignore_errors=True)

        self.signals.all_done.emit(success, total)


class BabylonDynamicOptsWorker(QRunnable):
    def __init__(self, site: Dict) -> None:
        super().__init__()
        self.site = site
        self.signals = _DynOptsSignals()

    def run(self) -> None:
        t = self.site["type"]
        try:
            dl = get_dl(t)
            mod = _load_mod(t)

            if t == "bakamh":
                genres = dl.get_genres()
                options = [("Todos", "")] + [(g["name"], g["slug"]) for g in genres]
                self.signals.finished.emit("genre", options)

            elif t == "picacomic":
                if not getattr(dl, "_token", ""):
                    return
                # get_categories está en el módulo, no en la clase
                cats_fn = getattr(mod, "get_categories", None)
                if cats_fn is None:
                    return
                cats = cats_fn(dl._sess, dl._token)
                if not cats:
                    return
                options = [("Todas", "")] + [
                    (c.get("title", ""), c.get("title", ""))
                    for c in cats
                    if not c.get("isWeb", False) and c.get("title")
                ]
                self.signals.finished.emit("category", options)

        except Exception as e:
            logging.warning(f"[Babylon] DynOpts {t}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  UTILS
# ══════════════════════════════════════════════════════════════════════════════


def _save_image(raw: bytes, path: str) -> None:
    try:
        from PIL import Image

        img = Image.open(BytesIO(raw))
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = bg
        img.save(path, quality=92)
    except Exception:
        with open(path, "wb") as f:
            f.write(raw)


# ══════════════════════════════════════════════════════════════════════════════
#  ESTILOS
# ══════════════════════════════════════════════════════════════════════════════

_PANEL_BG = "background-color:rgba(15,17,23,235);border:1px solid rgba(157,70,255,0.3);border-radius:12px;"
_BTN_BASE = (
    "QPushButton{background:rgba(60,60,60,150);color:white;"
    "border:1px solid rgba(150,0,150,100);border-radius:5px;padding:6px 8px;}"
    "QPushButton:hover{background:rgba(150,0,150,50);border:1px solid #960096;}"
    "QPushButton:disabled{color:rgba(255,255,255,0.25);border:1px solid rgba(150,0,150,0.15);}"
)
_BTN_PRIMARY = (
    "QPushButton{background:rgba(157,70,255,0.3);color:white;"
    "border:1px solid #9d46ff;border-radius:6px;padding:10px 20px;"
    "font-weight:bold;font-size:13px;}"
    "QPushButton:hover{background:rgba(157,70,255,0.55);}"
    "QPushButton:disabled{background:rgba(50,50,50,0.3);color:rgba(255,255,255,0.25);"
    "border:1px solid rgba(150,0,150,0.2);}"
)
_BTN_DANGER = (
    "QPushButton{background:rgba(150,0,0,0.3);color:#ffaaaa;"
    "border:1px solid rgba(200,0,0,0.5);border-radius:5px;padding:8px 14px;}"
    "QPushButton:hover{background:rgba(200,0,0,0.45);}"
    "QPushButton:disabled{color:rgba(255,100,100,0.2);}"
)
_BTN_NAV = (
    "QPushButton{background:rgba(40,40,60,180);color:#bd7aff;"
    "border:1px solid rgba(157,70,255,0.4);border-radius:5px;padding:6px 16px;"
    "font-weight:bold;}"
    "QPushButton:hover{background:rgba(157,70,255,0.25);border:1px solid #9d46ff;}"
    "QPushButton:disabled{color:rgba(157,70,255,0.2);border:1px solid rgba(157,70,255,0.1);}"
)
_PROG_STYLE = (
    "QProgressBar{background:rgba(0,0,0,0.5);border:1px solid rgba(157,70,255,0.3);"
    "border-radius:4px;text-align:center;color:white;font-size:11px;}"
    "QProgressBar::chunk{background:rgba(157,70,255,0.65);border-radius:3px;}"
)
_IMG_BAR = (
    "QProgressBar{background:rgba(0,0,0,0.3);border:none;border-radius:4px;}"
    "QProgressBar::chunk{background:rgba(0,200,130,0.75);border-radius:4px;}"
)
_LIST_STYLE = (
    "QListWidget{background:rgba(5,5,8,0.85);border:1px solid rgba(157,70,255,0.3);"
    "border-radius:6px;color:#e0e0e0;font-size:12px;outline:none;}"
    "QListWidget::item{padding:4px 8px;}"
    "QListWidget::item:selected{background:rgba(157,70,255,0.35);color:white;}"
    "QListWidget::item:hover{background:rgba(157,70,255,0.12);}"
)


class _ArrowLineEdit(QLineEdit):
    """
    QLineEdit que garantiza que las flechas ← → siempre muevan el cursor
    dentro del campo de texto, sin que widgets hermanos (como listas)
    intercepten esos eventos a nivel de ventana.
    """

    def keyPressEvent(self, event) -> None:
        # Siempre manejar flechas localmente — nunca propagar al padre
        from PySide6.QtCore import Qt as _Qt

        if event.key() in (
            _Qt.Key.Key_Left,
            _Qt.Key.Key_Right,
            _Qt.Key.Key_Home,
            _Qt.Key.Key_End,
            _Qt.Key.Key_Backspace,
            _Qt.Key.Key_Delete,
        ):
            super().keyPressEvent(event)
            event.accept()
        else:
            super().keyPressEvent(event)


class _DragSelectList(QListWidget):
    """
    QListWidget con selección por arrastre tipo "pintura":
    - Click normal: selecciona/deselecciona un ítem
    - Click + arrastrar: selecciona todos los ítems bajo el cursor
    Usa ExtendedSelection (no MultiSelection) para no interferir
    con el foco del teclado de otros widgets.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_active = False
        self._drag_toggle_to: Optional[bool] = None
        self.setMouseTracking(True)
        # ExtendedSelection en lugar de MultiSelection — no roba el foco del teclado
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Foco solo al hacer click explícito, no al navegar con Tab
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item:
                self._drag_active = True
                self._drag_toggle_to = not item.isSelected()
                self.blockSignals(True)
                item.setSelected(self._drag_toggle_to)
                self.blockSignals(False)
                self.itemSelectionChanged.emit()
            else:
                self.clearSelection()
            # No llamar super() para no activar el comportamiento nativo
            # que movería el foco del teclado fuera del campo de búsqueda
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_active and self._drag_toggle_to is not None:
            item = self.itemAt(event.position().toPoint())
            if item and item.isSelected() != self._drag_toggle_to:
                self.blockSignals(True)
                item.setSelected(self._drag_toggle_to)
                self.blockSignals(False)
                self.itemSelectionChanged.emit()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_active = False
        self._drag_toggle_to = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        # Pasar flechas a QListWidget solo cuando este widget TIENE el foco explícito
        # Esto evita que robe eventos de teclado de campos de texto hermanos
        super().keyPressEvent(event)


def _lbl(color: str = "#aaa") -> str:
    return f"color:{color};font-size:12px;background:transparent;border:none;"


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL DE DESCARGA
# ══════════════════════════════════════════════════════════════════════════════


class BabylonDownloadPanel(QWidget):
    back_requested = Signal()

    def __init__(
        self,
        site_type: str,
        series: Dict,
        chapters: List[Dict],
        output_dir: str,
        parent: Optional[QWidget] = None,
        body_font: Optional[QFont] = None,
        title_font: Optional[QFont] = None,
    ) -> None:
        super().__init__(parent)
        self.site_type = site_type
        self.series = series
        self.chapters = chapters
        self.output_dir = output_dir
        self.body_font = body_font
        self.title_font = title_font
        self._cancel = threading.Event()
        self._pool = QThreadPool.globalInstance()
        self._ch_rows: List[Tuple[QLabel, QProgressBar]] = []
        self._build_ui()
        self._start()

    def _build_ui(self) -> None:
        self.setObjectName("BabylonDownloadPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#BabylonDownloadPanel{{{_PANEL_BG}}}")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        self._btn_back = QPushButton("VOLVER")
        self._btn_back.setEnabled(False)
        self._btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_back.setStyleSheet(_BTN_BASE)
        if self.body_font:
            self._btn_back.setFont(self.body_font)
        self._btn_back.clicked.connect(self.back_requested.emit)
        hdr.addWidget(self._btn_back)
        hdr.addStretch()
        lt = QLabel(f"{self.series.get('title', 'Descarga')[:55]}")
        lt.setStyleSheet(
            "color:#bd7aff;font-size:15px;font-weight:bold;background:transparent;border:none;"
        )
        if self.title_font:
            lt.setFont(self.title_font)
        lt.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hdr.addWidget(lt)
        root.addLayout(hdr)

        self._lbl_status = QLabel(f"Preparando {len(self.chapters)} capítulos…")
        self._lbl_status.setStyleSheet(_lbl("#ccc"))
        if self.body_font:
            self._lbl_status.setFont(self.body_font)
        root.addWidget(self._lbl_status)

        self._bar_global = QProgressBar()
        self._bar_global.setRange(0, len(self.chapters))
        self._bar_global.setValue(0)
        self._bar_global.setFixedHeight(20)
        self._bar_global.setStyleSheet(_PROG_STYLE)
        root.addWidget(self._bar_global)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background:transparent;border:none;")
        cw = QWidget()
        cw.setStyleSheet("background:transparent;")
        cl = QVBoxLayout(cw)
        cl.setSpacing(5)
        cl.setContentsMargins(0, 0, 4, 0)

        for ch in self.chapters:
            row = QFrame()
            row.setStyleSheet(
                "QFrame{background:rgba(25,28,38,160);border:1px solid rgba(157,70,255,0.18);border-radius:6px;}"
            )
            rl = QVBoxLayout(row)
            rl.setContentsMargins(10, 5, 10, 5)
            rl.setSpacing(4)
            lbl = QLabel(f"...  {ch.get('title', '?')}")
            lbl.setStyleSheet(_lbl())
            if self.body_font:
                lbl.setFont(self.body_font)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFixedHeight(7)
            bar.setTextVisible(False)
            bar.setStyleSheet(_IMG_BAR)
            rl.addWidget(lbl)
            rl.addWidget(bar)
            cl.addWidget(row)
            self._ch_rows.append((lbl, bar))

        cl.addStretch()
        scroll.setWidget(cw)
        root.addWidget(scroll, 1)

        ld = QLabel(f"Destino: {self.output_dir}")
        ld.setStyleSheet(
            "color:#444;font-size:10px;background:transparent;border:none;"
        )
        ld.setWordWrap(True)
        root.addWidget(ld)

        self._btn_cancel = QPushButton("Cancelar")
        self._btn_cancel.setStyleSheet(_BTN_DANGER)
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.body_font:
            self._btn_cancel.setFont(self.body_font)
        self._btn_cancel.clicked.connect(self._do_cancel)
        root.addWidget(self._btn_cancel)

    def _start(self) -> None:
        w = BabylonDownloadWorker(
            self.site_type, self.series, self.chapters, self.output_dir, self._cancel
        )
        w.signals.chapter_start.connect(self._on_start)
        w.signals.image_progress.connect(self._on_img)
        w.signals.chapter_done.connect(self._on_done)
        w.signals.chapter_error.connect(self._on_err)
        w.signals.all_done.connect(self._on_all_done)
        w.signals.cancelled.connect(self._on_cancelled)
        self._pool.start(w)

    def _on_start(self, idx: int, _t: int, title: str) -> None:
        if idx < len(self._ch_rows):
            lbl, _ = self._ch_rows[idx]
            lbl.setText(f">> {title}")
            lbl.setStyleSheet(
                "color:#00ccff;font-size:12px;background:transparent;border:none;"
            )

    def _on_img(self, ch_idx: int, done: int, total: int) -> None:
        if ch_idx < len(self._ch_rows):
            self._ch_rows[ch_idx][1].setValue(int(done * 100 / total) if total else 0)

    def _on_done(self, done: int, total: int, _p: str) -> None:
        idx = done - 1
        if idx < len(self._ch_rows):
            lbl, bar = self._ch_rows[idx]
            lbl.setText(f"OK  {self.chapters[idx].get('title', '?')}")
            lbl.setStyleSheet(
                "color:#00e87a;font-size:12px;background:transparent;border:none;"
            )
            bar.setValue(100)
        self._bar_global.setValue(done)
        self._lbl_status.setText(f"Completados {done} / {total}…")

    def _on_err(self, idx: int, error: str) -> None:
        if idx < len(self._ch_rows):
            lbl, _ = self._ch_rows[idx]
            lbl.setText(f"ERROR  {self.chapters[idx].get('title', '?')} — {error[:50]}")
            lbl.setStyleSheet(
                "color:#ff5555;font-size:12px;background:transparent;border:none;"
            )
        self._bar_global.setValue(self._bar_global.value() + 1)

    def _on_all_done(self, success: int, total: int) -> None:
        self._lbl_status.setText(f"Listo — — {success}/{total} capítulos")
        self._lbl_status.setStyleSheet(
            "color:#00e87a;font-size:13px;font-weight:bold;background:transparent;border:none;"
        )
        self._btn_cancel.setEnabled(False)
        self._btn_back.setEnabled(True)
        QMessageBox.information(
            self,
            "Descarga completa",
            f"Se descargaron {success} de {total} capítulos en:\n\n{self.output_dir}",
        )

    def _on_cancelled(self) -> None:
        self._lbl_status.setText("⚠  Descarga cancelada.")
        self._lbl_status.setStyleSheet(
            "color:#ffaa00;font-size:12px;background:transparent;border:none;"
        )
        self._btn_cancel.setEnabled(False)
        self._btn_back.setEnabled(True)

    def _do_cancel(self) -> None:
        self._cancel.set()
        self._btn_cancel.setEnabled(False)
        self._lbl_status.setText("Cancelando…")


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL DE SERIE  (ficha + selección de capítulos)
# ══════════════════════════════════════════════════════════════════════════════


class BabylonSeriesPanel(QWidget):
    back_requested = Signal()
    download_requested = Signal(dict, list, str)

    def __init__(
        self,
        site: Dict,
        item: Dict,
        parent: Optional[QWidget] = None,
        body_font: Optional[QFont] = None,
        title_font: Optional[QFont] = None,
    ) -> None:
        super().__init__(parent)
        self.site = site
        self.item = item
        self.body_font = body_font
        self.title_font = title_font
        self._series: Dict = {}
        self._chapters: List[Dict] = []
        self._dest_dir = _last_dest_dir
        self._pool = QThreadPool.globalInstance()
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        self.setObjectName("BabylonSeriesPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#BabylonSeriesPanel{{{_PANEL_BG}}}")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        btn_back = QPushButton("VOLVER")
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.setStyleSheet(_BTN_BASE)
        if self.body_font:
            btn_back.setFont(self.body_font)
        btn_back.clicked.connect(self.back_requested.emit)
        hdr.addWidget(btn_back)
        hdr.addStretch()
        self._lbl_title = QLabel(self.item.get("title", "Cargando…")[:60])
        self._lbl_title.setStyleSheet(
            "QLabel{color:#bd7aff;font-size:15px;font-weight:bold;background:transparent;border:none;}"
            "QLabel:hover{color:#bd7aff;text-decoration:underline;}"
        )
        self._lbl_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lbl_title.setToolTip("Click para copiar enlace a la web")
        
        def _copy_title_url(ev):
            url = get_series_url(self.site["type"], self.item)
            if url:
                QApplication.clipboard().setText(url)
                # Opcional: feedback visual temporal en el label de info
                old_info = self._lbl_info.text()
                self._lbl_info.setText("¡Enlace copiado al portapapeles!")
                from PySide6.QtCore import QTimer
                QTimer.singleShot(2000, lambda: self._lbl_info.setText(old_info))

        self._lbl_title.mousePressEvent = _copy_title_url  # type: ignore

        if self.title_font:
            self._lbl_title.setFont(self.title_font)
        self._lbl_title.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        hdr.addWidget(self._lbl_title)
        root.addLayout(hdr)

        self._lbl_info = QLabel("Cargando ficha…")
        self._lbl_info.setStyleSheet(
            "color:#777;font-size:11px;background:transparent;border:none;"
        )
        if self.body_font:
            self._lbl_info.setFont(self.body_font)
        root.addWidget(self._lbl_info)

        content = QHBoxLayout()
        content.setSpacing(14)

        self._ch_list = _DragSelectList()
        self._ch_list.setStyleSheet(_LIST_STYLE)
        if self.body_font:
            self._ch_list.setFont(self.body_font)
        self._ch_list.itemSelectionChanged.connect(self._update_btn)
        content.addWidget(self._ch_list, 1)

        rp = QVBoxLayout()
        rp.setSpacing(8)
        rp.setContentsMargins(0, 0, 0, 0)
        for lbl_txt, slot in [
            ("Seleccionar todo", self._ch_list.selectAll),
            ("Quitar selección", self._ch_list.clearSelection),
            ("Invertir selección", self._invert),
            ("Invertir orden", self._invert_order),
            ("ABRIR WEB", self._open_web),
        ]:
            b = QPushButton(lbl_txt)
            b.setStyleSheet(_BTN_BASE)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if self.body_font:
                b.setFont(self.body_font)
            b.clicked.connect(slot)
            rp.addWidget(b)

        rp.addStretch()
        self._lbl_count = QLabel("0 seleccionados")
        self._lbl_count.setStyleSheet(
            "color:#666;font-size:11px;background:transparent;border:none;"
        )
        rp.addWidget(self._lbl_count)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background:rgba(157,70,255,0.2);")
        rp.addWidget(sep)

        lbl_out = QLabel("Carpeta de destino:")
        lbl_out.setStyleSheet(
            "color:#aaa;font-size:11px;background:transparent;border:none;"
        )
        rp.addWidget(lbl_out)
        self._lbl_dest = QLabel(
            _last_dest_dir if len(_last_dest_dir) <= 42 else "…" + _last_dest_dir[-39:]
        )
        self._lbl_dest.setWordWrap(True)
        self._lbl_dest.setStyleSheet(
            "color:#555;font-size:10px;background:transparent;border:none;"
        )
        rp.addWidget(self._lbl_dest)

        btn_dest = QPushButton("Elegir carpeta")
        btn_dest.setStyleSheet(_BTN_BASE)
        btn_dest.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.body_font:
            btn_dest.setFont(self.body_font)
        btn_dest.clicked.connect(self._choose_dest)
        rp.addWidget(btn_dest)

        self._btn_dl = QPushButton("DESCARGAR")
        self._btn_dl.setEnabled(False)
        self._btn_dl.setStyleSheet(_BTN_PRIMARY)
        self._btn_dl.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.body_font:
            self._btn_dl.setFont(self.body_font)
        self._btn_dl.clicked.connect(self._request_dl)
        rp.addWidget(self._btn_dl)

        content.addLayout(rp)
        root.addLayout(content, 1)

    def _load(self) -> None:
        w = BabylonSeriesWorker(self.site["type"], self.item)
        w.signals.finished.connect(self._on_loaded)
        w.signals.error.connect(lambda e: self._lbl_info.setText(f"Error: {e[:80]}"))
        self._pool.start(w)

    def _on_loaded(self, series: Dict, chapters: List[Dict]) -> None:
        self._series = series
        self._chapters = chapters
        self._lbl_title.setText(series.get("title", self.item.get("title", "?"))[:60])
        extras = [
            str(series.get(k, ""))[:30]
            for k in ("author", "autor", "status", "estado")
            if series.get(k)
        ]
        info = f"{len(chapters)} capítulos"
        if extras:
            info += "  —  " + "  ·  ".join(extras)
        self._lbl_info.setText(info)
        self._ch_list.clear()
        for ch in chapters:
            it = QListWidgetItem(ch.get("title", "?"))
            it.setData(Qt.ItemDataRole.UserRole, ch)
            self._ch_list.addItem(it)

    def _update_btn(self) -> None:
        n = len(self._ch_list.selectedItems())
        self._lbl_count.setText(f"{n} seleccionados")
        self._btn_dl.setEnabled(n > 0 and bool(self._dest_dir))

    def _invert(self) -> None:
        self._ch_list.blockSignals(True)
        for i in range(self._ch_list.count()):
            it = self._ch_list.item(i)
            if it:
                it.setSelected(not it.isSelected())
        self._ch_list.blockSignals(False)
        self._update_btn()

    def _choose_dest(self) -> None:
        global _last_dest_dir
        folder = QFileDialog.getExistingDirectory(
            self, "Carpeta de descarga", _last_dest_dir
        )
        if folder:
            self._dest_dir = folder
            _last_dest_dir = folder  # recordar para la próxima serie
            self._lbl_dest.setText(folder if len(folder) <= 42 else "…" + folder[-39:])
            self._update_btn()

    def _request_dl(self) -> None:
        selected = self._ch_list.selectedItems()
        if not selected or not self._dest_dir:
            return
        chapters = [it.data(Qt.ItemDataRole.UserRole) for it in selected]
        safe = re.sub(r'[\\/:*?"<>|]', "", self._series.get("title", "serie")).strip()[
            :50
        ]
        self.download_requested.emit(
            self._series, chapters, os.path.join(self._dest_dir, safe)
        )

    def _invert_order(self) -> None:
        if not self._chapters:
            return
        self._chapters.reverse()
        self._ch_list.clear()
        for ch in self._chapters:
            it = QListWidgetItem(ch.get("title", "?"))
            it.setData(Qt.ItemDataRole.UserRole, ch)
            self._ch_list.addItem(it)
        self._update_btn()

    def _open_web(self) -> None:
        url = get_series_url(self.site["type"], self.item)
        if url:
            QDesktopServices.openUrl(QUrl(url))


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL DE DETALLE DE SITIO  — con paginación prev/next real
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL DE CONFIGURACIÓN DEL DOWNLOADER
# ══════════════════════════════════════════════════════════════════════════════

# Campos configurables por downloader (nombre_en_modulo, label, tipo, min, max, opciones)
# tipo: "combo" | "float" | "int" | "bool" | "str_ro"  (str_ro = solo lectura)
_SITE_CONFIG_FIELDS: Dict[str, List[Dict]] = {
    "18mh": [
        {"key": "SITE_URL", "label": "URL base", "type": "str_ro"},
        {
            "key": "REQUEST_DELAY",
            "label": "Delay entre requests (s)",
            "type": "float",
            "min": 0.0,
            "max": 5.0,
        },
        {"key": "TIMEOUT", "label": "Timeout (connect, read)", "type": "timeout"},
        {
            "key": "RETRY_DELAY",
            "label": "Delay entre reintentos (s)",
            "type": "float",
            "min": 0.0,
            "max": 10.0,
        },
    ],
    "bakamh": [
        {"key": "BASE_URL", "label": "URL base", "type": "str_ro"},
        {
            "key": "REQUEST_DELAY",
            "label": "Delay entre requests (s)",
            "type": "float",
            "min": 0.0,
            "max": 5.0,
        },
    ],
    "baozimh": [
        {"key": "SITE_ORG", "label": "URL base (org)", "type": "str_ro"},
        {
            "key": "REQUEST_DELAY",
            "label": "Delay entre requests (s)",
            "type": "float",
            "min": 0.0,
            "max": 5.0,
        },
        {"key": "TIMEOUT", "label": "Timeout (connect, read)", "type": "timeout"},
        {
            "key": "RETRY_DELAY",
            "label": "Delay entre reintentos (s)",
            "type": "float",
            "min": 0.0,
            "max": 10.0,
        },
    ],
    "dumanwu": [
        {"key": "BASE_URL", "label": "URL base", "type": "str_ro"},
        {"key": "TIMEOUT", "label": "Timeout (connect, read)", "type": "timeout"},
        {
            "key": "RETRY",
            "label": "Delay entre reintentos (s)",
            "type": "float",
            "min": 0.0,
            "max": 10.0,
        },
    ],
    "hitomi": [
        {"key": "HEADERS", "label": "User-Agent", "type": "useragent"},
    ],
    "mangafox": [
        {"key": "BASE_URL", "label": "URL base", "type": "str_ro"},
        {"key": "TIMEOUT", "label": "Timeout (connect, read)", "type": "timeout"},
        {
            "key": "RETRY",
            "label": "Delay entre reintentos (s)",
            "type": "float",
            "min": 0.0,
            "max": 10.0,
        },
    ],
    "manhuagui": [
        {"key": "BASE", "label": "URL base", "type": "str_ro"},
        {
            "key": "REQUEST_DELAY",
            "label": "Delay entre requests (s)",
            "type": "float",
            "min": 0.0,
            "max": 5.0,
        },
        {"key": "TIMEOUT", "label": "Timeout (connect, read)", "type": "timeout"},
        {
            "key": "MAX_RESULTS",
            "label": "Máx. resultados por búsqueda",
            "type": "int",
            "min": 5,
            "max": 100,
        },
    ],
    "picacomic": [
        {"key": "BASE_URL", "label": "URL base", "type": "str_ro"},
        {"key": "APP_VER", "label": "App Version", "type": "str_ro"},
    ],
    "toonkor": [
        {"key": "BASE_URL", "label": "URL base (auto-detectada)", "type": "str_ro"},
        {"key": "TIMEOUT", "label": "Timeout (connect, read)", "type": "timeout"},
        {
            "key": "RETRY",
            "label": "Delay entre reintentos (s)",
            "type": "float",
            "min": 0.0,
            "max": 10.0,
        },
    ],
    "wfwf": [
        {"key": "BASE_URL", "label": "URL base (auto-detectada)", "type": "str_ro"},
        {"key": "TIMEOUT", "label": "Timeout (connect, read)", "type": "timeout"},
        {
            "key": "RETRY",
            "label": "Delay entre reintentos (s)",
            "type": "float",
            "min": 0.0,
            "max": 10.0,
        },
    ],
}


class BabylonConfigPanel(QWidget):
    """
    Panel de configuración para un downloader específico.
    Sección global: common.CFG (formato salida, imagen, workers, etc.)
    Sección por sitio: variables del módulo del downloader (delay, timeout, retry, etc.)
    Modifica los valores en runtime directamente sobre los módulos ya cargados.
    """

    back_requested = Signal()

    def __init__(
        self,
        site: Dict,
        parent: Optional[QWidget] = None,
        body_font: Optional[QFont] = None,
        title_font: Optional[QFont] = None,
    ) -> None:
        super().__init__(parent)
        self.site = site
        self.body_font = body_font
        self.title_font = title_font
        self._widgets: Dict[str, Any] = {}  # clave → widget de edición
        self._build_ui()

    # ── Helpers de estilo ──────────────────────────────────────────────────────

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color:#bd7aff;font-size:13px;font-weight:bold;"
            "background:transparent;border:none;padding:4px 0px 2px 0px;"
        )
        if self.title_font:
            lbl.setFont(self.title_font)
        return lbl

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text + ":")
        lbl.setStyleSheet(
            "color:#ccc;font-size:11px;background:transparent;border:none;"
        )
        lbl.setFixedWidth(230)
        if self.body_font:
            lbl.setFont(self.body_font)
        return lbl

    def _ro_label(self, text: str) -> QLabel:
        lbl = QLabel(str(text))
        lbl.setStyleSheet(
            "color:#666;font-size:11px;background:rgba(0,0,0,0.3);"
            "border:1px solid rgba(100,100,100,0.3);border-radius:4px;padding:4px 8px;"
        )
        if self.body_font:
            lbl.setFont(self.body_font)
        return lbl

    def _float_spin(self, value: float, min_v: float, max_v: float) -> "QDoubleSpinBox":
        from PySide6.QtWidgets import QDoubleSpinBox

        sb = QDoubleSpinBox()
        sb.setRange(min_v, max_v)
        sb.setSingleStep(0.1)
        sb.setDecimals(2)
        sb.setValue(value)
        sb.setStyleSheet(
            "QDoubleSpinBox{background:rgba(5,5,8,0.85);color:#e0e0e0;"
            "border:1px solid rgba(157,70,255,0.3);border-radius:4px;padding:4px;}"
            "QDoubleSpinBox:focus{border:1px solid #9d46ff;}"
        )
        if self.body_font:
            sb.setFont(self.body_font)
        return sb

    def _int_spin(self, value: int, min_v: int, max_v: int) -> "QSpinBox":
        from PySide6.QtWidgets import QSpinBox

        sb = QSpinBox()
        sb.setRange(min_v, max_v)
        sb.setValue(value)
        sb.setStyleSheet(
            "QSpinBox{background:rgba(5,5,8,0.85);color:#e0e0e0;"
            "border:1px solid rgba(157,70,255,0.3);border-radius:4px;padding:4px;}"
            "QSpinBox:focus{border:1px solid #9d46ff;}"
        )
        if self.body_font:
            sb.setFont(self.body_font)
        return sb

    def _combo(self, options: List[tuple], current: str) -> QComboBox:
        cb = QComboBox()
        for label, val in options:
            cb.addItem(label, val)
        for i in range(cb.count()):
            if cb.itemData(i) == current:
                cb.setCurrentIndex(i)
                break
        if self.body_font:
            cb.setFont(self.body_font)
        return cb

    # ── Construcción de UI ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setObjectName("BabylonConfigPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#BabylonConfigPanel{{{_PANEL_BG}}}")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        btn_back = QPushButton("VOLVER")
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.setStyleSheet(_BTN_BASE)
        if self.body_font:
            btn_back.setFont(self.body_font)
        btn_back.clicked.connect(self.back_requested.emit)
        hdr.addWidget(btn_back)
        hdr.addStretch()
        lt = QLabel(f"Configuracion —  {self.site['name']}")
        lt.setStyleSheet(
            "color:#bd7aff;font-size:13px;font-weight:bold;background:transparent;border:none;"
        )
        if self.title_font:
            lt.setFont(self.title_font)
        lt.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hdr.addWidget(lt)
        root.addLayout(hdr)

        # Área scrollable con los campos
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background:transparent;border:none;")

        cw = QWidget()
        cw.setStyleSheet("background:transparent;")
        fl = QVBoxLayout(cw)
        fl.setSpacing(6)
        fl.setContentsMargins(0, 0, 8, 0)

        # ── Sección global (common.CFG) ───────────────────────────────────────
        fl.addWidget(self._section_label("Descarga — configuracion global"))

        self._add_combo_row(
            fl,
            "cfg_output_type",
            "Formato de empaquetado",
            [("ZIP", "zip"), ("CBZ (Comic Book)", "cbz"), ("PDF", "pdf")],
            _common_cfg().get("output_type", "zip"),
        )

        self._add_combo_row(
            fl,
            "cfg_user_format",
            "Formato de imagen",
            [
                ("WebP (recomendado)", "webp"),
                ("JPG", "jpg"),
                ("PNG", "png"),
                ("Original (sin convertir)", "original"),
            ],
            _common_cfg().get("user_format", "webp"),
        )

        self._add_int_row(
            fl,
            "cfg_max_workers",
            "Hilos de descarga paralela",
            _common_cfg().get("max_workers", 8),
            1,
            32,
        )

        self._add_bool_row(
            fl,
            "cfg_delete_temp",
            "Eliminar carpeta temporal tras empaquetar",
            _common_cfg().get("delete_temp", True),
        )

        self._add_float_row(
            fl,
            "cfg_retry_delay",
            "Delay entre reintentos globales (s)",
            float(_common_cfg().get("retry_delay", 2.0)),
            0.0,
            30.0,
        )

        # Timeout global (tupla connect, read)
        timeout_val = _common_cfg().get("timeout", (15, 45))
        if isinstance(timeout_val, (tuple, list)) and len(timeout_val) == 2:
            connect_t, read_t = int(timeout_val[0]), int(timeout_val[1])
        else:
            connect_t, read_t = 15, 45
        self._add_int_row(
            fl, "cfg_timeout_connect", "Timeout conexión global (s)", connect_t, 1, 120
        )
        self._add_int_row(
            fl, "cfg_timeout_read", "Timeout lectura global (s)", read_t, 1, 300
        )

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background:rgba(157,70,255,0.25);margin:8px 0px;")
        fl.addWidget(sep)

        # ── Sección específica del downloader ─────────────────────────────────
        site_fields = _SITE_CONFIG_FIELDS.get(self.site.get("type", ""), [])
        if site_fields:
            fl.addWidget(
                self._section_label(f"{self.site['name']} — configuracion especifica")
            )
            try:
                mod = _load_mod(self.site["type"])
            except Exception:
                mod = None

            for field in site_fields:
                key = field["key"]
                label = field["label"]
                ftype = field["type"]

                # Leer valor actual del módulo
                if mod:
                    raw_val = getattr(mod, key, None)
                else:
                    raw_val = None

                if ftype == "str_ro":
                    row = QHBoxLayout()
                    row.setSpacing(8)
                    row.addWidget(self._field_label(label))
                    row.addWidget(
                        self._ro_label(str(raw_val) if raw_val is not None else "N/A"),
                        1,
                    )
                    fl.addLayout(row)

                elif ftype == "float":
                    val = (
                        float(raw_val) if raw_val is not None else field.get("min", 0.0)
                    )
                    self._add_float_row(
                        fl,
                        f"mod_{key}",
                        label,
                        val,
                        field.get("min", 0.0),
                        field.get("max", 10.0),
                    )

                elif ftype == "int":
                    val = int(raw_val) if raw_val is not None else field.get("min", 1)
                    self._add_int_row(
                        fl,
                        f"mod_{key}",
                        label,
                        val,
                        field.get("min", 1),
                        field.get("max", 100),
                    )

                elif ftype == "bool":
                    val = bool(raw_val) if raw_val is not None else True
                    self._add_bool_row(fl, f"mod_{key}", label, val)

                elif ftype == "timeout":
                    tv = raw_val if raw_val is not None else (15, 45)
                    if isinstance(tv, (tuple, list)) and len(tv) == 2:
                        cv, rv = int(tv[0]), int(tv[1])
                    else:
                        cv, rv = int(tv) if tv else 15, 45
                    self._add_int_row(
                        fl, f"mod_{key}_connect", f"{label} — conexión (s)", cv, 1, 120
                    )
                    self._add_int_row(
                        fl, f"mod_{key}_read", f"{label} — lectura (s)", rv, 1, 300
                    )

                elif ftype == "useragent":
                    # HEADERS dict → User-Agent string
                    ua = ""
                    if isinstance(raw_val, dict):
                        ua = raw_val.get("User-Agent", "")
                    row = QHBoxLayout()
                    row.setSpacing(8)
                    row.addWidget(self._field_label(label))
                    le = _ArrowLineEdit(ua)
                    le.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                    le.setStyleSheet(
                        "QLineEdit{background:rgba(5,5,8,0.85);color:#e0e0e0;"
                        "border:1px solid rgba(157,70,255,0.3);border-radius:4px;padding:4px;}"
                        "QLineEdit:focus{border:1px solid #9d46ff;}"
                    )
                    if self.body_font:
                        le.setFont(self.body_font)
                    row.addWidget(le, 1)
                    fl.addLayout(row)
                    self._widgets[f"mod_{key}_ua"] = le

        fl.addStretch()
        scroll.setWidget(cw)
        root.addWidget(scroll, 1)

        # Botones Aplicar / Restablecer
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        btn_reset = QPushButton("Restablecer")
        btn_reset.setStyleSheet(_BTN_BASE)
        btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.body_font:
            btn_reset.setFont(self.body_font)
        btn_reset.clicked.connect(self._reset_defaults)
        btn_row.addWidget(btn_reset)

        btn_apply = QPushButton("Aplicar")
        btn_apply.setStyleSheet(_BTN_PRIMARY)
        btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.body_font:
            btn_apply.setFont(self.body_font)
        btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(btn_apply)

        root.addLayout(btn_row)

    # ── Helpers para añadir filas ─────────────────────────────────────────────

    def _add_combo_row(
        self,
        layout: QVBoxLayout,
        key: str,
        label: str,
        options: List[tuple],
        current: str,
    ) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._field_label(label))
        cb = self._combo(options, current)
        row.addWidget(cb, 1)
        layout.addLayout(row)
        self._widgets[key] = cb

    def _add_float_row(
        self,
        layout: QVBoxLayout,
        key: str,
        label: str,
        value: float,
        min_v: float,
        max_v: float,
    ) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._field_label(label))
        sb = self._float_spin(value, min_v, max_v)
        row.addWidget(sb)
        row.addStretch()
        layout.addLayout(row)
        self._widgets[key] = sb

    def _add_int_row(
        self,
        layout: QVBoxLayout,
        key: str,
        label: str,
        value: int,
        min_v: int,
        max_v: int,
    ) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._field_label(label))
        sb = self._int_spin(value, min_v, max_v)
        row.addWidget(sb)
        row.addStretch()
        layout.addLayout(row)
        self._widgets[key] = sb

    def _add_bool_row(
        self, layout: QVBoxLayout, key: str, label: str, value: bool
    ) -> None:
        from PySide6.QtWidgets import QCheckBox

        row = QHBoxLayout()
        row.setSpacing(8)
        cb = QCheckBox(label)
        cb.setChecked(value)
        cb.setStyleSheet(
            "color:#ccc;font-size:11px;background:transparent;border:none;"
        )
        if self.body_font:
            cb.setFont(self.body_font)
        row.addWidget(cb)
        row.addStretch()
        layout.addLayout(row)
        self._widgets[key] = cb

    # ── Aplicar / Restablecer ─────────────────────────────────────────────────

    def _apply(self) -> None:
        from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QSpinBox

        cfg = _common_cfg()

        def _get(key: str):
            w = self._widgets.get(key)
            if w is None:
                return None
            if isinstance(w, QComboBox):
                return w.currentData()
            if isinstance(w, QDoubleSpinBox):
                return w.value()
            if isinstance(w, QSpinBox):
                return w.value()
            if isinstance(w, QCheckBox):
                return w.isChecked()
            if isinstance(w, QLineEdit):
                return w.text().strip()
            return None

        # ── Aplicar a common.CFG ──────────────────────────────────────────────
        if _get("cfg_output_type") is not None:
            cfg["output_type"] = _get("cfg_output_type")
        if _get("cfg_user_format") is not None:
            cfg["user_format"] = _get("cfg_user_format")
        if _get("cfg_max_workers") is not None:
            cfg["max_workers"] = int(_get("cfg_max_workers"))
        if _get("cfg_delete_temp") is not None:
            cfg["delete_temp"] = bool(_get("cfg_delete_temp"))
        if _get("cfg_retry_delay") is not None:
            cfg["retry_delay"] = float(_get("cfg_retry_delay"))
        tc = _get("cfg_timeout_connect")
        tr = _get("cfg_timeout_read")
        if tc is not None and tr is not None:
            cfg["timeout"] = (int(tc), int(tr))

        # ── Aplicar al módulo del downloader ─────────────────────────────────
        try:
            mod = _load_mod(self.site["type"])
            site_fields = _SITE_CONFIG_FIELDS.get(self.site.get("type", ""), [])
            for field in site_fields:
                key = field["key"]
                ftype = field["type"]
                if ftype == "str_ro":
                    continue
                elif ftype == "float":
                    v = _get(f"mod_{key}")
                    if v is not None:
                        setattr(mod, key, float(v))
                elif ftype == "int":
                    v = _get(f"mod_{key}")
                    if v is not None:
                        setattr(mod, key, int(v))
                elif ftype == "bool":
                    v = _get(f"mod_{key}")
                    if v is not None:
                        setattr(mod, key, bool(v))
                elif ftype == "timeout":
                    vc = _get(f"mod_{key}_connect")
                    vr = _get(f"mod_{key}_read")
                    if vc is not None and vr is not None:
                        setattr(mod, key, (int(vc), int(vr)))
                elif ftype == "useragent":
                    ua = _get(f"mod_{key}_ua")
                    if ua:
                        current_headers = getattr(mod, key, {})
                        if isinstance(current_headers, dict):
                            current_headers = dict(current_headers)
                            current_headers["User-Agent"] = ua
                            setattr(mod, key, current_headers)
        except Exception as e:
            logging.warning(f"[Babylon Config] Error aplicando config a módulo: {e}")

        logging.info(f"[Babylon] Config aplicada para {self.site['name']}")
        self.back_requested.emit()

    def _reset_defaults(self) -> None:
        """Recarga los valores actuales del módulo en los widgets."""
        from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QSpinBox

        try:
            mod = _load_mod(self.site["type"])
        except Exception:
            mod = None

        # Reset common.CFG defaults
        defaults = {
            "cfg_output_type": "zip",
            "cfg_user_format": "webp",
            "cfg_max_workers": 8,
            "cfg_delete_temp": True,
            "cfg_retry_delay": 2.0,
            "cfg_timeout_connect": 15,
            "cfg_timeout_read": 45,
        }
        for key, val in defaults.items():
            w = self._widgets.get(key)
            if w is None:
                continue
            if isinstance(w, QComboBox):
                for i in range(w.count()):
                    if w.itemData(i) == val:
                        w.setCurrentIndex(i)
                        break
            elif isinstance(w, (QDoubleSpinBox, QSpinBox)):
                w.setValue(val)
            elif isinstance(w, QCheckBox):
                w.setChecked(val)

        logging.info(f"[Babylon] Config restablecida para {self.site['name']}")


def _common_cfg() -> Dict:
    """Accede a common.CFG del directorio babylon_downloaders."""
    try:
        mod = _load_mod.__wrapped__ if hasattr(_load_mod, "__wrapped__") else None
        common_path = os.path.join(_DL_DIR, "common.py")
        if "common" not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                "_babylon_common", common_path
            )
            if spec and spec.loader:
                m = importlib.util.module_from_spec(spec)
                sys.modules["_babylon_common"] = m
                spec.loader.exec_module(m)  # type: ignore[union-attr]
        common_mod = sys.modules.get("_babylon_common")
        if common_mod and hasattr(common_mod, "CFG"):
            return common_mod.CFG
    except Exception:
        pass
    # Fallback: devuelve los defaults de common.py
    return {
        "output_type": "zip",
        "user_format": "webp",
        "max_workers": 8,
        "delete_temp": True,
        "timeout": (15, 45),
        "retry_delay": 2.0,
    }


_SITE_HINTS: Dict[str, str] = {
    "18mh": "Pulsa Listar para ver el catálogo, o escribe un nombre para buscar.",
    "bakamh": "Busca por nombre, o elige género/orden y pulsa Listar.",
    "baozimh": "Busca por nombre, o filtra región/estado/género y pulsa Listar.",
    "dumanwu": "Elige categoría y pulsa Listar, o busca por nombre.",
    "hitomi": "Busca por ID numérico (ej: 123456) o tags (ej: female:mind_control language:spanish). Pulsa Listar para ver catálogo por idioma.",
    "mangafox": "Busca por nombre, o pulsa Listar para ver el catálogo.",
    "manhuagui": "Busca por nombre, o filtra región/género/público/estado y pulsa Listar.",
    "picacomic": "Busca por nombre, o elige categoría/orden y pulsa Listar.",
    "toonkor": "Busca por nombre, o pulsa Listar para ver todas las series.",
    "wfwf": "Elige Webtoon/Manhwa/Ambos y pulsa Listar, o escribe un nombre.",
}


_SEARCH_PLACEHOLDER: Dict[str, str] = {
    "hitomi": "ID (ej: 123456) o tags (ej: female:mind_control language:spanish)",
    "18mh": "Buscar por nombre…",
    "bakamh": "Buscar por nombre…",
    "baozimh": "Buscar por nombre…",
    "dumanwu": "Buscar por nombre…",
    "mangafox": "Buscar por nombre…",
    "manhuagui": "Buscar por nombre…",
    "picacomic": "Buscar por nombre…",
    "toonkor": "Buscar por nombre…",
    "wfwf": "Buscar por nombre…",
}


class BabylonSiteDetailPanel(QWidget):
    back_requested = Signal()
    series_requested = Signal(dict)
    config_requested = Signal()  # ← abre el panel de configuración

    def __init__(
        self,
        site: Dict,
        parent: Optional[QWidget] = None,
        title_font: Optional[QFont] = None,
        body_font: Optional[QFont] = None,
    ) -> None:
        super().__init__(parent)
        self.site = site
        self.title_font = title_font
        self.body_font = body_font
        self._filter_combos: Dict[str, QComboBox] = {}
        self._pool = QThreadPool.globalInstance()
        # Estado de paginación
        self._cur_page: int = 1
        self._cur_query: str = ""
        self._cur_filters: Dict = {}
        self._has_more: bool = False
        self._busy: bool = False
        self._build_ui()
        self._load_dyn()

    def _build_ui(self) -> None:
        self.setObjectName("BabylonSiteDetailPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#BabylonSiteDetailPanel{{{_PANEL_BG}}}")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)

        # ── Header ───────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        btn_back = QPushButton("VOLVER")
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.setStyleSheet(_BTN_BASE)
        if self.body_font:
            btn_back.setFont(self.body_font)
        btn_back.clicked.connect(self.back_requested.emit)
        hdr.addWidget(btn_back)

        btn_cfg = QPushButton("Config")
        btn_cfg.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cfg.setStyleSheet(_BTN_BASE)
        if self.body_font:
            btn_cfg.setFont(self.body_font)
        btn_cfg.clicked.connect(self.config_requested.emit)
        hdr.addWidget(btn_cfg)

        hdr.addStretch()
        lt = QLabel(f"{self.site['name']}  —  {self.site.get('url', '')}")
        lt.setStyleSheet(
            "color:#bd7aff;font-size:13px;background:transparent;border:none;"
        )
        if self.title_font:
            lt.setFont(self.title_font)
        lt.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hdr.addWidget(lt)
        root.addLayout(hdr)

        # ── Pista ─────────────────────────────────────────────────────────────
        hint = _SITE_HINTS.get(self.site.get("type", ""), "")
        if hint:
            lh = QLabel(f"{hint}")
            lh.setStyleSheet(
                "color:#555;font-size:11px;background:transparent;border:none;"
            )
            root.addWidget(lh)

        # ── Filtros ──────────────────────────────────────────────────────────
        fconf = SITE_FILTER_CONFIG.get(self.site.get("type", ""), [])
        if fconf:
            fr = QHBoxLayout()
            fr.setSpacing(8)
            for fd in fconf:
                lbl = QLabel(fd["label"] + ":")
                lbl.setStyleSheet("color:#ccc;background:transparent;border:none;")
                if self.body_font:
                    lbl.setFont(self.body_font)
                cb = QComboBox()
                cb.setMinimumWidth(130)
                for d, v in fd["options"]:
                    cb.addItem(d, v)
                if self.body_font:
                    cb.setFont(self.body_font)
                self._filter_combos[fd["id"]] = cb
                fr.addWidget(lbl)
                fr.addWidget(cb)
            fr.addStretch()
            root.addLayout(fr)

        # ── Búsqueda ─────────────────────────────────────────────────────────
        sr = QHBoxLayout()
        self._search = _ArrowLineEdit()
        self._search.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        placeholder = _SEARCH_PLACEHOLDER.get(self.site.get("type", ""), "Buscar…")
        self._search.setPlaceholderText(placeholder)
        self._search.returnPressed.connect(self._do_search)
        if self.body_font:
            self._search.setFont(self.body_font)
        sr.addWidget(self._search, 1)
        for label, slot in [("Buscar", self._do_search), ("Listar", self._do_list)]:
            b = QPushButton(label)
            b.setStyleSheet(_BTN_BASE)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if self.body_font:
                b.setFont(self.body_font)
            b.clicked.connect(slot)
            sr.addWidget(b)
        root.addLayout(sr)

        # ── Status + navegación ───────────────────────────────────────────────
        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)

        self._btn_prev = QPushButton("◀  Anterior")
        self._btn_prev.setEnabled(False)
        self._btn_prev.setStyleSheet(_BTN_NAV)
        self._btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.body_font:
            self._btn_prev.setFont(self.body_font)
        self._btn_prev.clicked.connect(self._prev_page)
        nav_row.addWidget(self._btn_prev)

        self._lbl_status = QLabel("")
        self._lbl_status.setStyleSheet(
            "color:#888;font-size:11px;background:transparent;border:none;"
        )
        if self.body_font:
            self._lbl_status.setFont(self.body_font)
        self._lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav_row.addWidget(self._lbl_status, 1)

        self._btn_next = QPushButton("Siguiente  ▶")
        self._btn_next.setEnabled(False)
        self._btn_next.setStyleSheet(_BTN_NAV)
        self._btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.body_font:
            self._btn_next.setFont(self.body_font)
        self._btn_next.clicked.connect(self._next_page)
        nav_row.addWidget(self._btn_next)

        root.addLayout(nav_row)

        # ── Resultados ────────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background:transparent;border:none;")
        self._res_container = QWidget()
        self._res_container.setStyleSheet("background:transparent;")
        self._res_layout = QVBoxLayout(self._res_container)
        self._res_layout.setSpacing(5)
        self._res_layout.setContentsMargins(0, 0, 4, 0)
        self._res_layout.addStretch()
        scroll.setWidget(self._res_container)
        self._scroll = scroll
        root.addWidget(scroll, 1)

    # ── Opciones dinámicas ────────────────────────────────────────────────────

    def _load_dyn(self) -> None:
        if self.site.get("type") in ("bakamh", "picacomic"):
            w = BabylonDynamicOptsWorker(self.site)
            w.signals.finished.connect(self._on_dyn)
            self._pool.start(w)

    def _on_dyn(self, fid: str, options: List[tuple]) -> None:
        cb = self._filter_combos.get(fid)
        if cb:
            cb.clear()
            for d, v in options:
                cb.addItem(d, v)

    # ── Lógica de búsqueda / paginado ─────────────────────────────────────────

    def _get_filters(self) -> Dict[str, str]:
        return {
            fid: (
                cb.currentData() if cb.currentData() is not None else cb.currentText()
            )
            for fid, cb in self._filter_combos.items()
        }

    def _do_search(self) -> None:
        q = self._search.text().strip()
        if q:
            self._cur_query = q
            self._cur_filters = self._get_filters()
            self._load_page(1)

    def _do_list(self) -> None:
        self._cur_query = ""
        self._cur_filters = self._get_filters()
        self._load_page(1)

    def _next_page(self) -> None:
        if self._has_more and not self._busy:
            self._load_page(self._cur_page + 1)

    def _prev_page(self) -> None:
        if self._cur_page > 1 and not self._busy:
            self._load_page(self._cur_page - 1)

    def _load_page(self, page: int) -> None:
        if self._busy:
            return
        self._busy = True
        self._cur_page = page
        self._lbl_status.setText(f"Cargando página {page}…")
        self._btn_prev.setEnabled(False)
        self._btn_next.setEnabled(False)
        self._clear()

        w = BabylonSearchWorker(self.site, self._cur_query, self._cur_filters, page)
        w.signals.finished.connect(self._on_results)
        w.signals.error.connect(self._on_error)
        self._pool.start(w)

    def _on_results(self, items: List[Dict], has_more: bool, total_hint: str) -> None:
        self._busy = False
        self._has_more = has_more
        self._clear()

        if not items:
            lbl = QLabel("Sin resultados.")
            lbl.setStyleSheet("color:#555;background:transparent;border:none;")
            if self.body_font:
                lbl.setFont(self.body_font)
            self._res_layout.insertWidget(0, lbl)
            self._lbl_status.setText("Sin resultados")
        else:
            for item in items:
                self._res_layout.insertWidget(
                    self._res_layout.count() - 1, self._make_card(item)
                )
            # Construir texto de estado con total si está disponible
            page_info = f"Página {self._cur_page}  •  {len(items)} en esta página"
            if total_hint:
                page_info += f"  •  {total_hint}"
            if has_more:
                page_info += "  →"
            self._lbl_status.setText(page_info)

        # Scroll al tope
        self._scroll.verticalScrollBar().setValue(0)

        # Actualizar botones de navegación
        self._btn_prev.setEnabled(self._cur_page > 1)
        self._btn_next.setEnabled(has_more)

    def _on_error(self, msg: str) -> None:
        self._busy = False
        self._lbl_status.setText(f"Error: {msg[:80]}")
        self._btn_prev.setEnabled(self._cur_page > 1)
        self._btn_next.setEnabled(False)

    def _clear(self) -> None:
        while self._res_layout.count() > 1:
            it = self._res_layout.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()

    def _make_card(self, item: Dict) -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet(
            "QFrame{background:rgba(25,28,38,140);"
            "border:1px solid rgba(157,70,255,0.2);border-radius:6px;}"
            "QFrame:hover{border:1px solid rgba(157,70,255,0.5);}"
        )
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 7, 12, 7)

        title_lbl = QLabel(item.get("title", "(sin título)"))
        title_lbl.setStyleSheet("color:#ddd;background:transparent;border:none;")
        title_lbl.setWordWrap(True)
        title_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title_lbl.setCursor(Qt.CursorShape.IBeamCursor)
        if self.body_font:
            title_lbl.setFont(self.body_font)
        lay.addWidget(title_lbl, 1)

        btn_view = QPushButton("VER SERIE")
        btn_view.setMinimumWidth(100)
        btn_view.setStyleSheet(_BTN_PRIMARY)
        btn_view.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.body_font:
            btn_view.setFont(self.body_font)
        can_open = bool(item.get("slug") and item.get("slug") != "__no_token__")
        btn_view.setEnabled(can_open)

        def _open_series(i=item):
            self.series_requested.emit(i)

        btn_view.clicked.connect(lambda _c=False, i=item: _open_series(i))
        lay.addWidget(btn_view)

        # Click en el fondo del card (no widgets hijos) abre la serie
        if can_open:
            def _card_press(ev, i=item):
                if ev.button() == Qt.MouseButton.LeftButton:
                    self.series_requested.emit(i)
            card.mousePressEvent = _card_press  # type: ignore

        return card


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL RAÍZ
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
        self._grid: Optional[QWidget] = None
        self._site_p: Optional[BabylonSiteDetailPanel] = None
        self._series_p: Optional[BabylonSeriesPanel] = None
        self._dl_p: Optional[BabylonDownloadPanel] = None
        self._cfg_p: Optional[BabylonConfigPanel] = None
        self._cur_site: Optional[str] = None
        self._cur_site_obj: Optional[Dict] = None
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._build_grid()

    def _build_grid(self) -> None:
        self._grid = QWidget(self)
        self._grid.setStyleSheet("background:transparent;border:none;")
        gv = QVBoxLayout(self._grid)
        gv.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        cw = QWidget()
        cw.setStyleSheet("background:transparent;border:none;")
        cl = QVBoxLayout(cw)
        cl.setContentsMargins(8, 8, 8, 8)
        gc = QWidget()
        grid = QGridLayout(gc)
        qt = cast(Any, Qt)
        grid.setAlignment(qt.AlignLeft | qt.AlignTop)
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)
        self._fill_grid(grid)
        cl.addWidget(gc)
        cl.addStretch()
        scroll.setWidget(cw)
        gv.addWidget(scroll, 1)
        self._root.addWidget(self._grid)

    def _fill_grid(self, grid: QGridLayout) -> None:
        qt = cast(Any, Qt)
        base_s = "border:1px solid rgba(150,0,150,50);border-radius:8px;background:rgba(30,30,30,150);"
        hover_s = (
            "border:2px solid #960096;border-radius:8px;background:rgba(150,0,150,30);"
        )

        def _enter(l: QLabel, d: QLabel):
            l.setStyleSheet(hover_s)
            d.show()

        def _leave(l: QLabel, d: QLabel):
            l.setStyleSheet(base_s)
            d.hide()

        cols, sz, marg = 6, (120, 120), 25
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
                scaled = (
                    pix.scaled(
                        sz[0] - marg,
                        sz[1] - marg,
                        qt.AspectRatioMode.KeepAspectRatio,
                        qt.TransformationMode.SmoothTransformation,
                    )
                    if not pix.isNull()
                    else QPixmap(sz[0] - marg, sz[1] - marg)
                )
                if pix.isNull():
                    scaled.fill(Qt.GlobalColor.transparent)

                img = QLabel()
                img.setPixmap(scaled)
                if pix.isNull():
                    img.setText("?")
                img.setFixedSize(*sz)
                img.setAlignment(qt.AlignCenter)
                img.setStyleSheet(base_s)
                img.setCursor(qt.PointingHandCursor)

                desc = QLabel(
                    f"<b>{site['name']}</b><br><small>{site['description']}</small>",
                    img,
                )
                desc.setFixedSize(*sz)
                desc.setWordWrap(True)
                desc.setAlignment(qt.AlignCenter)
                desc.setStyleSheet(
                    "color:white;background:rgba(0,0,0,200);"
                    "font-size:11px;padding:8px;border-radius:8px;"
                )
                desc.hide()
                desc.setAttribute(qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

                img.enterEvent = lambda a0, l=img, d=desc: _enter(l, d)  # type: ignore
                img.leaveEvent = lambda a0, l=img, d=desc: _leave(l, d)  # type: ignore

                def _mk(s=site):
                    def _on(_ev):
                        self._open_site(s)

                    return _on

                img.mousePressEvent = _mk()  # type: ignore

                row, col = divmod(i, cols)
                grid.addWidget(img, row, col)
            except Exception as e:
                logging.warning(f"[Babylon] Icono {site.get('name')}: {e}")

    def _show_grid(self) -> None:
        for w in (self._site_p, self._series_p, self._dl_p, self._cfg_p):
            if w:
                w.hide()
        if self._grid:
            self._grid.show()

    def _open_site(self, site: Dict) -> None:
        if self._grid:
            self._grid.hide()
        for w in (self._series_p, self._dl_p, self._cfg_p):
            if w:
                w.hide()

        if self._site_p is None or self._cur_site != site["type"]:
            if self._site_p:
                self._root.removeWidget(self._site_p)
                self._site_p.deleteLater()
            self._site_p = BabylonSiteDetailPanel(
                site=site,
                parent=self,
                title_font=self.title_font,
                body_font=self.body_font,
            )
            self._site_p.back_requested.connect(self._show_grid)
            self._site_p.series_requested.connect(self._open_series)
            self._site_p.config_requested.connect(self._open_config)
            self._root.addWidget(self._site_p)
            self._cur_site = site["type"]
            self._cur_site_obj = site

        self._site_p.show()

    def _open_config(self) -> None:
        if self._site_p:
            self._site_p.hide()

        if self._cfg_p:
            self._root.removeWidget(self._cfg_p)
            self._cfg_p.deleteLater()

        self._cfg_p = BabylonConfigPanel(
            site=self._cur_site_obj or {},
            parent=self,
            body_font=self.body_font,
            title_font=self.title_font,
        )
        self._cfg_p.back_requested.connect(self._back_from_config)
        self._root.addWidget(self._cfg_p)
        self._cfg_p.show()

    def _back_from_config(self) -> None:
        if self._cfg_p:
            self._cfg_p.hide()
        if self._site_p:
            self._site_p.show()

    def _open_series(self, item: Dict) -> None:
        if self._cur_site is None:
            return
        if self._site_p:
            self._site_p.hide()
        if self._dl_p:
            self._dl_p.hide()

        if self._series_p:
            self._root.removeWidget(self._series_p)
            self._series_p.deleteLater()

        self._series_p = BabylonSeriesPanel(
            site=self._cur_site_obj or {},
            item=item,
            parent=self,
            body_font=self.body_font,
            title_font=self.title_font,
        )
        self._series_p.back_requested.connect(self._back_to_site)
        self._series_p.download_requested.connect(self._start_dl)
        self._root.addWidget(self._series_p)
        self._series_p.show()

    def _back_to_site(self) -> None:
        if self._series_p:
            self._series_p.hide()
        if self._site_p:
            self._site_p.show()

    def _start_dl(self, series: Dict, chapters: List[Dict], output_dir: str) -> None:
        if self._series_p:
            self._series_p.hide()
        if self._dl_p:
            self._root.removeWidget(self._dl_p)
            self._dl_p.deleteLater()

        self._dl_p = BabylonDownloadPanel(
            site_type=self._cur_site or "",
            series=series,
            chapters=chapters,
            output_dir=output_dir,
            parent=self,
            body_font=self.body_font,
            title_font=self.title_font,
        )
        self._dl_p.back_requested.connect(self._back_to_series)
        self._root.addWidget(self._dl_p)
        self._dl_p.show()

    def _back_to_series(self) -> None:
        if self._dl_p:
            self._dl_p.hide()
        if self._series_p:
            self._series_p.show()
