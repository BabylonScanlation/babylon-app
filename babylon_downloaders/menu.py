"""
menu.py — Menú unificado para todos los downloaders.

Uso:
    python menu.py
    python menu.py --debug
"""

from __future__ import annotations

import os
import sys
from getpass import getpass
from typing import Callable, Optional

# ── Config global ─────────────────────────────────────────────────────────────
import common

common.CFG["output_type"] = "zip"
common.CFG["user_format"] = "webp"
common.CFG["max_workers"] = 8
common.CFG["delete_temp"] = True

from common import C, bar, parse_positions, run_download

DEBUG = "--debug" in sys.argv


# ══════════════════════════════════════════════════════════════
#  REGISTRO DE DOWNLOADERS
# ══════════════════════════════════════════════════════════════
DOWNLOADERS: list[tuple[str, str]] = [
    ("bookwalker", "BOOKWALKER    (bookwalker.jp)     — trial CloudFront"),
    ("bookwalkerhar", "BOOKWALKER-HAR (member)      — /c + auto-tokens (config)"),
    ("18mh", "18MH          (18mh.org)          — requests + BS4"),
    ("bakamh", "BAKAMH        (bakamh.com)        — curl_cffi/WP AJAX"),
    ("baozimh", "BAOZIMH       (baozimh.org/com)   — mirrors + API JSON"),
    ("dumanwu", "DUMANWU       (dumanwu.com)       — XOR+base64 decrypt"),
    ("hitomi", "HITOMI        (hitomi.la)          — binario nozomi"),
    ("mangafox", "FANFOX        (fanfox.net)         — requests + BS4"),
    ("manhuagui", "MANHUAGUI     (manhuagui.com)     — p.a.c.k.e.r + LZ"),
    ("picacomic", "PICACOMIC     (picacomic.com)     — API HMAC + login"),
    ("pigmh", "PIGMH         (pigmh.com)         — AES-128-CBC + BS4"),
    ("toonkor", "TOONKOR       (tkorXXX.com)       — scrapling + base64"),
    ("wfwf", "WFWF          (wfwf448.com)       — dual-mode webtoon"),
    ("yumanhua", "YUMANHUA      (yumanhua.com)      — XOR + Scrapling"),
]


def _load_downloader(key: str):
    if key == "bookwalker":
        from d_bookwalker import DownloaderBookwalker

        return DownloaderBookwalker()
    if key == "bookwalkerhar":
        from d_bookwalker import DownloaderBookwalkerHar

        return DownloaderBookwalkerHar()
    if key == "18mh":
        from d_18mh import Downloader18mh

        return Downloader18mh()
    if key == "bakamh":
        from d_bakamh import DownloaderBakamh

        return DownloaderBakamh()
    if key == "baozimh":
        from d_baozimh import DownloaderBaozimh

        return DownloaderBaozimh()
    if key == "dumanwu":
        from d_dumanwu import DownloaderDumanwu

        return DownloaderDumanwu()
    if key == "hitomi":
        from d_hitomi import DownloaderHitomi

        return DownloaderHitomi()
    if key == "mangafox":
        from d_mangafox import DownloaderMangafox

        return DownloaderMangafox()
    if key == "manhuagui":
        from d_manhuagui import DownloaderManhuagui

        return DownloaderManhuagui()
    if key == "picacomic":
        from d_picacomic import DownloaderPicacomic

        return DownloaderPicacomic()
    if key == "pigmh":
        from d_pigmh import DownloaderPigmh

        return DownloaderPigmh()
    if key == "toonkor":
        from d_toonkor import DownloaderToonkor

        return DownloaderToonkor()
    if key == "wfwf":
        from d_wfwf import DownloaderWfwf

        return DownloaderWfwf()
    if key == "yumanhua":
        from d_yumanhua import DownloaderYumanhua

        return DownloaderYumanhua()
    raise ValueError(f"Downloader desconocido: {key}")


# ══════════════════════════════════════════════════════════════
#  HELPERS DE UI
# ══════════════════════════════════════════════════════════════


