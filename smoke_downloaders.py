"""Smoke test: search + catalog + series + chapter images + 1 image download por downloader."""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "babylon_downloaders"))

QUERIES = {
    "bookwalker": "one piece",
    "18mh": "proxy",
    "bakamh": "solo",
    "baozimh": "tower",
    "dumanwu": "level",
    "hitomi": "girl",
    "mangafox": "naruto",
    "manhuagui": "one",
    "picacomic": "solo",
    "pigmh": "tower",
    "toonkor": "one",
    "wfwf": "solo",
    "yumanhua": "level",
}


def load(key: str):
    if key == "bookwalker":
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
    raise ValueError(key)


def pick_item(results: list) -> dict | None:
    for r in results:
        if isinstance(r, dict) and (r.get("slug") or r.get("id") or r.get("url") or r.get("title")):
            return r
    return None


def test_one(key: str) -> dict:
    out = {"key": key, "load": False, "search": "skip", "catalog": "skip", "series": "skip", "images": "skip", "dl": "skip", "err": ""}
    t0 = time.time()
    try:
        dl = load(key)
        out["load"] = True
    except Exception as e:
        out["err"] = f"load: {e}"
        return out

    q = QUERIES.get(key, "a")

    # SEARCH
    try:
        if getattr(dl, "HAS_SEARCH", True):
            res = dl.search(q) or []
            out["search"] = f"ok({len(res)})"
            item = pick_item(res)
        else:
            item = None
            out["search"] = "n/a"
    except Exception as e:
        out["search"] = f"FAIL: {type(e).__name__}: {e}"
        item = None

    # CATALOG
    try:
        if getattr(dl, "HAS_CATALOG", True):
            items, more = dl.get_catalog_page(1, 5)
            out["catalog"] = f"ok({len(items)},more={more})"
            if item is None:
                item = pick_item(items or [])
        else:
            out["catalog"] = "n/a"
    except Exception as e:
        out["catalog"] = f"FAIL: {type(e).__name__}: {e}"

    # If search failed to give item, try catalog item already; or search again fallback empty
    if item is None:
        out["err"] = out["err"] or "no item for series test"
        out["elapsed"] = round(time.time() - t0, 1)
        return out

    # SERIES
    try:
        series, chapters = dl.get_series(item)
        nch = len(chapters or [])
        out["series"] = f"ok(ch={nch})"
        if not chapters:
            out["err"] = "series returned 0 chapters"
            out["elapsed"] = round(time.time() - t0, 1)
            return out
        chapter = chapters[0]
    except Exception as e:
        out["series"] = f"FAIL: {type(e).__name__}: {e}"
        out["elapsed"] = round(time.time() - t0, 1)
        return out

    # IMAGES
    try:
        urls = dl.get_chapter_images(chapter, series) or []
        out["images"] = f"ok({len(urls)})"
        if not urls:
            out["err"] = "0 image urls"
            out["elapsed"] = round(time.time() - t0, 1)
            return out
    except Exception as e:
        out["images"] = f"FAIL: {type(e).__name__}: {e}"
        out["elapsed"] = round(time.time() - t0, 1)
        return out

    # DL 1 image
    try:
        referer = ""
        try:
            referer = dl.get_referer(chapter, series) or ""
        except Exception:
            pass
        data = dl.dl_image(urls[0], referer)
        if data and len(data) > 100:
            out["dl"] = f"ok({len(data)}B)"
        else:
            out["dl"] = f"FAIL empty/small ({len(data) if data else 0}B)"
    except Exception as e:
        out["dl"] = f"FAIL: {type(e).__name__}: {e}"

    out["elapsed"] = round(time.time() - t0, 1)
    return out


def main():
    keys = sys.argv[1:] or [
        "bookwalker",
        "18mh",
        "bakamh",
        "baozimh",
        "dumanwu",
        "hitomi",
        "mangafox",
        "manhuagui",
        "picacomic",
        "pigmh",
        "toonkor",
        "wfwf",
        "yumanhua",
    ]
    results = []
    for k in keys:
        print(f"\n=== {k} ===", flush=True)
        try:
            r = test_one(k)
        except Exception as e:
            r = {"key": k, "err": f"outer: {e}", "search": "?", "catalog": "?", "series": "?", "images": "?", "dl": "?"}
            traceback.print_exc()
        results.append(r)
        print(
            f"  load={r.get('load')} search={r.get('search')} catalog={r.get('catalog')} "
            f"series={r.get('series')} images={r.get('images')} dl={r.get('dl')} "
            f"[{r.get('elapsed','?')}s] {r.get('err','')}",
            flush=True,
        )

    print("\n========== SUMMARY ==========")
    ok = 0
    for r in results:
        status = "OK " if r.get("dl", "").startswith("ok") else "BAD"
        if status == "OK ":
            ok += 1
        print(
            f"{status} {r['key']:12} search={r.get('search')} catalog={r.get('catalog')} "
            f"series={r.get('series')} images={r.get('images')} dl={r.get('dl')} {r.get('err','')}"
        )
    print(f"\n{ok}/{len(results)} full OK")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
