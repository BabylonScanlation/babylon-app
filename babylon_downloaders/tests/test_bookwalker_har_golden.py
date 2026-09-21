# -*- coding: utf-8 -*-
"""Regresión dorada (golden) de la integración BookWalker HAR.

Re-ejecuta el flujo REAL de la integración (`DownloaderBookwalkerHar`
desde `d_bookwalker`, método `_unscramble_bytes`) sobre las 167 páginas
de la captura HAR local — SIN red, SIN zip de referencia, usando solo las
capturas persistidas y las seeds construidas por el propio módulo — y compara
el SHA-256 de cada página de-scrambleada contra un dorado sembrado.

   python tests/test_bookwalker_har_golden.py            # valida (exit 0 = OK)
   python tests/test_bookwalker_har_golden.py --sembrar  # sembrar/re-sembrar

Si algún día se rompe la integración, este test lo detecta listando las
páginas que difieren del dorado.
"""
import hashlib
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))  # padre = babylon_downloaders

from d_bookwalker import (DownloaderBookwalkerHar, load_har_captures,
                          _sort_key, build_seeds_for_book, unscramble)

GOLDEN = os.path.join(HERE, "bookwalker_har.golden.json")

SRC_ZIP = r"C:\Users\Administrator\Desktop\bookwalker e41656de.zip"

_PAGE_SEED_RE = re.compile(r"((?:p-[a-z0-9_-]+)\.xhtml)", re.I)


def _hashes() -> dict:
    caps = load_har_captures()
    cap = next(v for v in caps.values() if v.get("config") and v.get("keys"))
    cfg, ks = cap["config"], cap["keys"]
    tokens = cap["tokens"]
    order = sorted(tokens, key=_sort_key)

    seeds = build_seeds_for_book(cfg, ks[0], ks[1], ks[2])
    dl = DownloaderBookwalkerHar()
    dl._PAGE_SEED_RE = _PAGE_SEED_RE
    dl._seeds_by_base = {fid.rsplit("/", 1)[-1].lower(): s
                         for fid, s in seeds.items()}

    z = zipfile.ZipFile(SRC_ZIP)
    names = sorted((n for n in z.namelist() if n.lower().endswith(".jpg")),
                   key=lambda n: int(n.rsplit(".", 1)[0]))

    assert len(names) == len(order), (len(names), len(order))

    hashes = {}
    for i, name in enumerate(names, 1):
        fid = order[i - 1]
        base = fid.rsplit("/", 1)[-1].lower()
        url = f"https://img.bookwalker.jp/book/OEBPS/text/{base}/{i}.jpeg"
        out = dl._unscramble_bytes(url, z.read(name))
        hashes[name] = hashlib.sha256(out).hexdigest()
    return hashes


def main() -> int:
    fresh = _hashes()

    if "--sembrar" in sys.argv:
        with open(GOLDEN, "w", encoding="utf-8") as f:
            json.dump(fresh, f, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"dorado sembrado: {len(fresh)} páginas -> {GOLDEN}")
        return 0

    if not os.path.exists(GOLDEN):
        print("No hay dorado; usa --sembrar primero.")
        return 2

    golden = json.load(open(GOLDEN, encoding="utf-8"))
    ok = sum(1 for k, v in fresh.items() if golden.get(k) == v)
    difs = sorted(k for k in fresh if golden.get(k) != fresh[k])
    for k in difs[:12]:
        print("   difiere:", k)
    status = "OK - app == dorado (sin regresion)" if ok == len(fresh) else \
        f"REGRESION ({len(fresh) - ok} paginas difieren)"
    print(f"verificadas {len(fresh)} | dorado-identicas {ok}/{len(fresh)}")
    print("RESULTADO:", status)
    return 0 if ok == len(fresh) else 1


if __name__ == "__main__":
    sys.exit(main())
