"""
babylon_panel.py  —  Panel de descarga multi-sitio para BBSL

Estructura esperada en el proyecto:
    BBSL/
    ├── babylon_panel.py          ← este archivo
    └── babylon_downloaders/
        ├── 18mh_downloader.py
        ├── bakamh_downloader.py
        ├── baozimh_downloader.py
        ├── dumanwu_downloader.py      (renombrar de dumanwu-downloader.py)
        ├── hitomi_downloader.py       (renombrar de hitomi-downloader.py)
        ├── mangafox_downloader.py
        ├── manhuagui_downloader.py
        ├── picacomic_downloader.py
        ├── toonkor_downloader.py      (renombrar de toonkor-downloader.py)
        └── wfwf_downloader.py
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import shiboken6
import logging
from typing import Any, Dict, List, Optional, cast
from urllib.parse import quote

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config import Config, resource_path

# ══════════════════════════════════════════════════════════════════════════════
#  CARGA DINÁMICA DE MÓDULOS
# ══════════════════════════════════════════════════════════════════════════════

_DL_DIR = os.path.join(os.path.dirname(__file__), "babylon_downloaders")

# Asegurar que el directorio de downloaders esté en el path para importaciones locales
if _DL_DIR not in sys.path:
    sys.path.insert(0, _DL_DIR)

_module_cache: Dict[str, Any] = {}


def _load_downloader(site_type: str, filename: str) -> Any:
    """Carga un módulo downloader desde babylon_downloaders/ con caché."""
    if site_type in _module_cache:
        return _module_cache[site_type]

    filepath = os.path.join(_DL_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"Downloader no encontrado: {filepath}\n"
            f"Copiá el archivo '{filename}' a la carpeta 'babylon_downloaders/'"
        )

    mod_name = f"_babylon_dl_{site_type}"
    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo crear spec para: {filepath}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    # --- AUTO-INICIALIZACIÓN DE SESIONES ---
    # Algunos scripts definen SESSION = None y lo inicializan en main().
    # Aquí forzamos la inicialización si el script lo permite.
    for session_attr in ["SESSION", "SESSION_ORG", "SESS", "_sess"]:
        if hasattr(mod, session_attr) and getattr(mod, session_attr) is None:
            # Intentar encontrar una función constructora
            for maker in ["make_session", "_make_session"]:
                if hasattr(mod, maker):
                    func = getattr(mod, maker)
                    # _make_session de baozimh necesita un argumento (base_url)
                    if maker == "_make_session" and site_type == "baozimh":
                        setattr(mod, session_attr, func(getattr(mod, "SITE_ORG", "")))
                    else:
                        setattr(mod, session_attr, func())
                    break

    _module_cache[site_type] = mod
    return mod


# ══════════════════════════════════════════════════════════════════════════════
#  BÚSQUEDA NORMALIZADA POR SITIO
#  Resultado: List[{"title": str, "slug": str, "url": str}]
#  query="" → listar catálogo (donde aplique)
# ══════════════════════════════════════════════════════════════════════════════


def search_site(site: Dict[str, str], query: str) -> List[Dict[str, str]]:
    try:
        mod = _load_downloader(site["type"], site["file"])
    except Exception as e:
        logging.error(f"❌ BABYLON: No se pudo cargar {site['name']}: {e}")
        return []

    t = site["type"]
    results: List[Dict[str, str]] = []

    try:
        # ── 18MH ─────────────────────────────────────────────────────────────────
        if t == "18mh":
            site_url: str = getattr(mod, "SITE_URL", "https://18mh.org")
            fetch_html = mod.fetch_html
            parse_cards = mod._parse_cards
            path = f"{site_url}/s/{quote(query)}" if query else f"{site_url}/manga"
            html = fetch_html(path)
            raw = parse_cards(html) if html else []
            results = [
                {
                    "title": r.get("title", r.get("slug", "")),
                    "slug": r.get("slug", ""),
                    "url": f"{site_url}/manga/{r.get('slug', '')}",
                }
                for r in raw
            ]

        # ── BakaMH ────────────────────────────────────────────────────────────────
        elif t == "bakamh":
            base_url: str = getattr(mod, "BASE_URL", "https://bakamh.com")
            # bakamh.search devuelve (items, total_pages)
            res = mod.search(query)
            items = res[0] if isinstance(res, tuple) else res
            results = [
                {
                    "title": r.get("title", ""),
                    "slug": r.get("slug", ""),
                    "url": r.get("url", "") or f"{base_url}/manga/{r.get('slug', '')}",
                }
                for r in items
            ]

        # ── BaoziMH ───────────────────────────────────────────────────────────────
        elif t == "baozimh":
            site_org: str = getattr(mod, "SITE_ORG", "https://baozimh.org")
            if query:
                raw = mod.search_series(query)
            else:
                html = mod.fetch_org(site_org)
                raw = mod._parse_cards(html) if html else []
            results = [
                {
                    "title": r.get("title", r.get("slug", "")),
                    "slug": r.get("slug", ""),
                    "url": f"{site_org}/manga/{r.get('slug', '')}",
                }
                for r in raw
            ]

        # ── Dumanwu ───────────────────────────────────────────────────────────────
        elif t == "dumanwu":
            base_url = getattr(mod, "BASE_URL", "https://dumanwu.com")
            logic = mod.DumanwuLogic()
            # Dumanwu search suele requerir query. Si es vacío, probamos vacío.
            raw = logic.search(query)
            results = [
                {
                    "title": r.get("title", r.get("slug", "")),
                    "slug": r.get("slug", ""),
                    "url": r.get("url", "") or f"{base_url}/manhua/{r.get('slug', '')}",
                }
                for r in raw
            ]

        # ── Hitomi ────────────────────────────────────────────────────────────────
        elif t == "hitomi":
            if not query:
                logging.info(f"ℹ️ BABYLON: Hitomi requiere términos de búsqueda (ej: 'tag:oriental')")
                return []
            ids: List[int] = mod.search_query(query)
            results = [
                {
                    "title": f"Gallery #{gid}",
                    "slug": str(gid),
                    "url": f"https://hitomi.la/galleries/{gid}.html",
                }
                for gid in ids[:30]
            ]

        # ── Fanfox (MangaFox) ─────────────────────────────────────────────────────
        elif t == "mangafox":
            base_url = getattr(mod, "BASE_URL", "https://fanfox.net")
            if query:
                raw = mod.search_series(query)
            else:
                raw = mod.fetch_full_catalog(max_pages=1) # Solo 1 pág para vista rápida
            results = [
                {
                    "title": r.get("title", r.get("name", "")),
                    "slug": r.get("slug", ""),
                    "url": r.get("url", "") or f"{base_url}/manga/{r.get('slug', '')}",
                }
                for r in raw
            ]

        # ── ManhuaGui ─────────────────────────────────────────────────────────────
        elif t == "manhuagui":
            if not query:
                # Si no hay query, ManhuaGui no tiene un 'search' vacío útil.
                # Podríamos intentar cargar la página principal pero search() fallará con 404.
                logging.info(f"ℹ️ BABYLON: ManhuaGui requiere un nombre de serie para buscar.")
                return []
            res = mod.search(query, page=1)
            raw = res[0] if isinstance(res, tuple) else res
            results = [
                {
                    "title": r.get("title", r.get("name", "")),
                    "slug": r.get("slug", str(r.get("id", ""))),
                    "url": r.get("url", ""),
                }
                for r in raw
            ]

        # ── PicaComic ─────────────────────────────────────────────────────────────
        elif t == "picacomic":
            # Verificar si hay un token activo
            token = getattr(mod, "_token", "")
            if not token:
                logging.warning(f"⚠️ BABYLON: PicaComic requiere login. Ejecuta el script por separado para loguearte.")
            
            res = mod.search(query) if query else mod.search("", sort="dd")
            raw = res[0] if isinstance(res, tuple) else res
            results = [
                {
                    "title": r.get("title", ""),
                    "slug": r.get("id", ""),
                    "url": f"https://picacomic.com/comics/{r.get('id', '')}",
                }
                for r in raw
            ]

        # ── ToonKor ───────────────────────────────────────────────────────────────
        elif t == "toonkor":
            raw = mod.search_query(query) if query else mod.fetch_series_list()
            results = [
                {
                    "title": r.get("title", r.get("slug", "")),
                    "slug": r.get("slug", ""),
                    "url": r.get("url", ""),
                }
                for r in raw
            ]

        # ── WFWF ──────────────────────────────────────────────────────────────────
        elif t == "wfwf":
            Mode = mod.Mode
            mw = Mode(Mode.WEBTOON)
            mm = Mode(Mode.MANHWA)
            if query:
                raw_w = mod.search_series(query, mw)
                raw_m = mod.search_series(query, mm)
            else:
                raw_w = mod.fetch_series_list(mw)
                raw_m = mod.fetch_series_list(mm)
            results = [
                {
                    "title": r.get("title", ""),
                    "slug": r.get("toon_id", r.get("slug", "")),
                    "url": r.get("url", ""),
                }
                for r in (raw_w + raw_m)
            ]

    except Exception as e:
        import traceback
        logging.error(f"❌ BABYLON: Error en {site['name']}: {e}\n{traceback.format_exc()}")
        return []

    return results


# ══════════════════════════════════════════════════════════════════════════════
#  WORKER
# ══════════════════════════════════════════════════════════════════════════════


class BabylonWorkerSignals(QObject):
    finished = Signal(list)
    error = Signal(str)


class BabylonSearchWorker(QRunnable):
    def __init__(self, site: Dict[str, str], query: str) -> None:
        super().__init__()
        self.site = site
        self.query = query
        self.signals = BabylonWorkerSignals()

    def run(self) -> None:
        try:
            results = search_site(self.site, self.query)
            self.signals.finished.emit(results)
        except Exception as exc:
            self.signals.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL DE DETALLE DE SITIO
# ══════════════════════════════════════════════════════════════════════════════


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
        self._pool = QThreadPool.globalInstance()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        # Encabezado
        top = QHBoxLayout()
        top.addStretch()
        lbl_title = QLabel(f"{self.site['name']}  —  {self.site['url']}")
        if self.title_font:
            lbl_title.setFont(self.title_font)
        lbl_title.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        top.addWidget(lbl_title)
        root.addLayout(top)

        # Búsqueda
        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Buscar serie por nombre…")
        if self.body_font:
            self._search_input.setFont(self.body_font)
        self._search_input.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search_input, 1)

        for label, slot in [
            ("Buscar", self._do_search),
            ("Listar todo", self._do_list_all),
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
        self._results_layout.setSpacing(6)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.addStretch()
        scroll.setWidget(self._results_container)
        root.addWidget(scroll, 1)

        # Pie: carpeta destino
        footer = QHBoxLayout()
        self._lbl_dest = QLabel("Destino: sin seleccionar")
        if self.body_font:
            self._lbl_dest.setFont(self.body_font)
        footer.addWidget(self._lbl_dest, 1)
        btn_folder = QPushButton("📁 Carpeta")
        if self.body_font:
            btn_folder.setFont(self.body_font)
        btn_folder.clicked.connect(self._choose_dest)
        footer.addWidget(btn_folder)
        root.addLayout(footer)

    # ── Slots ─────────────────────────────────────────────────────────────────
    def _do_search(self) -> None:
        query = self._search_input.text().strip()
        if not query:
            return
        self._run_worker(query)

    def _do_list_all(self) -> None:
        self._run_worker("")

    def _run_worker(self, query: str) -> None:
        self._lbl_status.setText("⏳ Cargando…")
        self._clear_results()
        worker = BabylonSearchWorker(self.site, query)
        worker.signals.finished.connect(self._on_results)
        worker.signals.error.connect(self._on_error)
        self._pool.start(worker)

    def _on_results(self, results: List[Dict[str, str]]) -> None:
        self._results = results
        n = len(results)
        plural = "s" if n != 1 else ""
        status_msg = f"{n} resultado{plural} encontrado{plural} en {self.site['name']}"
        self._lbl_status.setText(status_msg)
        logging.info(f"🔎 BABYLON: {status_msg}")
        self._clear_results()
        if not results:
            lbl = QLabel("Sin resultados para esta búsqueda.")
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
        error_msg = f"Error en {self.site['name']}: {msg}"
        self._lbl_status.setText(f"❌ {error_msg}")
        logging.error(f"❌ BABYLON: {error_msg}")

    def _choose_dest(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta destino")
        if folder:
            self._dest_dir = folder
            short = folder if len(folder) <= 50 else "…" + folder[-47:]
            self._lbl_dest.setText(f"Destino: {short}")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _clear_results(self) -> None:
        while self._results_layout.count() > 1:
            item = self._results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _make_result_card(self, item: Dict[str, str]) -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 6, 10, 6)
        lbl = QLabel(item.get("title", "(sin título)"))
        if self.body_font:
            lbl.setFont(self.body_font)
        lbl.setWordWrap(True)
        layout.addWidget(lbl, 1)
        btn_dl = QPushButton("⬇ Descargar")
        if self.body_font:
            btn_dl.setFont(self.body_font)
        btn_dl.setFixedWidth(110)
        btn_dl.clicked.connect(
            lambda _checked=False, i=item: self._launch_downloader(i)
        )
        layout.addWidget(btn_dl)
        return card

    def _launch_downloader(self, item: Dict[str, str]) -> None:
        """Abre el script del sitio en un subprocess pasando --url y --output."""
        filepath = os.path.join(_DL_DIR, self.site["file"])
        if not os.path.exists(filepath):
            self._lbl_status.setText(f"❌ No encontrado: {self.site['file']}")
            return
        args = [sys.executable, filepath]
        url = item.get("url", "")
        if url:
            args += ["--url", url]
        if self._dest_dir:
            args += ["--output", self._dest_dir]
        try:
            subprocess.Popen(args, cwd=_DL_DIR)
            self._lbl_status.setText(f"✅ Abriendo descarga: {item.get('title', '')}")
        except Exception as exc:
            self._lbl_status.setText(f"❌ Error al abrir: {exc}")


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

        # ── Vista 1: grilla ───────────────────────────────────────────────────
        self._grid_widget = QWidget()
        gv = QVBoxLayout(self._grid_widget)
        gv.setContentsMargins(0, 0, 0, 0)
        gv.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        cards_w = QWidget()
        cards_w.setStyleSheet("background: transparent; border: none;")

        # Usar un VBoxLayout con stretch y un Grid interno para empujar todo hacia arriba
        main_cards_layout = QVBoxLayout(cards_w)
        main_cards_layout.setContentsMargins(8, 8, 8, 8)

        grid_container = QWidget()
        self._grid = QGridLayout(grid_container)
        qt_any = cast(Any, Qt)
        self._grid.setAlignment(qt_any.AlignLeft | qt_any.AlignTop)
        self._grid.setSpacing(6)  # Espaciado reducido entre recuadros
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._fill_grid()

        main_cards_layout.addWidget(grid_container)
        main_cards_layout.addStretch()  # Empuja la grilla hacia arriba

        scroll.setWidget(cards_w)
        gv.addWidget(scroll, 1)

        self._root.addWidget(self._grid_widget)

    def _fill_grid(self) -> None:
        base_style = "border: 1px solid rgba(150, 0, 150, 50); border-radius: 8px; background-color: rgba(30, 30, 30, 150);"
        hover_style = "border: 2px solid #960096; border-radius: 8px; background-color: rgba(150, 0, 150, 30);"

        def enter_handler(label: QLabel, desc: QLabel):
            label.setStyleSheet(hover_style)
            desc.show()

        def leave_handler(label: QLabel, desc: QLabel):
            label.setStyleSheet(base_style)
            desc.hide()

        cols = 6
        size = (120, 120)
        margin = 25
        from config import resource_path

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

                pixmap = QPixmap(icon_path) if os.path.exists(icon_path) else QPixmap()
                if not pixmap.isNull():
                    scaled_pixmap = pixmap.scaled(
                        size[0] - margin,
                        size[1] - margin,
                        qt_any.AspectRatioMode.KeepAspectRatio,
                        qt_any.TransformationMode.SmoothTransformation,
                    )
                else:
                    scaled_pixmap = QPixmap(size[0] - margin, size[1] - margin)
                    scaled_pixmap.fill(Qt.GlobalColor.transparent)

                image_label = QLabel()
                image_label.setPixmap(scaled_pixmap)
                if pixmap.isNull():
                    image_label.setText("🌐")
                image_label.setFixedSize(size[0], size[1])
                image_label.setAlignment(qt_any.AlignCenter)
                image_label.setStyleSheet(base_style)
                image_label.setCursor(qt_any.PointingHandCursor)

                # Descripción en Hover
                desc_text = f"<b>{site['name']}</b><br>{site['description']}"
                description_label = QLabel(desc_text, image_label)
                description_label.setFixedSize(size[0], size[1])
                description_label.setWordWrap(True)
                description_label.setAlignment(qt_any.AlignCenter)
                description_label.setStyleSheet(
                    """
                    color: white;
                    background-color: rgba(0, 0, 0, 200);
                    font-size: 11px;
                    padding: 8px;
                    border-radius: 8px;
                    """
                )
                description_label.hide()
                description_label.setAttribute(
                    qt_any.WidgetAttribute.WA_TransparentForMouseEvents, True
                )

                # Eventos Hover
                image_label.enterEvent = (
                    lambda a0, lbl=image_label, desc=description_label: enter_handler(
                        lbl, desc
                    )
                )  # type: ignore
                image_label.leaveEvent = (
                    lambda a0, lbl=image_label, desc=description_label: leave_handler(
                        lbl, desc
                    )
                )  # type: ignore

                # Acción de Click
                def make_click_handler(s=site):
                    def on_click(event):
                        self._open_site(s)

                    return on_click

                image_label.mousePressEvent = make_click_handler()  # type: ignore

                row, col = divmod(i, cols)
                self._grid.addWidget(image_label, row, col)
            except Exception as e:
                pass

    def _open_site(self, site: Dict[str, str]) -> None:
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