def _header(subtitle: str = "") -> None:
    os.system("cls" if os.name == "nt" else "clear")
    print(f"{C.BLUE}╔══════════════════════════════════════════════════╗")
    print(f"║  {C.BOLD}MANGA DOWNLOADER — MENÚ UNIFICADO{C.END}{C.BLUE}              ║")
    if subtitle:
        sub = subtitle[:46]
        print(f"║  {C.CYAN}{sub:<46}{C.END}{C.BLUE}  ║")
    print(f"╚══════════════════════════════════════════════════╝{C.END}")
    cfg = common.CFG
    print(
        f"  {C.DIM}salida={cfg['output_type'].upper()}"
        f"  imagen={cfg['user_format'].upper()}"
        f"  workers={cfg['max_workers']}" + (" [debug]" if DEBUG else "") + C.END
    )
    print()


def _prompt(msg: str = "") -> str:
    """Input limpio. Evita acumulación de enters."""
    try:
        return input(f"  {C.YELLOW}{msg}➜ {C.END}").strip()
    except EOFError:
        return ""


def _paginated_list(
    items: list[dict],
    label: str,
    page_size: int = 20,
    load_more_fn: Optional[Callable[[], tuple[list, bool]]] = None,
) -> Optional[dict]:
    """
    Muestra items paginados (cliente). Devuelve ítem seleccionado o None.
    load_more_fn: () → (nuevos_items, hay_más)  — carga lazy desde el servidor.
    """
    page = 0
    paginate = True
    can_more = load_more_fn is not None

    while True:
        _header(label)
        if paginate:
            start = page * page_size
            end = min(start + page_size, len(items))
            chunk = items[start:end]
        else:
            start, end = 0, len(items)
            chunk = items

        extra = f"  {C.DIM}(+más disponibles){C.END}" if can_more else ""
        print(f"  {C.PURPLE}'{label}'  {start + 1}–{end} / {len(items)}{extra}{C.END}")
        print(f"  {'━' * 54}")
        for i, it in enumerate(chunk):
            num = start + i + 1
            print(f"  {C.BOLD}{num:4d}.{C.END}  {it.get('title', '')[:52]}")
        print(f"  {'━' * 54}")

        nav: list[str] = []
        at_last_client = paginate and end >= len(items)
        if paginate and end < len(items):
            nav.append(f"{C.CYAN}n{C.END}=sig")
        elif at_last_client and can_more:
            nav.append(f"{C.CYAN}n{C.END}=más")
        if paginate and page > 0:
            nav.append(f"{C.CYAN}p{C.END}=ant")
        nav += [
            f"{C.CYAN}t{C.END}=toggle",
            f"{C.CYAN}q{C.END}=volver",
            "número para seleccionar",
        ]
        print("  " + "  ".join(nav))

        sel = _prompt()

        if sel == "q":
            return None

        if sel == "n":
            if paginate and end < len(items):
                page += 1
            elif at_last_client and can_more:
                print(f"  {C.DIM}Cargando…{C.END}")
                new_items, still_more = load_more_fn()
                if new_items:
                    items.extend(new_items)
                    page += 1
                if not still_more:
                    can_more = False
            continue

        if sel == "p" and paginate and page > 0:
            page -= 1
            continue

        if sel == "t":
            paginate = not paginate
            page = 0
            continue

        if sel.isdigit():
            # Números mostrados son siempre absolutos (1-based)
            idx = int(sel) - 1
            if 0 <= idx < len(items):
                return items[idx]
            continue


def _chapter_selector(chapters: list[dict], series_title: str) -> list[dict]:
    PAGE = 20
    off = 0
    while True:
        end_idx = min(off + PAGE, len(chapters))
        print(f"\n  {C.PURPLE}{'─' * 58}{C.END}")
        for i in range(off, end_idx):
            print(f"  {C.BOLD}{i + 1:4d}.{C.END}  {chapters[i].get('title', '')[:55]}")
        print(f"  {C.PURPLE}{'─' * 58}{C.END}")
        nav = ""
        if end_idx < len(chapters):
            nav += f"  {C.CYAN}n{C.END}=sig  "
        if off > 0:
            nav += f"  {C.CYAN}p{C.END}=ant"
        if nav:
            print(nav)
        raw = _prompt("Caps ('1', '3-5,9', 'all', q=volver) ")
        if raw.lower() == "n" and end_idx < len(chapters):
            off += PAGE
        elif raw.lower() == "p" and off > 0:
            off -= PAGE
        elif raw.lower() == "q":
            return []
        elif raw == "":
            continue
        elif raw.lower() == "all":
            return list(chapters)
        else:
            idxs = parse_positions(raw, len(chapters))
            if idxs:
                return [chapters[i] for i in idxs]


