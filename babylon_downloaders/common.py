"""
common.py — Utilidades compartidas por todos los downloaders.
Incluye: colores, guardado de imagen, empaquetado, worker de descarga,
         runner genérico de capítulos.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Callable, Optional

# Consola Windows: stdout cp1252 rompe al imprimir CJK (mismo patrón que d_pigmh)
try:
    if sys.stdout and getattr(sys.stdout, "buffer", None):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    Image = None
    HAS_PILLOW = False


# ══════════════════════════════════════════════════════════════
#  CONFIGURACIÓN GLOBAL  (puede sobreescribirse desde menu.py)
# ══════════════════════════════════════════════════════════════
CFG = {
    "output_type": "zip",  # zip | cbz | pdf
    "user_format": "webp",  # original | jpg | png | webp
    "max_workers": 8,
    "delete_temp": True,
    "timeout": (15, 45),
    "retry_delay": 2.0,
}


# ══════════════════════════════════════════════════════════════
#  COLORES
# ══════════════════════════════════════════════════════════════
class C:
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"


def bar(done: int, total: int, width: int = 32) -> str:
    pct = done / max(total, 1)
    fill = int(width * pct)
    return f"[{C.CYAN}{'█' * fill}{C.DIM}{'─' * (width - fill)}{C.END}] {done}/{total}"


def parse_positions(s: str, length: int) -> list[int]:
    """'1,3-5,8' → [0,2,3,4,7]"""
    idxs: set[int] = set()
    for part in s.replace(" ", "").split(","):
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                idxs.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        elif part.isdigit():
            idxs.add(int(part))
    return sorted(i - 1 for i in idxs if 1 <= i <= length)


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()


# ══════════════════════════════════════════════════════════════
#  IMAGEN — guardado con conversión opcional
# ══════════════════════════════════════════════════════════════
def save_image(raw: bytes, path: str, user_format: Optional[str] = None) -> None:
    fmt = user_format or CFG["user_format"]
    if not HAS_PILLOW or fmt == "original" or Image is None:
        with open(path, "wb") as f:
            f.write(raw)
        return
    try:
        img = Image.open(BytesIO(raw))
        if fmt in ("jpg", "jpeg") and img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            mask = img.split()[-1] if img.mode in ("RGBA", "LA") else None
            bg.paste(img, mask=mask)
            img = bg
        if fmt == "webp":
            # WebP lossless: el formato elegido se mantiene SIN pérdida de calidad
            img.save(path, format="WEBP", lossless=True, method=6)
        elif fmt in ("jpg", "jpeg"):
            img.save(path, format="JPEG", quality=92)
        else:
            img.save(path)
    except Exception:
        with open(path, "wb") as f:
            f.write(raw)


def ext_for(url: str, user_format: Optional[str] = None) -> str:
    fmt = user_format or CFG["user_format"]
    if HAS_PILLOW and fmt != "original":
        return fmt
    raw_ext = os.path.splitext(url.split("?")[0])[-1].lower().lstrip(".")
    return raw_ext if raw_ext in ("jpg", "jpeg", "png", "webp", "gif", "avif", "bmp") else "jpg"


# ══════════════════════════════════════════════════════════════
#  METADATOS DE SERIE  (portada / etiquetas / ficha)
#
#  Helper genérico que cada downloader puede fusionar en su `series`:
#      series.update(extract_series_extras(html_o_soup, BASE_URL))
#  Deja disponibles las claves que el panel consume:
#      series["cover"] -> URL de la portada
#      series["tags"]  -> list[str] de etiquetas/géneros
#      series["meta"]  -> dict {etiqueta: valor} (Autor, Estado, Sinopsis…)
# ══════════════════════════════════════════════════════════════
_GENRE_HREF_HINTS = (
    "genre", "genero", "género", "tag", "category", "categoria",
    "classify", "sort", "manga-genre",
)


def extract_series_extras(html_or_soup, base_url: str = "") -> dict:
    """Extrae portada, etiquetas y metadatos de la ficha HTML de una serie.

    Acepta un `BeautifulSoup` ya construido o HTML crudo. Nunca lanza:
    devuelve sólo las claves que pudo determinar (`cover`, `tags`, `meta`)."""
    out: dict = {}
    try:
        soup = html_or_soup
        if isinstance(soup, str) or not hasattr(soup, "select_one"):
            if not html_or_soup:
                return out
            try:
                from bs4 import BeautifulSoup
            except ImportError:
                return out
            try:
                soup = BeautifulSoup(str(html_or_soup), "lxml")
            except Exception:
                soup = BeautifulSoup(str(html_or_soup), "html.parser")

        def _meta_content(*keys: str) -> str:
            for key in keys:
                for attr in ("property", "name", "itemprop"):
                    tag = soup.find("meta", attrs={attr: key})
                    if tag and tag.get("content"):
                        v = str(tag["content"]).strip()
                        if v:
                            return v
            return ""

        # ── Portada ──────────────────────────────────────────
        cover = _meta_content("og:image", "og:image:secure_url", "twitter:image")
        if not cover:
            for sel in (
                "img.cover", "img.cover-img", "img.book-cover",
                ".cover img", ".cover-img img", ".comic-cover img",
                ".book-cover img", ".summary_image img", ".poster img",
                ".detail-info-cover-img", ".manga-cover img", ".thumb img",
                ".comic-cover", ".book-cover",
            ):
                img = soup.select_one(sel)
                if img and hasattr(img, "get"):
                    cover = (
                        img.get("data-src") or img.get("data-original")
                        or img.get("data-lazy-src") or img.get("src") or ""
                    ).strip()
                    if cover:
                        break
        if cover:
            if cover.startswith("//"):
                cover = "https:" + cover
            elif cover.startswith("/") and base_url:
                cover = base_url.rstrip("/") + cover
            out["cover"] = cover

        # ── Sinopsis / descripción ───────────────────────────
        desc = _meta_content("og:description", "description")
        if not desc:
            for sel in (
                ".fullcontent", ".summary__content p", ".summary__content",
                ".summary-content", ".comic-intro", ".book-intro",
                "p.introduction", ".detail-info-right-content",
                ".manga-excerpt", ".description", ".intro", ".book-detail",
            ):
                node = soup.select_one(sel)
                if node:
                    t = node.get_text(" ", strip=True)
                    if t and len(t) > 10:
                        desc = t
                        break

        # ── Etiquetas / géneros ──────────────────────────────
        tags: list[str] = []
        seen: set[str] = set()
        for sel in (
            ".genres-content a", ".detail-info-right-tag-list a",
            ".book-label a", ".book-tags a", ".tag-list a", ".tags a",
            ".manga-genres a", ".categories a", ".genres a",
            ".post-content_item .genres-content a", ".manga-tags a",
        ):
            for a in soup.select(sel):
                t = a.get_text(strip=True)
                if t and 1 < len(t) <= 40 and t.lower() not in seen:
                    seen.add(t.lower())
                    tags.append(t)
            if tags:
                break
        if not tags:
            for a in soup.find_all("a", href=True):
                href = str(a["href"]).lower()
                if any(h in href for h in _GENRE_HREF_HINTS):
                    t = a.get_text(strip=True)
                    if t and 1 < len(t) <= 40 and t.lower() not in seen:
                        seen.add(t.lower())
                        tags.append(t)
        if not tags:
            kw = _meta_content("keywords")
            if kw:
                tags = [t.strip() for t in kw.split(",") if t.strip()][:20]
        if tags:
            out["tags"] = tags[:30]

        # ── Autor / estado ───────────────────────────────────
        author = ""
        for sel in (
            ".author-content a", ".detail-info-right-say a",
            ".book-author a", ".author a", ".manga-authors a",
            "a[href*='author']", ".artist-content a", ".book-author",
        ):
            node = soup.select_one(sel)
            if node and node.get_text(strip=True):
                author = node.get_text(strip=True)
                break
        status = ""
        for sel in (
            ".post-status .summary-content", ".detail-info-right-title-tip",
            ".book-status", ".manga-status .summary-content",
            ".status", ".comic-status", ".detail-info-right-title-tip",
        ):
            node = soup.select_one(sel)
            if node:
                t = node.get_text(strip=True)
                if t and len(t) < 30:
                    status = t
                    break

        meta_fields: dict[str, str] = {}
        if author:
            meta_fields["Autor"] = author
        if status:
            meta_fields["Estado"] = status
        if desc:
            meta_fields["Sinopsis"] = desc[:400]
        if meta_fields:
            out["meta"] = meta_fields
    except Exception:
        return out
    return out


# ══════════════════════════════════════════════════════════════
#  EMPAQUETADO
# ══════════════════════════════════════════════════════════════
def pack_folder(src: str, out: str, fmt: Optional[str] = None) -> None:
    output_fmt = fmt or CFG["output_type"]
    files = sorted(
        os.path.join(src, f)
        for f in os.listdir(src)
        if os.path.isfile(os.path.join(src, f))
    )
    if not files:
        return
    if output_fmt == "pdf" and HAS_PILLOW and Image is not None:
        pages = []
        for p in files:
            try:
                pages.append(Image.open(p).convert("RGB"))
            except Exception:
                pass
        if pages:
            pages[0].save(out, format="PDF", save_all=True, append_images=pages[1:])
    else:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, os.path.basename(f))


# ══════════════════════════════════════════════════════════════
#  RUNNER GENÉRICO DE DESCARGA
#
#  Cada downloader implementa la interfaz BaseDownloader.
#  Este runner usa:
#    - downloader.get_chapter_images(chapter, series) -> list[str]
#    - downloader.dl_image(url, referer)              -> bytes | None
# ══════════════════════════════════════════════════════════════
def run_download(
    downloader: "BaseDownloader",
    series: dict,
    selected_chapters: list[dict],
    output_type: Optional[str] = None,
    user_format: Optional[str] = None,
    max_workers: Optional[int] = None,
    delete_temp: Optional[bool] = None,
    out_base: Optional[str] = None,
) -> int:
    fmt_out = output_type or CFG["output_type"]
    fmt_img = user_format or CFG["user_format"]
    mw = max_workers or CFG["max_workers"]
    dt = delete_temp if delete_temp is not None else CFG["delete_temp"]

    title = series.get("title", "unknown")
    slug = series.get("slug", series.get("id", ""))
    folder = out_base or safe_name(f"{title} [{slug}]")
    os.makedirs(folder, exist_ok=True)

    ok = 0
    print(f"\n{C.CYAN}[*] Descargando {len(selected_chapters)} cap(s)…{C.END}\n")

    for i, chap in enumerate(selected_chapters, 1):
        chap_title = chap.get("title", f"cap_{i}")
        label = f"[{i}/{len(selected_chapters)}] {chap_title[:50]}"
        print(f"  {C.BOLD}{label}{C.END}", end=" ", flush=True)

        imgs = downloader.get_chapter_images(chap, series)
        if not imgs:
            print(f"\n    {C.RED}× Sin imágenes{C.END}")
            continue

        print(f"\n    → {len(imgs)} págs", flush=True)

        safe_chap = safe_name(chap_title) or f"cap_{i:04d}"
        out_file = os.path.join(folder, f"{i:04d} - {safe_chap}.{fmt_out}")
        tmp = os.path.join(folder, f"_tmp_{i}")
        os.makedirs(tmp, exist_ok=True)

        # Descarga paralela
        done = 0
        referer = downloader.get_referer(chap, series)

        def _worker(args):
            url, tmp_folder, idx = args
            path = os.path.join(tmp_folder, f"{idx + 1:03d}.{ext_for(url, fmt_img)}")
            if os.path.exists(path):
                return True
            raw = downloader.dl_image(url, referer)
            if raw:
                save_image(raw, path, fmt_img)
                return True
            return False

        with ThreadPoolExecutor(max_workers=mw) as exe:
            futures = {
                exe.submit(_worker, (url, tmp, idx)): idx
                for idx, url in enumerate(imgs)
            }
            for _ in as_completed(futures):
                done += 1
                sys.stdout.write(f"\r    {bar(done, len(imgs))}")
                sys.stdout.flush()
        print()

        pack_folder(tmp, out_file, fmt_out)
        if dt:
            shutil.rmtree(tmp, ignore_errors=True)
        ok += 1
        print(f"    {C.GREEN}✓ → {os.path.basename(out_file)}{C.END}")

    print(
        f"\n{C.GREEN}[+] {ok}/{len(selected_chapters)} completados → {folder}/{C.END}"
    )
    return ok


# ══════════════════════════════════════════════════════════════
#  INTERFAZ BASE  (protocol / clase abstracta ligera)
# ══════════════════════════════════════════════════════════════
class BaseDownloader:
    """
    Interfaz que cada downloader debe implementar.

    Diccionarios estándar:
      item    → {"id": str, "title": str, ...}  (resultado de search/catalog)
      series  → {"id": str, "slug": str, "title": str, ...}
      chapter → {"id": str, "title": str, ...}
    """

    NAME: str = "?"
    NEEDS_LOGIN: bool = False
    HAS_CATALOG: bool = True
    HAS_SEARCH: bool = True

    # ── obligatorio ──────────────────────────────────────────
    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        """Devuelve (series_meta, chapters)."""
        raise NotImplementedError

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        """Devuelve lista de URLs de imágenes."""
        raise NotImplementedError

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        """Descarga una imagen y devuelve los bytes crudos."""
        raise NotImplementedError

    # ── opcionales (con defaults) ─────────────────────────────
    def login(self, **kwargs) -> bool:
        return True

    def search(self, query: str) -> list[dict]:
        return []

    def get_catalog(self, **kwargs) -> list[dict]:
        return []

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list[dict], bool]:
        """
        Devuelve (items_de_esta_página, hay_más).
        Por defecto: carga todo con get_catalog() y cachea.
        Los downloaders con paginación real en el servidor lo sobreescriben.
        """
        cache_key = repr(sorted(kwargs.items()))
        if getattr(self, "_cat_buf_key", None) != cache_key:
            self._cat_buf: list[dict] = self.get_catalog(**kwargs)
            self._cat_buf_key: str = cache_key
        start = (page - 1) * page_size
        end = start + page_size
        return self._cat_buf[start:end], end < len(self._cat_buf)

    def get_referer(self, chapter: dict, series: dict) -> str:
        return ""

    def get_image_count(self, chapter: dict, series: dict) -> Optional[int]:
        """Devuelve el número de imágenes de un capítulo SIN descargar ninguna imagen.

        Implementación por defecto: rasca la página/API del capítulo, extrae las URLs
        y las cuenta (un único GET/parseo). Los descargadores con API/JSON ligero
        pueden sobrescribirlo con una consulta aún más barata.
        Devuelve None si no se puede determinar."""
        try:
            return len(self.get_chapter_images(chapter, series))
        except Exception:
            return None
