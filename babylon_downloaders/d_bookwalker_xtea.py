"""
d_bookwalker_xtea.py — descifrado del configuration_pack.json de BOOKWALKER
y cálculo del token de imagen `{TOKEN}.jpeg`.

Port a Python del algoritmo del visor (viewer.bookwalker.jp), basado en el
userscript open-source 'bookwalker-native-downloader' (GolyBidoof, MIT), que
a su vez porta byte-a-byte el JS minificado del visor (funciones A8j/A3b/
B0p/A7L/A6I/A2F/B0L/tB0l/b8gNo conservando los nombres originales).

  - El configuration_pack.json "member" es un sobre cifrado:
        { "version":"1.0", "data":"<custom-base64>" }
    Los primeros 128 chars del `data` son 3 claves de 32 bytes (k1,k2,k3); el
    resto es el payload. Pipeline fijo de descifrado:
        A8j (custom base64) -> A3b(0) -> B0p -> A7L -> A6I -> A2F -> B0L
        -> A3b(1) -> A3b(2) -> A3b(3) -> tB0l   ->  UTF-8 JSON
  - El token de imagen se DERIVA (no es aleatorio): por cada archivo
        p-XXX.xhtml  ->  b8gNo(fid, k1, k2, k3, no)   (no: índice de página
    dentro de PageLinkInfoList, 0 para manga de 1 página/archivo). El token
    resultante son 18 hex (p.ej. "10" + 16 hex). URL final:
        {base}/OEBPS/text/{fid}/{TOKEN}.jpeg

Con esto ya no hace falta extraer los 167 tokens del HAR: basta con bajar y
descifrar el configuration_pack.json (usa la misma auth_info que las páginas).
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

# ── tabla base64 custom del visor (idéntica al JS) ──────────────────────────

_ARR1 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_v4 = [0] * 256
_v5 = [0] * 256
_v6 = [0] * 256
_v7 = [0] * 256
_v8 = [0] * 256
_v9 = [0] * 256
_vak = [False] * 256
for _i, _c in enumerate(_ARR1):
    _ch = ord(_c)
    _v4[_ch] = _i
    _v5[_ch] = _i << 2
    _v6[_ch] = (_i << 4) & 255
    _v7[_ch] = (_i << 6) & 255
    _v8[_ch] = _i >> 2
    _v9[_ch] = _i >> 4
    _vak[_ch] = True
_A8F = (_v4, _v5, _v6, _v7, _v8, _v9, _vak)


def _swap(arr: List[int], a: int, b: int) -> None:
    t = arr[a]
    arr[a] = arr[b]
    arr[b] = t


def _b64_decode(content: str, data_offset: int, data_end_offset: int):
    """A8j: decode custom base64 -> (payload, k1, k2, k3 de 32 bytes)."""
    payload_offset = data_offset + 128
    key_part = content[data_offset:payload_offset]
    k1: List[int] = []
    k2: List[int] = []
    k3: List[int] = []
    active = k1
    for ci in range(0, 128, 4):
        a, b, c, d = (ord(key_part[ci + k]) for k in range(4))
        if not (_A8F[6][a] and _A8F[6][b] and _A8F[6][c] and _A8F[6][d]):
            raise ValueError("A8j char failure")
        active += [_A8F[1][a] | _A8F[5][b]]
        if ci + 4 == 88:
            active = k3
        active += [_A8F[2][b] | _A8F[4][c]]
        if ci + 4 == 44:
            active = k2
        active += [_A8F[3][c] | _A8F[0][d]]
    payload = content[payload_offset:data_end_offset]
    plen = len(payload)
    result_length = (plen * 3) >> 2
    if ord(content[data_end_offset - 2]) == 61:
        result_length -= 2
    elif ord(content[data_end_offset - 1]) == 61:
        result_length -= 1
    result = bytearray(result_length)
    idx = 0
    off = 0
    while off < plen - 4:
        c1, c2, c3, c4 = (ord(payload[off + k]) for k in range(4))
        if not (_A8F[6][c1] and _A8F[6][c2] and _A8F[6][c3] and _A8F[6][c4]):
            raise ValueError("A8j char failure")
        result[idx] = _A8F[1][c1] | _A8F[5][c2]
        idx += 1
        result[idx] = _A8F[2][c2] | _A8F[4][c3]
        idx += 1
        result[idx] = _A8F[3][c3] | _A8F[0][c4]
        idx += 1
        off += 4
    u = ord(payload[off])
    v = ord(payload[off + 1])
    w = ord(payload[off + 2])
    x = ord(payload[off + 3])
    if not (_A8F[6][u] and _A8F[6][v]):
        raise ValueError("A8j tail parsing error")
    result[idx] = _A8F[1][u] | _A8F[5][v]
    idx += 1
    if _A8F[6][w]:
        result[idx] = _A8F[2][v] | _A8F[4][w]
        idx += 1
        if _A8F[6][x]:
            result[idx] = _A8F[3][w] | _A8F[0][x]
        elif x != 61:
            raise ValueError("A8j tail alignment error")
    elif w != 61 or x != 61:
        raise ValueError("A8j tail padding error")
    return result, k1, k2, k3


def _ksa(inp: List[int]) -> List[int]:
    """a0F: key schedule algorithm (RC4 variant)."""
    arr = list(range(256))
    n = len(inp) or 1
    c = 0
    for i in range(256):
        c = (c + arr[i] + inp[i % n]) & 255
        _swap(arr, i, c)
    return arr


def _prga_xor(key: List[int], box: List[int]) -> List[int]:
    """a0g: PRGA XOR stream."""
    out: List[int] = []
    g = _ksa(box)
    c = 0
    d = 0
    for item in key:
        c = (c + 1) % 256
        d = (d + g[c]) % 256
        _swap(g, c, d)
        out.append(item ^ g[(g[c] + g[d]) % 256])
    return out


def _v_qmi(p1: List[int], p2: List[int], p3: List[int]) -> List[int]:
    return _ksa(p1 + p2 + p3)


def _v_smi(content: List[int], p1: List[int], p2: List[int], p3: List[int]) -> List[int]:
    return _prga_xor(content, p1 + p2 + p3)


def _step(v7: int, v8: int, i: int, key: List[int], content: bytearray) -> Tuple[int, int]:
    v7 = (v7 + 1) % 256
    v8 = (v8 + key[v7]) % 256
    _swap(key, v7, v8)
    content[i] ^= key[(key[v7] + key[v8]) % 256]
    return v7, v8


def _process_content_step(st, key: List[int], i0: int):
    content, clen, k1, k2, k3 = st
    v7 = 0
    v8 = 0
    i = i0
    while i >= 0:
        v7, v8 = _step(v7, v8, i, key, content)
        i -= 2
    return content, clen, k1, k2, k3


def _check1(n: int, m: int) -> bool:
    return (n & m) == m


def _process1(v0: int, v1: int, key: List[int]) -> Tuple[int, int]:
    for i in range(32):
        v0 = (v0 + key[i]) & 255
        v1 ^= key[i]
    return v0, v1


def _process2(y: int, u: int, g: List[int]) -> None:
    v = y
    while u > y:
        _swap(g, u, v)
        u -= 1
        v -= 1


def _a3b(of: int, st):
    """A3b: permutación byte-wise con seed derivado de las claves."""
    content, clen, k1, k2, k3 = st
    if of == 3:
        jki, kki, lki, mki, nki = k1, 32, k2, k3, None
    elif of == 2:
        jki, kki, lki, mki, nki = k2, 32, k1, k3, None
    elif of == 1:
        jki, kki, lki, mki, nki = k3, 32, k1, k2, None
    else:
        jki, kki, lki, mki, nki = content, clen, k1, k2, k3

    w0, x1 = _process1(0, 0, lki)
    w0, x1 = _process1(w0, x1, mki)
    if nki is not None:
        w0, x1 = _process1(w0, x1, nki)
    f2 = not _check1(w0, 2)
    f4 = not _check1(w0, 4)
    f8 = not _check1(w0, 8)
    s5 = x1 >> 5
    s6 = 8 - s5

    p7 = 0
    gli: List[int] = [0] * 32
    while p7 < kki:
        pli = p7 + 32
        qli = pli > kki
        if qli:
            pli = kki
            rli = pli - p7
        else:
            rli = 32
        wli = w0
        xli = x1
        tli = 0
        uli = p7
        while tli < rli:
            sli = jki[uli]
            uli += 1
            if f2:
                sli = ((sli & 85) << 1) | ((sli >> 1) & 85)
            if f4:
                sli = ((sli & 51) << 2) | ((sli >> 2) & 51)
            if f8:
                sli = ((sli & 15) << 4) | ((sli >> 4) & 15)
            gli[tli] = sli
            tli += 1
            wli = (wli + sli) & 255
            xli ^= sli
        for j in range(rli):
            for i in range(1, 7):
                a = 1 << i
                if not _check1(j, a - 1):
                    break
                if not _check1(wli, a):
                    _process2(j - (1 << (i - 1)), j, gli)
        zli = xli >> 3
        if qli:
            zli %= rli
        else:
            zli &= 31
        if s5 == 0:
            i = p7
            j = rli - zli
            while i < pli:
                if j == rli:
                    j = 0
                jki[i] = gli[j]
                i += 1
                j += 1
        else:
            i = p7
            j = rli - zli - 1
            while i < pli:
                sli = gli[j] << s6
                j += 1
                if j == rli:
                    j = 0
                sli |= gli[j] >> s5
                jki[i] = sli & 255
                i += 1
        p7 = pli
    return content, clen, k1, k2, k3


def _b0p(fk: List[int], st):
    content, clen, k1, k2, k3 = st
    key = _v_qmi(k2, fk, k3)
    omi = 0
    for off in range(clen):
        content[off] ^= key[omi]
        omi = (omi + 1) % 256
    return content, clen, k1, k2, k3


def _a7l(fk: List[int], st):
    content, clen, k1, k2, k3 = st
    i = (clen | 1) - 2
    key = _v_qmi(fk, k1, k2)
    return _process_content_step((content, clen, k1, k2, k3), key, i)


def _a6i(fk: List[int], st):
    content, clen, k1, k2, k3 = st
    i = (clen - 1) & -2
    key = _v_qmi(k3, fk, k1)
    return _process_content_step((content, clen, k1, k2, k3), key, i)


def _a2f(st):
    content, clen, k1, k2, k3 = st
    dmi = min(32, clen)
    for i in range(dmi):
        x = content[i] ^ k1[i] ^ k2[i] ^ k3[i]
        x12 = x & 12
        if x12 == 0:
            a = k1[i]
        elif x12 == 4:
            a = k2[i]
        elif x12 == 8:
            a = k3[i]
        else:
            a = content[i]
        x3 = x & 3
        if x3 == 0:
            b = k1[i]
            k1[i] = a
        elif x3 == 1:
            b = k2[i]
            k2[i] = a
        elif x3 == 2:
            b = k3[i]
            k3[i] = a
        else:
            b = content[i]
            content[i] = a
        if x12 == 0:
            k1[i] = b
        elif x12 == 4:
            k2[i] = b
        elif x12 == 8:
            k3[i] = b
        else:
            content[i] = b
        x192 = x & 192
        if x192 == 0:
            a = k1[i]
        elif x192 == 64:
            a = k2[i]
        elif x192 == 128:
            a = k3[i]
        else:
            a = content[i]
        x48 = x & 48
        if x48 == 0:
            b = k1[i]
            k1[i] = a
        elif x48 == 16:
            b = k2[i]
            k2[i] = a
        elif x48 == 32:
            b = k3[i]
            k3[i] = a
        else:
            b = content[i]
            content[i] = a
        if x192 == 0:
            k1[i] = b
        elif x192 == 64:
            k2[i] = b
        elif x192 == 128:
            k3[i] = b
        else:
            content[i] = b
    return content, clen, k1, k2, k3


def _b0l(fk: List[int], st):
    content, clen, k1, k2, k3 = st
    k3 = _v_smi(k3, k2, k1, fk)
    k2 = _v_smi(k2, k1, fk, k3)
    k1 = _v_smi(k1, fk, k3, k2)
    return content, clen, k1, k2, k3


def _tb0l(fk: List[int], st):
    content, clen, k1, k2, k3 = st
    key = _v_qmi(k3, k2, fk)
    v7 = 0
    v8 = 0
    for i in range(clen):
        v7, v8 = _step(v7, v8, i, key, content)
    return content, clen, k1, k2, k3


# ── pipeline público ────────────────────────────────────────────────────────

def decrypt_config(text: str):
    """Descifra el configuration_pack.json.

    Acepta el texto completo (busca '"data":"..."') o el contenido crudo del
    data. Devuelve (config_dict, k1, k2, k3) o (None, None, None, None).
    """
    t = text or ""
    if '"data":"' in t:
        marker = '"data":"'
        data_offset = t.index(marker) + len(marker)
        data_end_offset = t.index('"', data_offset)
    else:
        data_offset = 0
        data_end_offset = len(t)
    if data_end_offset - data_offset < 128:
        return None, None, None, None

    fk = [ord(c) for c in "configuration_pack.json"]
    result, k1, k2, k3 = _b64_decode(t, data_offset, data_end_offset)
    st = (bytearray(result), len(result), k1, k2, k3)
    st = _a3b(0, st)
    st = _b0p(fk, st)
    st = _a7l(fk, st)
    st = _a6i(fk, st)
    st = _a2f(st)
    st = _b0l(fk, st)
    st = _a3b(1, st)
    st = _a3b(2, st)
    st = _a3b(3, st)
    st = _tb0l(fk, st)
    content, clen, k1, k2, k3 = st
    payload = bytes(content[:clen])
    try:
        cfg = json.loads(payload.decode("utf-8"))
    except Exception:
        return None, None, None, None
    return cfg, k1, k2, k3


# ── cálculo del token de imagen (b8gNo) ─────────────────────────────────────

def _v_jdf(filename: str) -> str:
    n = int(filename) if filename.isdigit() else -1
    if not (0 <= n <= 1152921504606847000):
        return "0" + filename
    h = format(n, "x")
    return format(len(h), "x") + h


def _v_hdf(k1: List[int], k2: List[int], k3: List[int]) -> List[int]:
    n = max(len(k1), len(k2), len(k3))
    out = [0] * n
    for i in range(len(k1)):
        out[i] ^= k1[i]
    for i in range(len(k2)):
        out[i] ^= k2[i]
    for i in range(len(k3)):
        out[i] ^= k3[i]
    return out


def _vval(value: int) -> int:
    return (48 if value < 10 else 87) + value


def _v_ndf(b9w: List[int], page_id: str, file_name: str) -> str:
    parent_folder = page_id + "/"
    path_length = len(parent_folder) + len(file_name)
    v_bef = (1 + path_length) << 1
    cef = [0] * v_bef
    cef[0] = 0
    cef[1] = 59
    plain = parent_folder + file_name
    p = 2
    for o in range(path_length):
        s = ord(plain[o])
        cef[p] = s >> 8
        p += 1
        cef[p] = s % 256
        p += 1
    fef = 3
    eef = (len(file_name) << 1) + v_bef + v_bef
    while eef < 256:
        eef += v_bef
        fef += 1
    jef = 1670739
    kef = 1282576
    lef = 2237221
    i0 = (1 + len(parent_folder)) << 1
    j = 0
    for _ in range(fef):
        i = i0
        while i < v_bef:
            lef ^= cef[i] ^ b9w[j]
            j += 1
            if j >= len(b9w):
                j = 0
            ief = 435 * lef
            hef = 435 * kef + ((lef & 7) << 18) + (ief >> 22)
            gef = 435 * jef + ((kef & 3) << 19) + ((lef & 4194296) >> 3) + (hef >> 21)
            lef = ief & 4194303
            kef = hef & 2097151
            jef = gef & 2097151
            i += 1
        i0 = 0
    mef = [0] * 16

    def pval(idx: int, value: int) -> None:
        mef[idx] = _vval(value >> 4)
        mef[idx + 1] = _vval(value & 15)

    pval(0, (jef >> 13) ^ b9w[0])
    pval(2, ((jef >> 5) & 255) ^ b9w[1])
    pval(4, (((jef & 31) << 3) | (kef >> 18)) ^ b9w[2])
    pval(6, ((kef >> 10) & 255) ^ b9w[3])
    pval(8, ((kef >> 2) & 255) ^ b9w[4])
    pval(10, (((kef & 3) << 6) | (lef >> 16)) ^ b9w[5])
    pval(12, ((lef >> 8) & 255) ^ b9w[6])
    pval(14, (lef & 255) ^ b9w[7])
    return "".join(chr(v) for v in mef)


def page_token(page_id: str, k1: List[int], k2: List[int], k3: List[int], no: int = 0) -> str:
    """Token `{TOKEN}` (sin '.jpeg') para el archivo page_id y página `no`."""
    fname = str(no if no is not None else 0)
    return _v_jdf(fname) + _v_ndf(_v_hdf(k1, k2, k3), page_id, fname)


def build_tokens(config: dict, k1: List[int], k2: List[int], k3: List[int]) -> Dict[str, str]:
    """Recorre configuration.contents[] y devuelve {fid: TOKEN} (fid='p-XXX.xhtml', TOKEN=18 hex).

    Si hay más de una página por archivo, `TOKEN` corresponde a la primera
    (no=0); los tomos manga usan 1 página/archivo.
    """
    out: Dict[str, str] = {}
    contents = ((config or {}).get("configuration") or {}).get("contents") or []
    for item in contents:
        fid = (item.get("file") or "").strip()
        if not fid:
            continue
        out[fid] = page_token(fid, k1, k2, k3, 0)
    return out


def build_urls(base: str, tokens: Dict[str, str], query: str = "") -> List[str]:
    """Arma URLs {base}/OEBPS/text/{fid}/{TOKEN}.jpeg?{query} en orden natural."""
    def sort_key(fid: str):
        import re

        m = re.match(r"p-(\d+)", fid)
        return (0, int(m.group(1))) if m else (1, fid)

    base = (base or "").rstrip("/") + "/OEBPS/text/"
    urls: List[str] = []
    for fid in sorted(tokens, key=sort_key):
        u = f"{base}{fid}/{tokens[fid]}.jpeg"
        if query:
            u += "?" + query
        urls.append(u)
    return urls