def _estimate_total_images(dl, series: dict, selected: list[dict]) -> Optional[int]:
    """Estimación del número total de imágenes de los capítulos seleccionados
    (rasca la página/cantidad de cada capítulo sin descargar imágenes)."""
    total = 0
    unknown = 0
    for c in selected:
        try:
            n = dl.get_image_count(c, series)
            if n is not None:
                total += n
            else:
                unknown += 1
        except Exception:
            unknown += 1
    return total if unknown == 0 else None


def _confirm_download(selected: list[dict], dl=None, series: Optional[dict] = None) -> bool:
    print(f"\n  {C.BOLD}Capítulos seleccionados ({len(selected)}):{C.END}")
    for i, c in enumerate(selected[:10], 1):
        print(f"    {i}. {c.get('title', '')[:60]}")
    if len(selected) > 10:
        print(f"    … y {len(selected) - 10} más")
    if dl is not None and series:
        est = _estimate_total_images(dl, series, selected)
        if est is not None:
            print(f"  {C.CYAN}Total estimado: {C.BOLD}{est}{C.END}{C.CYAN} imágenes en {len(selected)} capítulos{C.END}")
        else:
            print(f"  {C.YELLOW}No se pudo estimar el número total de imágenes.{C.END}")
    ans = _prompt("¿Confirmar descarga? [Enter=sí / n=cancelar] ")
    return ans.lower() != "n"


# ══════════════════════════════════════════════════════════════
#  FLUJOS DE DESCARGA
# ══════════════════════════════════════════════════════════════


def _flow_series_and_download(dl, item: dict) -> None:
    print(f"\n  {C.CYAN}Cargando ficha…{C.END}")
    series, chapters = dl.get_series(item)
    if not series:
        print(f"  {C.RED}No se pudo cargar la serie.{C.END}")
        _prompt("Enter para continuar ")
        return
    print(f"\n  {C.GREEN}{C.BOLD}{series.get('title', '')}{C.END}")
    for key in ("autor", "author", "status", "estado"):
        val = series.get(key, "")
        if val:
            print(f"  {key.capitalize()}: {val}")
    genres = series.get("genres", series.get("categories", []))
    if genres:
        print(f"  Géneros: {', '.join(genres[:6])}")
    summ = series.get("summary", series.get("desc", ""))
    if summ:
        s = str(summ)
        print(f"  Sinopsis: {(s[:100] + '…') if len(s) > 100 else s}")
    print(f"  {C.GREEN}{len(chapters)} capítulos{C.END}")

    if not chapters:
        print(f"  {C.YELLOW}Sin capítulos disponibles.{C.END}")
        _prompt("Enter para continuar ")
        return

    selected = _chapter_selector(chapters, series.get("title", ""))
    if not selected:
        return
    if not _confirm_download(selected, dl, series):
        return

    run_download(
        dl,
        series,
        selected,
        output_type=common.CFG["output_type"],
        user_format=common.CFG["user_format"],
        max_workers=common.CFG["max_workers"],
        delete_temp=common.CFG["delete_temp"],
    )


def _flow_search(dl) -> None:
    """
    Búsqueda estándar.
    Para Hitomi: acepta ID numérico puro, o tags separados por espacio
    (ej: language:spanish female:mind_control).
    """
    hint = ""
    if "hitomi" in dl.NAME.lower():
        hint = "ID o tags (language:X female:Y …) "
    query = _prompt(f"Búsqueda {hint}")
    if not query:
        return
    print(f"  {C.DIM}Buscando…{C.END}")
    results = dl.search(query)
    if not results:
        print(f"  {C.RED}Sin resultados.{C.END}")
        _prompt("Enter para continuar ")
        return
    item = _paginated_list(results, f"Búsqueda: {query}")
    if item:
        _flow_series_and_download(dl, item)


