# -*- coding: utf-8 -*-
"""Test de regresion (golden) para el de-scramble de BookWalker HAR.

Genera el zip descifrado DESDE la propia app integrada (metodo real
`DownloaderBookwalkerHar._unscramble_bytes`, sin red) y lo compara:
  - contra el zip descifrado standalone (byte a byte), y
  - contra un golden SHA-256 sembrado (para futuras corridas sin la
    referencia).

Uso:
    python test_bookwalker_har.py           # compara contra zip de referencia
    python test_bookwalker_har.py --golden  # (re)siembra bookwalker_har.golden.json
"""
import hashlib
import io
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from d_bookwalker_har import (DownloaderBookwalkerHar, load_har_captures,
                              _sort_key)
from d_bookwalker_unscramble import build_seeds_for_book, unscramble

DOCS = r"C:\Users\Administrator\Documents\babylon-app"
SRC_ZIP = r"C:\Users\Administrator\Desktop\bookwalker e41656de.zip"
REF_ZIP = r"C:\Users\Administrator\Desktop\bookwalker e41656de - descifrado.zip"
DLDIR = os.path.join(DOCS, "babylon_downloaders")
GOLDEN = os.path.join(HERE, "bookwalker_har.golden.json")

_PAGE_SEED_RE = re.compile(r"/((?:p-[a-z0-9_-]+)\.xhtml)/", re.I)


def build_dl() -> "DownloaderBookwalkerHar":
    caps = load_har_captures()
    cap = next(v for v in caps.values() if v.get("config"))
    cfg = cap["config"]
    k1, k2, k3 = cap["keys"]
    tokens = cap["tokens"]

    dl = DownloaderBookwalkerHar()
    dl._PAGE_SEED_RE = _PAGE_SEED_RE
    seeds = build_seeds_for_book(cfg, k1, k2, k3)
    dl._seeds_by_base = {fid.rsplit("/", 1)[-1].lower(): s
                         for fid, s in seeds.items()}
    dl._tokens = tokens
    dl._sort_key = _sort_key
    return dl


def main() -> int:
    dl = build_dl()
    gold = {}
    if os.path.exists(GOLDEN):
        gold = json.load(open(GOLDEN, encoding="utf-8"))

    z = zipfile.ZipFile(SRC_ZIP)
    names = sorted((n for n in z.namelist() if n.endswith(".jpg")),
                   key=lambda n: int(n.rsplit(".", 1)[0]))
    zr = zipfile.ZipFile(REF_ZIP) if os.path.exists(REF_ZIP) else None

    order = sorted(dl._tokens, key=_sort_key)
    total = ident = equal_ref = 0
    for i, name in enumerate(names, 1):
        fid = order[i - 1]
        base = fid.rsplit("/", 1)[-1].lower()
        url = f"https://img.bookwalker.jp/book/OEBPS/text/{base}/x.jpeg"
        raw = z.read(name)
        out = dl._unscramble_bytes(url, raw)
        h = hashlib.sha256(out).hexdigest()
        total += 1
        if gold.get(name) == h:
            ident += 1
        else:
            gold[name] = h
        if zr and zr.read(name) == out:
            equal_ref += 1

    if sys.argv[-1] == "--golden":
        json.dump({n: gold.get(n) for n in sorted(gold)},
                  open(GOLDEN, "w", encoding="utf-8"), indent=2)
        print(f"[golden] sembrado {total} hashes")

    ok_gr = "--golden" in sys.argv  # tras siembra no hay previo a comparar
    if not ok_gr:
        ok_gr = ident == total
    ref_note = "" if zr is None else f" | byte-ref {equal_ref}/{total}"
    print(f"167 paginas | golden-match {ident}/{total}{ref_note}")
    print("RESULTADO:", "OK (app == standalone)" if (ok_gr and (zr is None
          or equal_ref == total)) else "FALLO")
    return 0 if (ok_gr and (zr is None or equal_ref == total)) else 1


if __name__ == "__main__":
    sys.exit(main())