def _flow_catalog(dl) -> None:
    """Catálogo con carga lazy (página a página) o filtro completo en memoria."""
    filters: dict = {}

    # ── filtros por downloader ────────────────────────────────────────────────
    if hasattr(dl, "languages"):  # Hitomi — elegir idioma
        _header("Hitomi — Catálogo")
        langs = dl.languages
        keys = list(langs.keys())
        for i, (k, v) in enumerate(langs.items()):
            print(f"  {C.BOLD}{i + 1:>2}.{C.END} {v}  {C.DIM}({k}){C.END}")
        sel = _prompt("Idioma (Enter=Todos) ")
        lang = "all"
        if sel.isdigit():
            idx2 = int(sel) - 1
            if 0 <= idx2 < len(keys):
                lang = keys[idx2]
        filters["language"] = lang
    elif hasattr(dl, "_sess_org"):  # Baozimh
        _header("Baozimh — Catálogo")
        t = input(f"  {C.CYAN}Género (Enter=todo) ➜ {C.END}").strip()
        r = input(f"  {C.CYAN}Región (Enter=todo) ➜ {C.END}").strip()
        s = input(f"  {C.CYAN}Estado (Enter=todo) ➜ {C.END}").strip()
        if t:
            filters["type_"] = t
        if r:
            filters["region"] = r
        if s:
            filters["state"] = s
    elif "manhuagui" in dl.NAME.lower():  # Manhuagui
        _header("Manhuagui — Catálogo")
        print(f"  {C.DIM}Filtros opcionales (Enter = todo){C.END}")
        for fname in ("region", "genre", "audience", "status"):
            v = input(f"  {fname.capitalize()} ➜ ").strip()
            if v:
                filters[fname] = v

    # ── filtro en memoria vs lazy ─────────────────────────────────────────────
    ft = input(f"\n  {C.CYAN}Filtrar por nombre (Enter=lazy): {C.END}").strip().lower()

    if ft:
        print(f"  {C.DIM}Cargando catálogo completo…{C.END}")
        all_items = dl.get_catalog(**filters)
        # Hitomi: pre-load metadata for all items so titles are available
        if hasattr(dl, "preload_batch"):
            from d_hitomi import load_meta_batch as _lmb

            try:
                _lmb(
                    dl._sess,
                    [int(it["id"]) for it in all_items if it.get("id", "").isdigit()],
                )
            except Exception:
                pass
            # Refresh titles after metadata load
            from d_hitomi import gallery_title as _gt

            for it in all_items:
                if it.get("id", "").isdigit():
                    it["title"] = _gt(int(it["id"]))
        items = [it for it in all_items if ft in it.get("title", "").lower()]
        if not items:
            print(f"  {C.RED}Sin resultados para '{ft}'.{C.END}")
            _prompt("Enter para continuar ")
            return
        item = _paginated_list(items, f"Catálogo: '{ft}'")
    else:
        print(f"  {C.DIM}Cargando primera página…{C.END}")
        first_items, has_more = dl.get_catalog_page(page=1, **filters)
        if not first_items:
            print(f"  {C.RED}Catálogo vacío.{C.END}")
            _prompt("Enter para continuar ")
            return

        _page = [1]
        _more = [has_more]

        def load_more() -> tuple[list, bool]:
            _page[0] += 1
            new_items, still_more = dl.get_catalog_page(page=_page[0], **filters)
            _more[0] = still_more
            return new_items, still_more

        item = _paginated_list(
            list(first_items),
            "Catálogo",
            load_more_fn=(load_more if has_more else None),
        )

    if item:
        _flow_series_and_download(dl, item)


def _flow_picacomic_login(dl) -> bool:
    _header("PicaComic — Login")
    print(f"  {C.DIM}Ingresá tus credenciales o un token JWT.{C.END}\n")
    print(f"  {C.BOLD}1.{C.END}  Usuario + contraseña")
    print(f"  {C.BOLD}2.{C.END}  Token JWT directo")
    print(f"  {C.BOLD}3.{C.END}  Cancelar\n")
    op = _prompt()
    if op == "1":
        email = input(f"  {C.CYAN}Email/usuario ➜ {C.END}").strip()
        password = getpass(f"  {C.CYAN}Contraseña   ➜ {C.END}")
        print(f"  {C.DIM}Iniciando sesión…{C.END}")
        ok = dl.login(email=email, password=password)
        print(
            f"  {C.GREEN}✔  Login exitoso.{C.END}"
            if ok
            else f"  {C.RED}✗  Credenciales inválidas.{C.END}"
        )
        return ok
    elif op == "2":
        token = input(f"  {C.CYAN}Token JWT ➜ {C.END}").strip()
        return dl.login(token=token)
    return False


def _flow_bookwalkerhar_import(dl) -> bool:
    """Pega cURL del /c (FLUJO A) o de una imagen 'pages' (FLUJO B), o una ruta .har."""
    _header("BOOKWALKER-HAR — Importar tomo")
    print(
        f"  {C.DIM}Las 167 imágenes SOLO se bajan con la sesión del navegador que{C.END}"
    )
    print(
        f"  {C.DIM}abre el tomo (cuenta del dueño). Elegí una vía:{C.END}\n"
    )
    print(f"  {C.BOLD}1.{C.END}  Pegar el cURL de la petición  browserWebApi/c  (FLUJO A /c)")
    print(
        f"      F12 → Network → F5 → clic en 'c?cid=…' → Copy as cURL"
        f"\n      (descifra el config y calcula los 167 tokens solo)"
    )
    print(f"  {C.BOLD}2.{C.END}  Pegar el cURL de CUALQUIER imagen del HAR (FLUJO B pages)")
    print(f"      Ej: …/OEBPS/text/p-001.xhtml/{C.DIM}{C.CYAN}(token){C.END}.jpeg")
    print(f"  {C.BOLD}3.{C.END}  Ruta a un archivo .har para extraer los 167 tokens\n")
    op = _prompt()
    try:
        import d_bookwalker as _h
    except Exception:
        _h = None
    if not _h:
        print(f"  {C.RED}✗  No se pudo cargar d_bookwalker.{C.END}")
        _prompt("Enter…")
        return False

    item = None
    if op == "1":
        raw = input(f"  {C.CYAN}cURL del /c ➜ {C.END}").strip()
        if raw:
            res = _h.DownloaderBookwalkerHar().search(raw)
            item = res[0] if res else None
    elif op == "2":
        raw = input(f"  {C.CYAN}cURL de 'pages' ➜ {C.END}").strip()
        if raw:
            res = _h.DownloaderBookwalkerHar().search(raw)
            item = res[0] if res else None
    elif op == "3":
        raw = input(f"  {C.CYAN}Ruta del .har ➜ {C.END}").strip()
        if raw:
            res = _h.DownloaderBookwalkerHar().search(raw)
            item = res[0] if res else None

    if not item:
        print(f"  {C.RED}✗  No se pudo reconocer la entrada.{C.END}")
        _prompt("Enter…")
        return False
    print(f"  {C.GREEN}✔  Captura guardada: {C.CYAN}{item['title'][:40]}{C.END}")
    _prompt("Enter…")
    return True


def _flow_bookwalkerhar_list(dl) -> bool:
    try:
        import d_bookwalker as _h

        caps = _h.load_har_captures()
    except Exception:
        caps = {}
    if not caps:
        print(f"  {C.YELLOW}Sin capturas guardadas.{C.END}")
        print(
            f"  {C.DIM}Usá la opción 7 para abrir el navegador, o importá un cURL/HAR.{C.END}"
        )
    else:
        print(f"  {C.GREEN}{len(caps)} captura(s) guardada(s):{C.END}")
        for cid, cap in caps.items():
            tok = len(cap.get("tokens") or {})
            sess = cap.get("session")
            state = "listo" if sess else "sin sesión"
            print(f"    • {cid[:20]}…  {tok} págs  [{state}]")
    _prompt("Enter…")
    return True


def _flow_bookwalkerhar_clear(dl) -> bool:
    try:
        import d_bookwalker as _b

        _b.clear_har_captures()
        print(f"  {C.GREEN}✔  Capturas HAR eliminadas.{C.END}")
    except Exception:
        print(f"  {C.RED}✗  No se pudieron limpiar.{C.END}")
    _prompt("Enter…")
    return True


def _flow_bookwalkerhar_session(dl) -> bool:
    """Sesión automática: toma la sesión del navegador REAL del usuario."""
    _header("BOOKWALKER-HAR — Sesión automática")
    try:
        import bw_session as _s
    except Exception:
        _s = None
    if not _s or not _s.importable():
        print(
            f"  {C.RED}✗  Falta Playwright. Instalalo con:{C.END}\n"
            f"     pip install playwright\n"
            f"     python -m playwright install chromium"
        )
        _prompt("Enter…")
        return False
    nombre, en_uso = _s.real_profile_status()
    if not en_uso:
        print(
            f"  {C.DIM}Se abrirá tu {nombre} (donde ya está tu sesión).{C.END}\n"
            f"  {C.DIM}Si está abierto, cerrarlo una vez: aparece un falso{C.END}\n"
            f"  {C.DIM}advertencia y la SESSION queda guardada para siempre.{C.END}"
        )
    else:
        print(
            f"  {C.RED}Tu {nombre} está abierto y no se puede abrir dos veces.{C.END}\n"
            f"  {C.DIM}Cerrá {nombre} y volvé a elegir esta opción.{C.END}"
        )
    try:
        ok = bool(dl.login_via_browser())
    except Exception:
        ok = False
    if ok:
        print(
            f"  {C.GREEN}✔  SESSION tomada de tu {nombre}.{C.END}"
            f"\n  {C.DIM} Las firmas/tokens se renuevan solos con requests;"
            f"\n  {C.DIM} los tomos ya se capturan sin login.{C.END}"
        )
    else:
        print(f"  {C.RED}✗  No apareció la sesión en {C.CYAN}600s{C.END}. ¿La cuenta está logueada?")
    _prompt("Enter…")
    return ok


def _flow_bookwalker_cookies(dl) -> bool:
    """Pega cookies de sesión de bookwalker.jp (tomos comprados)."""
    _header("BOOKWALKER — Cookies de cuenta")
    print(
        f"  {C.DIM}Para descargar tomos COMPRADOS se necesitan las cookies de la{C.END}"
    )
    print(
        f"  {C.DIM}sesión logueada en member.bookwalker.jp (Google-linked no{C.END}"
    )
    print(f"  {C.DIM}expone credenciales; la cuenta solo usa sesión).{C.END}\n")
    print(f"  {C.DIM}Formato:  {C.CYAN}key=value; key2=value2{C.END}")
    print(
        f"  {C.DIM}Desde el navegador logueado: DevTools → Application → Cookies{C.END}\n"
    )
    raw = input(f"  {C.CYAN}Cookies ➜ {C.END}").strip()
    if not raw:
        return False
    print(f"  {C.DIM}Guardando cookies…{C.END}")
    ok = dl.login(cookies=raw)
    print(
        f"  {C.GREEN}✔  Cookies guardadas.{C.END}"
        if ok
        else f"  {C.RED}✗  No se pudieron guardar.{C.END}"
    )
    return ok


def _flow_bookwalker_capture(dl) -> bool:
    """Pega el cURL de la petición /c del visor (tomo comprado)."""
    _header("BOOKWALKER — Tomo comprado (captura del visor)")
    print(
        f"  {C.DIM}El visor member ata la sesión al navegador que la abrió y cada{C.END}"
    )
    print(
        f"  {C.DIM}petiticion /c es de un solo uso. Para tomos COMPRADOS pegá el{C.END}"
    )
    print(f"  {C.DIM}cURL de esa peticion desde el navegador del dueno de la cuenta:{C.END}\n")
    print(f"  1. Abrir  view-source:  https://viewer.bookwalker.jp/03/30/viewer.html?cid=…&cty=1")
    print(f"     (navegador logueado en member.bookwalker.jp)")
    print(f"  2. F12 → Network → recargar")
    print(f"  3. Clic en la peticion 'c?cid=…' → Copy → Copy as cURL\n")
    raw = input(f"  {C.CYAN}cURL ➜ {C.END}").strip()
    if not raw:
        return False
    try:
        import d_bookwalker as _b

        cid = _b.import_bookwalker_curl(raw)
    except Exception:
        cid = None
    if not cid:
        print(f"  {C.RED}✗  No se reconocio la peticion /c. Revisa que copies el cURL de browserWebApi/c.{C.END}")
        _prompt("Enter…")
        return False
    caps = _b.load_member_captures()
    cap = caps.get(cid) or {}
    print(f"  {C.GREEN}✔  Captura guardada para el tomo {C.CYAN}{cid[:8]}…{C.END}")
    print(f"  {C.DIM}    u1(SESSION): {str(cap.get('u1'))[:12]}…  BID: {str(cap.get('bid'))[:16]}…")
    print(f"  {C.DIM}    cookies: {len(str(cap.get('cookies')))} bytes{C.END}")
    _prompt("Enter…")
    return True


def _flow_bookwalker_clear(dl) -> bool:
    try:
        import d_bookwalker as _b

        _b.clear_member_captures()
        print(f"  {C.GREEN}✔  Capturas member eliminadas.{C.END}")
    except Exception:
        print(f"  {C.RED}✗  No se pudieron limpiar.{C.END}")
    _prompt("Enter…")
    return True


# ══════════════════════════════════════════════════════════════
#  MENÚ DE SITIO
# ══════════════════════════════════════════════════════════════


def _site_menu(dl) -> None:
    # Login requerido y aún no autenticado
    if dl.NEEDS_LOGIN and not getattr(dl, "_token", ""):
        # Check if credentials are configured
        try:
            import d_picacomic as _p

            has_creds = bool(
                _p.PICACOMIC_EMAIL or _p.PICACOMIC_PASSWORD or _p.PICACOMIC_TOKEN
            )
        except Exception:
            has_creds = False
        if not has_creds:
            print(
                f"  {C.YELLOW}Para login automático, definí en .env:{C.END}"
            )
            print("    PICACOMIC_EMAIL    = 'tu@email.com'")
            print("    PICACOMIC_PASSWORD = 'tucontraseña'")
            print(f"  {C.DIM}(o bien PICACOMIC_TOKEN con tu JWT){C.END}")
            print()
        if not _flow_picacomic_login(dl):
            _prompt("Sin login, volviendo… Enter ")
            return

    while True:
        _header(dl.NAME)
        opts: list[tuple[str, str]] = []
        if dl.HAS_SEARCH:
            opts.append(("1", "Buscar"))
        if dl.HAS_CATALOG:
            opts.append(("2", "Catálogo"))
        opts.append(("3", "Volver"))
        if getattr(dl, "HAR_SESSION", False):
            opts.append(("4", "Importar tomo (cURL /c, pages o .har)"))
            opts.append(("5", "Ver capturas guardadas"))
            opts.append(("6", "Limpiar capturas HAR"))
            opts.append(("7", "Sesión automática (abrir navegador, loguear 1 vez)"))
        elif getattr(dl, "NAME", "").startswith("BOOKWALKER"):
            ops = " (cuenta configurada)" if getattr(dl, "_logged", False) else ""
            opts.append(("4", "Cookies de cuenta (tomos comprados)" + ops))
            opts.append(("5", "Captura /c del visor (tomo comprado)"))
            opts.append(("6", "Limpiar capturas member"))

        for code, label in opts:
            print(f"  {C.BOLD}{code}.{C.END}  {label}")

        op = _prompt()

        if op == "3" or op == "q":
            break
        elif op == "1" and dl.HAS_SEARCH:
            _flow_search(dl)
        elif op == "2" and dl.HAS_CATALOG:
            _flow_catalog(dl)
        elif op == "4" and getattr(dl, "HAR_SESSION", False):
            _flow_bookwalkerhar_import(dl)
        elif op == "5" and getattr(dl, "HAR_SESSION", False):
            _flow_bookwalkerhar_list(dl)
        elif op == "6" and getattr(dl, "HAR_SESSION", False):
            _flow_bookwalkerhar_clear(dl)
        elif op == "7" and getattr(dl, "HAR_SESSION", False):
            _flow_bookwalkerhar_session(dl)
        elif op == "4" and getattr(dl, "NAME", "").startswith("BOOKWALKER"):
            _flow_bookwalker_cookies(dl)
        elif op == "5" and getattr(dl, "NAME", "").startswith("BOOKWALKER"):
            _flow_bookwalker_capture(dl)
        elif op == "6" and getattr(dl, "NAME", "").startswith("BOOKWALKER"):
            _flow_bookwalker_clear(dl)
        # Opción no reconocida → re-muestra menú directamente (sin enter extra)


# ══════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════


def _config_menu() -> None:
    _header("Configuración")
    cfg = common.CFG

    print(
        f"  {C.BOLD}Formato de salida actual:{C.END}  {C.CYAN}{cfg['output_type'].upper()}{C.END}"
    )
    print(f"    1. ZIP   2. CBZ   3. PDF")
    sel = _prompt()
    if sel == "1":
        cfg["output_type"] = "zip"
    elif sel == "2":
        cfg["output_type"] = "cbz"
    elif sel == "3":
        cfg["output_type"] = "pdf"

    print(
        f"\n  {C.BOLD}Formato de imagen actual:{C.END}  {C.CYAN}{cfg['user_format'].upper()}{C.END}"
    )
    print(f"    1. original   2. WEBP   3. JPG   4. PNG")
    sel = _prompt()
    if sel == "1":
        cfg["user_format"] = "original"
    elif sel == "2":
        cfg["user_format"] = "webp"
    elif sel == "3":
        cfg["user_format"] = "jpg"
    elif sel == "4":
        cfg["user_format"] = "png"

    raw = input(f"\n  {C.BOLD}Workers{C.END} (actual={cfg['max_workers']}) ➜ ").strip()
    if raw.isdigit() and int(raw) > 0:
        cfg["max_workers"] = int(raw)

    print(f"\n  {C.GREEN}✔  Config actualizada.{C.END}")
    _prompt("Enter para continuar ")


# ══════════════════════════════════════════════════════════════
#  MENÚ PRINCIPAL
# ══════════════════════════════════════════════════════════════


def main() -> None:
    while True:
        _header()
        print(f"  {C.PURPLE}{C.BOLD}Seleccioná un sitio:{C.END}\n")
        for i, (key, desc) in enumerate(DOWNLOADERS, 1):
            print(f"  {C.BOLD}{i:>2}.{C.END}  {desc}")
        print(f"\n  {C.BOLD} c.{C.END}  Configuración")
        print(f"  {C.BOLD} q.{C.END}  Salir")

        sel = _prompt()

        if sel == "q":
            print(f"\n  {C.GREEN}¡Hasta luego!{C.END}\n")
            break
        elif sel == "c":
            _config_menu()
            continue

        if not sel.isdigit():
            continue
        idx = int(sel) - 1
        if not (0 <= idx < len(DOWNLOADERS)):
            continue

        key, desc = DOWNLOADERS[idx]
        _header(f"Cargando {desc[:40]}…")
        print(f"  {C.DIM}Iniciando {key}…{C.END}")
        try:
            dl = _load_downloader(key)
        except ImportError as e:
            print(f"\n  {C.RED}Error de importación: {e}{C.END}")
            print(f"  {C.YELLOW}Instalá las dependencias requeridas para {key}.{C.END}")
            _prompt("Enter para continuar ")
            continue
        except Exception as e:
            print(f"\n  {C.RED}Error al iniciar {key}: {e}{C.END}")
            _prompt("Enter para continuar ")
            continue

        _site_menu(dl)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Interrumpido.{C.END}\n")
