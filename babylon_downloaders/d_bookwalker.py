"""
d_bookwalker.py — BOOKWALKER (bookwalker.jp) downloader único.

Unifica en un solo archivo los módulos legacy de BookWalker:
  - d_bookwalker.py            → tienda + visor trial + member (captura /c)
  - d_bookwalker_har.py        → member vía HAR/tokens (FLUJO A /c / FLUJO B pages)
  - d_bookwalker_xtea.py       → descifrado configuration_pack.json + token de imagen
  - d_bookwalker_unscramble.py → de-scramble de imágenes member

Expone UN downloader unificado (trial + member/HAR):
  - DownloaderBookwalkerHar (site_type "bookwalker") — tienda/búsqueda/ranking y
      muestra gratuita (trial) sin login SIEMPRE; tomos comprados adicionalmente
      vía cookies de cuenta, captura /c del visor o HAR (cURL del /c, de una
      imagen "pages" o un .har pegado).

─────────────────────────── FLUJO TIENDA (HTML) ───────────────────────────
  En bookwalker cada VOLUMEN es un libro independiente con un cid UUID (los
  URLs de tienda son bookwalker.jp/de{cid}/). El catálogo/búsqueda devuelve
  tarjetas li.m-tile de SERIE (link /series/{id}/list/) y de LIBRO (link
  /de{cid}/) según el tipo de página. Sobre una serie se listan sus volúmenes
  como capítulos.

─────────────────────────── VISOR TRIAL (muestras gratis) ─────────────────
  El visor de prueba no requiere login. Flujo (verificado empíricamente):
    1. GET viewer-trial.bookwalker.jp/trial-page/c?cid={cid}&BID=0
       → JSON con status, cty (0=novela, 1=manga), url base y auth_info
         (pfCd, Policy, Signature, Key-Pair-Id). Las firmas CloudFront
         expiran ~1h y están atadas a la IP (Policy con AWS:SourceIp).
    2. {base}configuration_pack.json?{auth} → JSON PLANO (sin cifrar) con
       configuration.contents[] (file=OEBPS/text/p-*.xhtml) y por archivo
       FileLinkInfo.PageCount.
    3. {base}{file}/{n}.jpeg?{auth} → cada página (JPEG).

─────────────────────── VISOR MEMBER (tomos comprados) ────────────────────
  El visor real de `member` ata la sesión al navegador logueado y cada /c es
  de UN SOLO uso por sesión (SESSION), por lo que la vía práctica es capturar
  la petición `/c` del visor del usuario (Copy as cURL) y reproducirla:
    - FLUJO A (/c): pegar el cURL de viewer.bookwalker.jp/browserWebApi/c con
      SESSION/u1/BID y cookies → auth_info {hti,cfg,bid,uuid,pfCd,Policy,
      Signature,Key-Pair-Id} + url base
      (bw-bv-epubs.bookwalker.jp/e_product/{cid}/1/...). El
      configuration_pack.json member va CIFRADO ({version,data} base64 custom
      con 3 claves de 32B; pipeline A8j→tB0l). El token de imagen se DERIVA
      (b8gNo), no es aleatorio → ya no hace falta extraer tokens del HAR.
      Imágenes: {base}/OEBPS/text/{fid}/{TOKEN}.jpeg?{auth}
      (portada: {TOKEN}.jpegbvCoverImage) y llegan SCRAMBLEADAS en bloques
      32x32 → de-scramble con build_seeds_for_book()/unscramble().
    - FLUJO B (pages): pegar el cURL de CUALQUIER imagen del HAR → extrae
      Cookie/UA/Referer/host y se bajan las imágenes con los tokens extraídos
      del propio HAR.
  Las imágenes SOLO se bajan desde la máquina/IP que tiene el tomo abierto
  con su cuenta (los cURL/interacciones salen del navegador del dueño).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode

import requests

from common import CFG, BaseDownloader, extract_series_extras


# ═══════════════════════ TIENDA / TRIAL / MEMBER (d_bookwalker.py) ═══════════════════════

BASE_URL = "https://bookwalker.jp"
TRIAL_URL = "https://viewer-trial.bookwalker.jp/trial-page/c"
MEMBER_URL = "https://viewer.bookwalker.jp/browserWebApi/c"
TRIAL_REFERER = "https://viewer-trial.bookwalker.jp/"
# Host de los archivos firmados (nunca con firma: mero referer)
FILE_REFERER = "https://viewer-epubs-trial.bookwalker.jp/"
COOKIE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bookwalker_cookies.txt"
)
_BROWSER_ID_SUFFIX = "NFBR"
# Campo visible en la configuración del sitio (se persiste en COOKIE_FILE)
SITE_COOKIES = ""

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
_DE_RE = re.compile(r"/de([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/")
_SERIES_RE = re.compile(r"/series/(\d+)/list/")
_TOTAL_RE = re.compile(r"(\d+)～(\d+)件目/全(\d+)件")
_FREE_SIGN = 300  # renovar firma si queda menos de 5 min de validez

MEMBER_SESSION_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bookwalker_member.json"
)


def load_site_cookies() -> str:
    """Lee las cookies de sesión guardadas ('k=v; k2=v2') o ''."""
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_site_cookies(value: str) -> None:
    """Persiste/limpia las cookies de sesión en el archivo del módulo."""
    v = (value or "").strip()
    try:
        if v:
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                f.write(v)
        else:
            os.remove(COOKIE_FILE)
    except OSError:
        pass


def load_member_captures() -> dict:
    """Lectura de las capturas (/c por cid) guardadas del visor member."""
    try:
        with open(MEMBER_SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_member_captures(captures: dict) -> None:
    """Persiste las capturas (/c por cid) del visor member."""
    try:
        with open(MEMBER_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(captures, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def clear_member_captures() -> None:
    """Elimina todas las capturas member guardadas."""
    try:
        os.remove(MEMBER_SESSION_FILE)
    except OSError:
        pass


def import_bookwalker_curl(curl: str) -> Optional[str]:
    """Importa la petición `/c` del visor member pegada como cURL del navegador.

    Espera el texto de 'Copy as cURL' de la petición
    http.../browserWebApi/c?cid=...&u1=...&BID=...&cr=... (incluye el header
    `-b "cookie=..."`). Guarda la captura por {cid} y devuelve el cid, o None
    si el texto no se reconoce.
    """
    text = (curl or "").replace("^", "")
    m = re.search(r"https://viewer\.bookwalker\.jp/browserWebApi/c\?[^\s\"']*", text)
    if not m:
        return None
    qs = {}
    for k, v in parse_qsl(m.group(0).split("?", 1)[1]):
        qs[k] = v
    cid = str(qs.get("cid") or "")
    u1 = str(qs.get("u1") or "")
    bid = str(qs.get("BID") or "")
    if not (cid and u1 and bid):
        return None

    cookies = ""
    mc = re.search(r"-b\s+(\".*?\"|'.*?')", text, re.S)
    if mc:
        cookies = mc.group(1).strip("\"'").strip()

    caps = load_member_captures()
    caps[cid] = {
        "cid": cid,
        "u1": u1,
        "bid": bid,
        "cr": str(qs.get("cr") or ""),
        "cookies": cookies,
        "captured_at": time.time(),
        "auth": None,
        "base": "",
        "cty": 1,
        "expires_at": 0.0,
    }
    save_member_captures(caps)
    return cid


def _make_session() -> requests.Session:
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(HEADERS)
    return s


def _policy_expiry(policy: str) -> Optional[float]:
    """Decodifica el base64 url-safe de `Policy` y extrae AWS:EpochTime."""
    try:
        b64 = policy.replace("-", "+").replace("_", "/")
        b64 += "=" * (-len(b64) % 4)
        data = json.loads(base64.b64decode(b64))
        for st in data.get("Statement") or []:
            less = (st.get("Condition") or {}).get("DateLessThan") or {}
            if "AWS:EpochTime" in less:
                return float(less["AWS:EpochTime"])
    except Exception:
        pass
    return None


def _clean_series_title(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^『|』$", "", t)
    t = re.sub(r"(の\.?電子書籍一覧|の電子書籍(一覧)?|無料試し読みならBOOK.?WALKER).*$", "", t)
    return t.strip(" 　｜|")


# ═══════════════════════ HAR / TOKENS (d_bookwalker_har.py) ═══════════════════════

_HAR_STORAGE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bookwalker_har.json"
)
# Alias visible en la configuración del sitio (miembro)
_STORAGE = _HAR_STORAGE

_CID_PARAM_RE = re.compile(
    r"[?&]cid=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I
)
_CID_DE_RE = re.compile(
    r"/de([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I
)
_XHTML_TOKEN_RE = re.compile(
    r"/OEBPS/text/((?:p-[a-z0-9_-]+)\.xhtml)/([0-9a-f]{12,})\.jpeg", re.I
)
_BASE_BEFORE_OEBPS_RE = re.compile(r"(https?://[^/]+/.+?/OEBPS/text/)", re.I)
_CURL_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_CURL_H_RE = re.compile(r'''\-H\s+(?:"([^"]+)"|'([^']+)')''', re.I)
_CURL_COOKIE_RE = re.compile(r'''\-b\s+(?:"([^"]+)"|'([^']+)')''', re.I)
_CURL_REFERER_RE = re.compile(r'''\-e\s+(?:"([^"]+)"|'([^']+)')''', re.I)

_AUTH_KEYS = ("hti", "cfg", "bid", "uuid", "pfCd", "Policy", "Signature", "Key-Pair-Id")
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)
_DEFAULT_REFERER = "https://viewer.bookwalker.jp/"


def load_har_captures() -> dict:
    try:
        with open(_HAR_STORAGE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_har_captures(captures: dict) -> None:
    try:
        with open(_HAR_STORAGE, "w", encoding="utf-8") as f:
            json.dump(captures, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def clear_har_captures() -> None:
    try:
        os.remove(_HAR_STORAGE)
    except OSError:
        pass


def _extract_cid(data: str, base: str = "") -> str:
    m = _CID_PARAM_RE.search(data) or _CID_DE_RE.search(data)
    if m:
        return m.group(1).lower()
    if base:
        m = _UUID_RE.search(base)
        if m:
            return m.group(0).lower()
    m = _UUID_RE.search(data)
    return m.group(0).lower() if m else ""


def _sort_key(page: str) -> tuple:
    m = re.match(r"p-(\d+)", page)
    return (0, int(m.group(1))) if m else (1, page)


def _parse_curl_request(curl: str) -> Dict[str, str]:
    """Extrae URL, cookies y headers de un 'Copy as cURL' (cmd)."""
    text = (curl or "").replace("^", "")
    m = _CURL_URL_RE.search(text)
    url = m.group(0).rstrip(";") if m else ""
    headers: Dict[str, str] = {}
    for hm in _CURL_H_RE.finditer(text):
        raw = (hm.group(1) or hm.group(2) or "").strip()
        if ":" in raw:
            k, v = raw.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    cookie = ""
    cm = _CURL_COOKIE_RE.search(text)
    if cm:
        cookie = (cm.group(1) or cm.group(2) or "").strip()
    else:
        cookie = headers.pop("cookie", "")
    if cookie and "cookie" not in headers:
        headers["cookie"] = cookie
    rm = _CURL_REFERER_RE.search(text)
    if rm and "referer" not in headers:
        headers["referer"] = (rm.group(1) or rm.group(2) or "").strip()
    return {"url": url, "cookie": cookie, "headers": headers}


def _pick_capture(
    captures: dict, cid: str, *, lacking_session: bool = False
) -> Optional[str]:
    if cid in captures:
        return cid
    # Hay una sola captura sin sesión → asumir que el cURL le pertenece
    if not cid:
        candidates = [k for k, v in captures.items() if not v.get("session")]
        return candidates[0] if len(candidates) == 1 else None
    return None


def _flow_item(cid: str, title: str) -> dict:
    return {"kind": "capture", "id": cid, "cid": cid, "title": title, "slug": cid}


def _capture_title(cap: dict, cid: str) -> str:
    t = str(cap.get("title") or "").strip()
    return t or f"bookwalker {cid[:8]}"


# ═══════════════════════ XTEA (d_bookwalker_xtea.py) ═══════════════════════
# Descifrado del configuration_pack.json de BOOKWALKER y cálculo del token de
# imagen `{TOKEN}.jpeg`.
#
# Port a Python del algoritmo del visor (viewer.bookwalker.jp), basado en el
# userscript open-source 'bookwalker-native-downloader' (GolyBidoof, MIT), que
# a su vez porta byte-a-byte el JS minificado del visor (funciones A8j/A3b/
# B0p/A7L/A6I/A2F/B0L/tB0l/b8gNo conservando los nombres originales).
#
#   - El configuration_pack.json "member" es un sobre cifrado:
#         { "version":"1.0", "data":"<custom-base64>" }
#     Los primeros 128 chars del `data` son 3 claves de 32 bytes (k1,k2,k3); el
#     resto es el payload. Pipeline fijo de descifrado:
#         A8j (custom base64) -> A3b(0) -> B0p -> A7L -> A6I -> A2F -> B0L
#         -> A3b(1) -> A3b(2) -> A3b(3) -> tB0l   ->  UTF-8 JSON
#   - El token de imagen se DERIVA (no es aleatorio): por cada archivo
#         p-XXX.xhtml  ->  b8gNo(fid, k1, k2, k3, no)   (no: índice de página
#     dentro de PageLinkInfoList, 0 para manga de 1 página/archivo). El token
#     resultante son 18 hex (p.ej. "10" + 16 hex). URL final:
#         {base}/OEBPS/text/{fid}/{TOKEN}.jpeg

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


def page_token(
    page_id: str, k1: List[int], k2: List[int], k3: List[int], no: int = 0
) -> str:
    """Token `{TOKEN}` (sin '.jpeg') para el archivo page_id y página `no`."""
    fname = str(no if no is not None else 0)
    return _v_jdf(fname) + _v_ndf(_v_hdf(k1, k2, k3), page_id, fname)


def build_tokens(
    config: dict, k1: List[int], k2: List[int], k3: List[int]
) -> Dict[str, str]:
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


# ═══════════════════════ UNSCRAMBLE (d_bookwalker_unscramble.py) ═══════════════════════
# De-scramble de imágenes BOOKWALKER member.
#
# Port a Python de bookworm (aaa4xu, GPL) / bookwalker-native-downloader: las
# páginas se sirven divididas en bloques de 32x32 px desordenados con una
# permutación determinista; el visor las reensambla al pintarlas en un canvas.
#
#   - B2y  : PRNG xorshift de 32 bits con tablas (v_cgh/v_dgh).
#   - a3f  : deriva la permutación (latin square + Fisher-Yates) con 4 seeds.
#   - A9p  : convierte la permutación en lista de movimientos (srcX,srcY,..).
#   - page_seeds(): semillas por página desde pageId+No+NS/PS/RS+keys (Page.ts).
#   - unscramble(): aplica los movimientos a los píxeles (RGBA).
#
# Validado byte a byte contra los fixtures de bookworm (A9p-001.json y
# image-001-*.png, uso individual/Solo lectura).

_V_CGH = json.loads(
    '[[1,3,10],[1,5,16],[1,5,19],[1,9,29],[1,11,6],[1,11,16],[1,19,3],[1,21,20],[1,27,27],'
    '[2,5,15],[2,5,21],[2,7,7],[2,7,9],[2,7,25],[2,9,15],[2,15,17],[2,15,25],[2,21,9],'
    '[3,1,14],[3,3,26],[3,3,28],[3,3,29],[3,5,20],[3,5,22],[3,5,25],[3,7,29],[3,13,7],'
    '[3,23,25],[3,25,24],[3,27,11],[4,3,17],[4,3,27],[4,5,15],[5,3,21],[5,7,22],[5,9,7],'
    '[5,9,28],[5,9,31],[5,13,6],[5,15,17],[5,17,13],[5,21,12],[5,27,8],[5,27,21],[5,27,25],'
    '[5,27,28],[6,1,11],[6,3,17],[6,17,9],[6,21,7],[6,21,13],[7,1,9],[7,1,18],[7,1,25],'
    '[7,13,25],[7,17,21],[7,25,12],[7,25,20],[8,7,23],[8,9,23],[9,5,14],[9,5,25],[9,11,19],'
    '[9,21,16],[10,9,21],[10,9,25],[11,7,12],[11,7,16],[11,17,13],[11,21,13],[12,9,23],'
    '[13,3,17],[13,3,27],[13,5,19],[13,17,15],[14,1,15],[14,13,15],[15,1,29],[17,15,20],'
    '[17,15,23],[17,15,26]]'
)
_M32 = 0xFFFFFFFF


def _shl(value: int, n: int) -> int:
    return (value << n) & _M32


def _mix_fn(fn: int, p1: int, p2: int, p3: int, p4: int) -> int:
    p1 &= _M32
    if fn == 0:
        # p1 ^= p1 << p2;  p1 ^= p1 >>> p3;  p1 ^= p1 << p4
        p1 ^= _shl(p1, p2)
        p1 ^= p1 >> p3
        p1 ^= _shl(p1, p4)
    elif fn == 1:
        # p1 ^= p1 << p4;  p1 ^= p1 >>> p3;  p1 ^= p1 << p2
        p1 ^= _shl(p1, p4)
        p1 ^= p1 >> p3
        p1 ^= _shl(p1, p2)
    elif fn == 2:
        # p1 ^= p1 >>> p2;  p1 ^= p1 << p3;  p1 ^= p1 >>> p4
        p1 ^= p1 >> p2
        p1 ^= _shl(p1, p3)
        p1 ^= p1 >> p4
    elif fn == 3:
        # p1 ^= p1 >>> p4;  p1 ^= p1 << p3;  p1 ^= p1 >>> p2
        p1 ^= p1 >> p4
        p1 ^= _shl(p1, p3)
        p1 ^= p1 >> p2
    elif fn == 4:
        # p1 ^= p1 << p2;  p1 ^= p1 << p4;  p1 ^= p1 >>> p3
        p1 ^= _shl(p1, p2)
        p1 ^= _shl(p1, p4)
        p1 ^= p1 >> p3
    else:
        # p1 ^= p1 >>> p2;  p1 ^= p1 >>> p4;  p1 ^= p1 << p3
        p1 ^= p1 >> p2
        p1 ^= p1 >> p4
        p1 ^= _shl(p1, p3)
    return p1 & _M32


class B2y:
    b6o = len(_V_CGH)      # 82
    b6b = 6                # v_dgh.length
    b4v = b6o * b6b        # 492

    def __init__(self) -> None:
        self._row = 74
        self._fn = 0
        self.xyz = list(_V_CGH[74])
        self.v_jgh = 2463534242

    def b9es(self, row: int, fn: int) -> None:
        self.v_jgh = 2463534242
        self._row = row
        self._fn = fn
        self.xyz = list(_V_CGH[row])

    def B0o(self, value: int) -> None:
        v = (value & _M32) or 2463534242
        self.v_jgh = v

    def b4K(self, bound: int) -> int:
        """Número pseudoaleatorio 0..bound-1 con rechazo de sesgo."""
        if bound <= 1:
            return 0
        v_vgh = 4294967295 - bound
        v_ugh = self.v_jgh
        while True:
            v_ugh = _mix_fn(self._fn, v_ugh, self.xyz[0], self.xyz[1], self.xyz[2])
            v_tgh = v_ugh - 1
            v_sgh = v_tgh % bound
            if not (v_vgh < v_tgh - v_sgh):
                break
        self.v_jgh = v_ugh
        return v_sgh


def _mqg(fn, total: int) -> List[int]:
    out: List[int] = []
    for i in range(total):
        n = fn(i + 1)  # 0..i (self swap posible)
        old = out[n] if n < len(out) else None
        if len(out) <= n:
            out.extend([None] * (n + 1 - len(out)))
        out[n] = i
        if n != i:
            if len(out) <= i:
                out.extend([None] * (i + 1 - len(out)))
            out[i] = old
    return out


def _x6qg(fn, value: int) -> int:
    return fn(value + 1) if value < 4 else fn(value - 1) + 1


def _x7qg(fn, ye_e_: int, e_e_: int) -> int:
    if e_e_ <= 0:
        return 0
    v = fn(e_e_)
    return v if v < ye_e_ else v + 1


def _x9qg(fn, p2: List[int], p3: List[int], p4: int, p5: int, p6: int, p7: int) -> None:
    """v_9qg de a3f: completa el cuadro latino de filas/columnas."""
    dq, eq = p6, p7
    fq, gq = p4, p5
    hq = 0
    iq = 0
    jq = -1

    def get(arr, idx):
        if idx < 0 or idx >= len(arr) or arr[idx] is None:
            return None
        return arr[idx]

    def ge(a, b):
        # emula `a >= b` en JS con undefined → NaN → false
        return a is not None and b is not None and a >= b

    def le(a, b):
        # emula `a <= b` en JS con undefined → NaN → false
        return a is not None and b is not None and a <= b

    while dq + eq > 0:
        aq = fn(dq + eq)
        if aq < dq:
            if aq < fq:
                bq = iq
                while bq > 0 and not ge(hq, get(p2, bq + jq)):
                    bq -= 1
                cq = iq + eq
                while cq < p7 and not ge(hq, get(p2, cq)):
                    cq += 1
                v = fn(cq - bq) + bq
                while len(p3) <= hq:
                    p3.append(None)
                p3[hq] = v
                hq += 1
                fq -= 1
            else:
                bq = iq
                while bq > 0 and not le(hq + dq, get(p2, bq + jq)):
                    bq -= 1
                cq = iq + eq
                while cq < p7 and not le(hq + dq, get(p2, cq)):
                    cq += 1
                v = fn(cq - bq) + bq
                idx = hq + dq + jq
                while len(p3) <= idx:
                    p3.append(None)
                p3[idx] = v
            dq -= 1
        else:
            if aq - dq < gq:
                bq = hq
                while bq > 0 and not ge(iq, get(p3, bq + jq)):
                    bq -= 1
                cq = hq + dq
                while cq < p6 and not ge(iq, get(p3, cq)):
                    cq += 1
                v = fn(cq - bq) + bq
                while len(p2) <= iq:
                    p2.append(None)
                p2[iq] = v
                iq += 1
                gq -= 1
            else:
                bq = hq
                while bq > 0 and not le(iq + eq, get(p3, bq + jq)):
                    bq -= 1
                cq = hq + dq
                while cq < p6 and not le(iq + eq, get(p3, cq)):
                    cq += 1
                v = fn(cq - bq) + bq
                idx = iq + eq + jq
                while len(p2) <= idx:
                    p2.append(None)
                p2[idx] = v
            eq -= 1


def _qpg(p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11, p12, p13) -> List[int]:
    result: List[int] = []
    v_1qg = p1 + 1
    v_2qg = p2 + 1
    v_3qg = v_1qg << 1
    v_4qg = v_2qg << 1

    def lt(a, b):
        return a is not None and b is not None and a < b

    for v_vpg in range(p1):
        for v_wpg in range(p2):
            v_zpg = p3[v_vpg + v_wpg * p1]
            v_xpg = v_zpg % p1
            v_ypg = (v_zpg - v_xpg) // p1
            v_rpg = v_vpg if lt(v_vpg, p11[v_wpg]) else v_vpg + v_1qg
            v_spg = v_wpg if lt(v_wpg, p10[v_vpg]) else v_wpg + v_2qg
            v_tpg = v_xpg if lt(v_xpg, p7[v_ypg]) else v_xpg + v_1qg
            v_upg = v_ypg if lt(v_ypg, p6[v_xpg]) else v_ypg + v_2qg
            result.append(v_upg * v_3qg + v_rpg)
            result.append(v_tpg * v_4qg + v_spg)

    result.append(p9 * v_3qg + p12)
    result.append(p8 * v_4qg + p13)

    for v_vpg in range(p1):
        v_xpg = p4[v_vpg]
        v_rpg = v_vpg if lt(v_vpg, p12) else v_vpg + v_1qg
        v_tpg = v_xpg if lt(v_xpg, p8) else v_xpg + v_1qg
        result.append(p6[v_xpg] * v_3qg + v_rpg)
        result.append(v_tpg * v_4qg + p10[v_vpg])

    for v_wpg in range(p2):
        v_ypg = p5[v_wpg]
        v_spg = v_wpg if lt(v_wpg, p13) else v_wpg + v_2qg
        v_upg = v_ypg if lt(v_ypg, p9) else v_ypg + v_2qg
        result.append(v_upg * v_3qg + p11[v_wpg])
        result.append(p7[v_ypg] * v_4qg + v_spg)

    return result


def a3f(p1: int, p2: int, p3: int, p4: int) -> List[int]:
    tog = B2y()
    v_uog = p2 ^ p3 ^ p4
    v_vog = p1 // 65536
    v_wog = p2 // 65536
    v_xog = p3 // 65536
    v_yog = p4 // 65536
    v_zog = B2y.b6o
    v_0pg = B2y.b6b

    v_1pg = v_wog ^ v_xog ^ v_yog
    v_2pg = v_vog ^ v_yog
    v_3pg = p1 ^ p2
    v_4pg = p1 ^ p3
    v_5pg = p1 ^ p4

    v_1pg >>= 16
    v_6pg = v_1pg % v_0pg
    v_7pg = ((v_1pg - v_6pg) // v_0pg) % v_zog

    tog.b9es(v_7pg, v_6pg)
    tog.B0o(v_uog)
    fn = tog.b4K
    v_9pg = fn(65536) | (fn(65536) << 16)
    v_apg = fn(512)
    v_bpg = v_wog >> 16
    v_cpg = v_xog >> 16

    v_2pg = (v_2pg >> 16) ^ v_apg
    v_3pg = (v_3pg ^ v_9pg) & _M32
    v_4pg = (v_4pg ^ v_9pg) & _M32
    v_5pg = (v_5pg ^ v_9pg) & _M32

    v_dpg = v_2pg % v_0pg
    v_epg = ((v_2pg - v_dpg) // v_0pg) % v_zog

    tog.b9es(v_epg, v_dpg)
    tog.B0o(v_3pg)
    v_fpg = _mqg(fn, v_bpg * v_cpg)

    tog.B0o(v_4pg)
    v_gpg = _x6qg(fn, v_bpg)
    v_hpg = _x6qg(fn, v_cpg)
    v_ipg = _x7qg(fn, v_gpg, v_bpg)
    v_jpg = _x7qg(fn, v_hpg, v_cpg)

    tog.B0o(v_5pg)
    v_kpg: List[int] = []
    v_lpg: List[int] = []
    _x9qg(fn, v_kpg, v_lpg, v_gpg, v_hpg, v_bpg, v_cpg)

    v_mpg = _mqg(fn, v_bpg)
    v_npg = _mqg(fn, v_cpg)
    v_opg: List[int] = []
    v_ppg: List[int] = []
    _x9qg(fn, v_ppg, v_opg, v_ipg, v_jpg, v_bpg, v_cpg)

    return _qpg(v_bpg, v_cpg, v_fpg, v_mpg, v_npg, v_opg, v_ppg, v_ipg, v_jpg, v_lpg, v_kpg, v_gpg, v_hpg)


def a9p_move_list(page: dict, width: int, height: int):
    """Devuelve lista de tuplas (srcX, srcY, destX, destY, w, h).

    En A9p los nombres son del lado del *encode* (untado de la imagen correcta
    en la posición scrambleada): src = posición original (correcta), dest =
    dónde acaba el bloque en la imagen scrambleada. Por tanto para DECODE hay
    que copiar imagen_scramble[dest] -> salida[src]."""
    block_width = page["b8A"]
    block_height = page["b6V"]
    r = page["B0J"]
    s = page["B0K"]
    t = page["B0n"]
    u = page["B0A"]
    v_v = B2y.b6o
    v_w = B2y.b6b
    blocks_x = width // block_width
    blocks_y = height // block_height
    last_block_width = width % block_width
    last_block_height = height % block_height
    v_14j = (blocks_x + 1) << 1
    v_24j = (blocks_y + 1) << 1
    last_block_xvs = (blocks_x + 1) * block_width - last_block_width
    last_block_yvs = (blocks_y + 1) * block_height - last_block_height

    g = B2y()
    v_64j = u ^ blocks_x ^ blocks_y
    v_74j = v_64j % v_w
    v_84j = ((v_64j - v_74j) // v_w) % v_v
    g.b9es(v_84j, v_74j)
    g.B0o(r ^ s ^ t)
    v_94j = g.b4K(65536) + g.b4K(65536) * 65536 + g.b4K(512) * 4294967296

    v_a4j = blocks_x * 4294967296 + r
    v_b4j = blocks_y * 4294967296 + s
    v_c4j = u * 4294967296 + t

    d4 = a3f(v_94j, v_a4j, v_b4j, v_c4j)

    moves: List[Tuple[int, int, int, int, int, int]] = []
    idx = 0

    def emit(total: int, step_w: int, step_h: int) -> None:
        nonlocal idx
        if step_w == 0 or step_h == 0:
            return
        while idx < total:
            f = d4[idx]
            idx += 1
            gv = d4[idx]
            idx += 1
            h = f % v_14j
            i = gv % v_24j
            j = (gv - i) // v_24j
            k = (f - h) // v_14j
            src_x = h * block_width - (last_block_xvs if h > blocks_x else 0)
            src_y = i * block_height - (last_block_yvs if i > blocks_y else 0)
            dest_x = j * block_width - (last_block_xvs if j > blocks_x else 0)
            dest_y = k * block_height - (last_block_yvs if k > blocks_y else 0)
            moves.append((src_x, src_y, dest_x, dest_y, step_w, step_h))

    n = blocks_x * blocks_y * 2
    emit(n, block_width, block_height)
    n += 2
    emit(n, last_block_width, last_block_height)
    n += blocks_x * 2
    emit(n, block_width, last_block_height)
    n += blocks_y * 2
    emit(n, last_block_width, block_height)
    return moves


def _v_mhf(key: List[int]) -> int:
    n = len(key) & -4
    if n > 32:
        n = 32
    nhf = 0
    for p in range(0, n, 4):
        nhf ^= key[p] << 24
        nhf ^= key[p + 1] << 16
        nhf ^= key[p + 2] << 8
        nhf ^= key[p + 3]
    return nhf & _M32


def page_seeds(
    page_id: str,
    no: int,
    ns: int,
    ps: int,
    rs: int,
    block_width: int,
    block_height: int,
    k1: List[int],
    k2: List[int],
    k3: List[int],
) -> dict:
    v0 = 47
    for ch in page_id:
        v0 += ord(ch)
    fname = str(int(no))
    for ch in fname:
        v0 += ord(ch)
    for k in (k1, k2, k3):
        v0 += sum(k)

    v9 = (v0 & 255)
    v9 |= v9 << 8
    v9 |= v9 << 16
    v9 &= _M32

    return {
        "B0A": v0 % B2y.b4v,
        "B0J": (v9 ^ _v_mhf(k1) ^ (ns & _M32)) & _M32,
        "B0K": (v9 ^ _v_mhf(k2) ^ (ps & _M32)) & _M32,
        "B0n": (v9 ^ _v_mhf(k3) ^ (rs & _M32)) & _M32,
        "b8A": block_width,
        "b6V": block_height,
    }


def build_seeds_for_book(
    config: dict, k1: List[int], k2: List[int], k3: List[int]
) -> dict:
    """Seeds por fid completo (ej. 'OEBPS/text/p-001.xhtml')."""
    out: dict = {}
    contents = ((config or {}).get("configuration") or {}).get("contents") or []
    for item in contents:
        fid = (item.get("file") or "").strip()
        if not fid:
            continue
        page_cfg = (config or {}).get(fid) or {}
        plist = ((page_cfg.get("FileLinkInfo") or {}).get("PageLinkInfoList") or [])
        pl = (plist[0] or {}).get("Page") or {}
        # Solo las páginas que declaran rejilla (BlockWidth/BlockHeight) y las
        # semillas NS/PS/RS llegan scrambleadas (p. ej. la portada NO lo está).
        scrambled = bool(
            pl.get("BlockWidth")
            and pl.get("BlockHeight")
            and "NS" in pl
            and "PS" in pl
            and "RS" in pl
        )
        seeds = page_seeds(
            fid,
            pl.get("No", 0),
            pl.get("NS", 0),
            pl.get("PS", 0),
            pl.get("RS", 0),
            pl.get("BlockWidth", 32),
            pl.get("BlockHeight", 32),
            k1,
            k2,
            k3,
        )
        seeds["Size"] = pl.get("Size") or {}
        seeds["Scrambled"] = scrambled
        out[fid] = seeds
    return out


def unscramble(src_rgba: bytes, width: int, height: int, seeds: dict):
    """devuelve bytearray RGBA con los bloques ya colocados (posición original)."""
    moves = a9p_move_list(seeds, width, height)
    out = bytearray(len(src_rgba))
    px = width * 4
    for (sx, sy, dx, dy, w, h) in moves:
        for y in range(h):
            # fila fuente: imagen scrambleada en dy+y; columna dx
            s_off = (dy + y) * px + dx * 4
            # fila destino: salida en sy+y; columna sx
            d_off = (sy + y) * px + sx * 4
            out[d_off:d_off + w * 4] = src_rgba[s_off:s_off + w * 4]
    return out


# _HAS_UNSCRAMBLE: el de-scramble vive en este mismo módulo → siempre True
_HAS_UNSCRAMBLE = True


# ═══════════════════════ DownloaderBookwalker (tienda/trial/member) ═══════════════════════

class DownloaderBookwalker(BaseDownloader):
    NAME = "BOOKWALKER   (bookwalker.jp)"
    NEEDS_LOGIN = False
    HAS_CATALOG = True
    HAS_SEARCH = True

    def __init__(self):
        self._sess = _make_session()
        self._logged = False
        # Firma por cid: (expiry_ts, resolve_dict) — trial y member por separado
        self._res_cache: dict[str, tuple[Optional[float], dict]] = {}
        self._mem_cache: dict[str, tuple[Optional[float], dict]] = {}
        self._ensure_cookies()

    def _make_browser_id(self) -> str:
        now = int(time.time() * 1000)
        rnd = "%08d" % random.randint(0, 99999999)
        return f"{now}{rnd}{_BROWSER_ID_SUFFIX}"

    def _inject_cookies(self, cookies) -> None:
        if isinstance(cookies, str):
            for part in cookies.split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                k, _, v = part.partition("=")
                k = k.strip()
                if k:
                    self._sess.cookies.set(
                        k, v.strip(), domain=".bookwalker.jp", path="/"
                    )
        elif isinstance(cookies, dict) and cookies:
            self._sess.cookies.update(cookies)
        self._sess.headers.update({"Referer": "https://member.bookwalker.jp/"})
        self._logged = True

    def _ensure_cookies(self) -> bool:
        """Reintenta el login si ya hay cookies persistidas en el archivo."""
        if self._logged:
            return True
        cookies = load_site_cookies()
        if not cookies:
            return False
        try:
            self._inject_cookies(cookies)
        except Exception:
            self._logged = False
            return False
        return True

    # ── helpers de red ───────────────────────────────────────────────────

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[requests.Response]:
        last = None
        for attempt in range(3):
            try:
                r = self._sess.get(url, params=params, timeout=CFG.get("timeout", (15, 45)))
                if r.status_code == 200:
                    return r
                if r.status_code in (403, 404, 410):
                    return None
                last = r
            except Exception:
                pass
            time.sleep(CFG.get("retry_delay", 2.0))
        return last

    # ── visor de prueba (trial) ──────────────────────────────────────────

    def _trial_resolve(self, cid: str) -> Optional[dict]:
        """Firma CloudFront para el trial del cid. Renueva si está por vencer."""
        if cid in self._res_cache:
            expiry, res = self._res_cache[cid]
            if expiry is not None and expiry > time.time() + _FREE_SIGN:
                return res

        r = self._get(TRIAL_URL, {"cid": cid, "BID": "0"})
        if r is None:
            return None
        try:
            d = r.json()
        except Exception:
            return None
        if d.get("status") != "200" or not d.get("url"):
            return None

        base = str(d["url"]).rstrip("/") + "/"
        if int(d.get("cty") or 0) == 0:
            base += "normal_default/"
        info = d.get("auth_info") or {}
        auth = {
            k: info.get(k)
            for k in ("pfCd", "Policy", "Signature", "Key-Pair-Id")
        }
        expiry = _policy_expiry(str(auth.get("Policy") or ""))
        res = {
            "base": base,
            "auth": auth,
            "cty": int(d.get("cty") or 0),
            "cti": str(d.get("cti") or ""),
        }
        self._res_cache[cid] = (expiry, res)
        return res

    def _member_session_cookies(self, cap: dict) -> None:
        """Aplica cookies de cuenta + cookie SESSION desde una captura del visor."""
        if cap.get("cookies"):
            self._inject_cookies(cap["cookies"])
        sid = str(cap.get("u1") or "")
        if sid:
            self._sess.cookies.set(
                "SESSION", sid, domain="viewer.bookwalker.jp", path="/browserWebApi/"
            )
        # referer idéntico al del visor (lo espera el endpoint /c)
        cid = str(cap.get("cid") or "")
        self._sess.headers.update(
            {"Referer": f"https://viewer.bookwalker.jp/03/30/viewer.html?cid={cid}&cty=1"}
        )
        self._logged = True

    @staticmethod
    def _res_from_capture(cap: dict) -> Optional[dict]:
        """Convierte una captura consumida (auth/base/cty) en un res usable."""
        auth = cap.get("auth") or {}
        base = str(cap.get("base") or "").rstrip("/")
        if not auth or not base:
            return None
        base += "/"
        if int(cap.get("cty") or 0) == 0 and not base.endswith("normal_default/"):
            base += "normal_default/"
        return {
            "base": base,
            "auth": {k: auth[k] for k in auth if auth[k] is not None and str(auth[k]) != ""},
            "cty": int(cap.get("cty") or 0),
            "cti": "",
            "member": True,
        }

    def _member_resolve(self, cid: str) -> Optional[dict]:
        """Sesión del visor de `member` para un tomo COMPRADO (captura del usuario).

        Usa la captura `/c` importada con import_bookwalker_curl(). El endpoint
        es de UN SOLO uso por SESSION: si ya hay una firma válida cacheada no se
        re-consume; si caducó (~1h) se necesita una captura nueva.
        """
        if cid in self._mem_cache:
            expiry, res = self._mem_cache[cid]
            if expiry is not None and expiry > time.time() + _FREE_SIGN:
                return res

        caps = load_member_captures()
        cap = caps.get(cid)
        if not cap:
            return None
        self._member_session_cookies(cap)

        exp = float(cap.get("expires_at") or 0)
        res = None
        if exp > time.time() + _FREE_SIGN and cap.get("auth"):
            # Firma ya consumida y válida → no re-llamar /c (es one-shot)
            res = self._res_from_capture(cap)
        else:
            r = self._get(
                MEMBER_URL,
                {
                    "cid": cid,
                    "u1": str(cap.get("u1") or ""),
                    "BID": str(cap.get("bid") or ""),
                    "cr": str(cap.get("cr") or "")
                    or str(int(time.time() % 10**19)),
                },
            )
            if r is not None:
                d = None
                try:
                    d = r.json()
                except Exception:
                    d = None
                if isinstance(d, dict) and str(d.get("status")) == "200" and d.get("url"):
                    info = d.get("auth_info") or {}
                    auth = {
                        k: info.get(k)
                        for k in ("hti", "cfg", "Policy", "Signature", "Key-Pair-Id")
                        if info.get(k)
                    }
                    cap["auth"] = auth
                    cap["base"] = str(d["url"]).rstrip("/")
                    cap["cty"] = int(d.get("cty") or 0)
                    cap["expires_at"] = float(
                        _policy_expiry(str(info.get("Policy") or "")) or 0
                    )
                    caps[cid] = cap
                    save_member_captures(caps)
                    res = self._res_from_capture(cap)

        if res is None and exp > time.time() + _FREE_SIGN:
            res = self._res_from_capture(cap)
        if res is None:
            return None
        self._mem_cache[cid] = (exp, res)
        return res

    def _configuration(self, res: dict) -> Optional[dict]:
        r = self._get(res["base"] + "configuration_pack.json", params=res["auth"])
        if r is None:
            return None
        try:
            return r.json()
        except Exception:
            return None

    def _build_page_urls(self, res: dict, cfg: dict) -> list[str]:
        base = res["base"]
        query = urlencode({k: str(v) for k, v in (res["auth"] or {}).items() if v})
        contents = ((cfg.get("configuration") or {}).get("contents")) or []
        urls: list[str] = []
        for chapter in contents:
            key = (chapter.get("file") or "").strip()
            if not key:
                continue
            entry = cfg.get(key) or cfg.get(key.lstrip("./")) or {}
            n = int(((entry.get("FileLinkInfo") or {}).get("PageCount")) or 0)
            if n <= 0:
                continue
            for p in range(n):
                urls.append(f"{base}{key}/{p}.jpeg?{query}")
        return urls

    # ── parseo de tarjetas (catálogo / búsqueda / serie) ────────────────

    @staticmethod
    def _img_title(img) -> str:
        for attr in ("title", "alt"):
            v = (img.get(attr) or "").strip()
            if v:
                return v
        return ""

    @staticmethod
    def _tile_item(tile) -> Optional[dict]:
        """Convierte una tarjeta `li.m-tile` en item de libro o de serie."""
        thumb = tile.select_one("a.m-thumb__image") or tile.select_one("a.m-book-item__title")
        if thumb is None:
            return None
        href = str(thumb.get("href") or "")
        img = thumb.find("img") if thumb else None
        title = (thumb.get("title") or "").strip() or (DownloaderBookwalker._img_title(img) if img is not None else "")
        if not title:
            t2 = tile.select_one("a.m-book-item__title[title]")
            if t2 is not None:
                title = (t2.get("title") or "").strip()
        cover = ""
        if img is not None:
            cover = (
                img.get("data-original") or img.get("data-src")
                or img.get("src") or ""
            ).strip()
            if cover.startswith("//"):
                cover = "https:" + cover

        m_ser = _SERIES_RE.search(href)
        if m_ser:
            item = {
                "kind": "series",
                "id": str(int(m_ser.group(1))),
                "slug": f"series/{int(m_ser.group(1))}",
                "title": title,
                "url": f"{BASE_URL}/series/{int(m_ser.group(1))}/list/",
            }
            if cover:
                item["cover"] = cover
            # cids de volúmenes destacados (1巻 / 最新刊)
            cids = [
                m.group(1)
                for a in tile.select("a[href*='/de']")
                for m in [_DE_RE.search(str(a.get("href") or ""))]
                if m
            ]
            if cids:
                item["vol_cids"] = list(dict.fromkeys(cids))
            return item

        m_de = _DE_RE.search(href)
        if m_de:
            cid = m_de.group(1)
            trial_btn = tile.select_one("a.a-icon-btn--trial")
            item = {
                "kind": "book",
                "id": cid,
                "slug": "de" + cid,
                "title": title,
                "url": f"{BASE_URL}/de{cid}/",
                "trial": trial_btn is not None,
            }
            if cover:
                item["cover"] = cover
            return item
        return None

    def _parse_items(self, soup) -> list[dict]:
        items: list[dict] = []
        seen: set[str] = set()
        for tile in soup.select("li.m-tile"):
            it = self._tile_item(tile)
            if it and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
        return items

    def _fetch_search_page(
        self, word: str = "", order: str = "rank", free: str = "", page: int = 1
    ) -> tuple[list[dict], bool, Optional[int]]:
        params: dict = {"order": order, "page": page}
        if word:
            params["word"] = word
        if free:
            params["qpri"] = free
        r = self._get(f"{BASE_URL}/search/", params)
        if r is None:
            return [], False, None
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return [], False, None
        try:
            soup = BeautifulSoup(r.text, "lxml")
        except Exception:
            soup = BeautifulSoup(r.text, "html.parser")
        items = self._parse_items(soup)
        total = None
        m = _TOTAL_RE.search(r.text)
        if m:
            total = int(m.group(3))
        has_more = bool(
            r.text.find(f"page={page + 1}") != -1
            or (total is not None and page * 60 < total)
        )
        return items, has_more, total

    # ── catálogo paginado (buffer por filtro) ────────────────────────────

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list[dict], bool]:
        word = str(kwargs.get("word", "")).strip()
        order = str(kwargs.get("order", "rank")).strip() or "rank"
        free = str(kwargs.get("free", "")).strip()
        key = repr(["bw", word, order, free])

        if getattr(self, "_cat_buf_key", None) != key:
            self._cat_buf = []
            self._cat_buf_key = key
            self._bw_site = 0
            self._bw_finished = False

        need = page * page_size
        while len(self._cat_buf) < need and not self._bw_finished:
            self._bw_site += 1
            items, has_more, _total = self._fetch_search_page(
                word=word, order=order, free=free, page=self._bw_site
            )
            if not items:
                self._bw_finished = True
                break
            self._cat_buf.extend(items)
            if not has_more:
                self._bw_finished = True

        start = (page - 1) * page_size
        end = start + page_size
        chunk = self._cat_buf[start:end]
        has_more = (not self._bw_finished) or len(self._cat_buf) > end
        return chunk, has_more

    # ── búsqueda ─────────────────────────────────────────────────────────

    def search(self, query: str) -> list[dict]:
        q = (query or "").strip()
        if not q:
            return []

        # Acepta URL / UUID / id de serie directamente
        m_de = _DE_RE.search(q) or _UUID_RE.search(q)
        if m_de:
            cid = m_de.group(0) if _UUID_RE.fullmatch(m_de.group(0)) else m_de.group(1)
            it = {
                "kind": "book",
                "id": cid,
                "slug": "de" + cid,
                "title": q,
                "url": f"{BASE_URL}/de{cid}/",
                "trial": True,
            }
            return self._enrich_book_item(it)
        m_ser = _SERIES_RE.search(q)
        if m_ser:
            it = {
                "kind": "series",
                "id": str(int(m_ser.group(1))),
                "slug": f"series/{int(m_ser.group(1))}",
                "title": q,
                "url": f"{BASE_URL}/series/{int(m_ser.group(1))}/list/",
            }
            return [it]

        items, _has_more, _total = self._fetch_search_page(word=q, order="rank", page=1)
        return items

    def _enrich_book_item(self, it: dict) -> list[dict]:
        """Rellena el título real y la portada del/los item(s) del libro."""
        try:
            r = self._get(it["url"])
            if r is None:
                return [it]
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(r.text, "lxml")
            h1 = soup.select_one("h1.t-c-product-main-data__title")
            if h1 is not None and h1.get_text(strip=True):
                it["title"] = h1.get_text(strip=True)
            if not it.get("cover"):
                og = soup.find("meta", attrs={"property": "og:image"})
                if og and og.get("content"):
                    it["cover"] = og["content"]
        except Exception:
            pass
        return [it]

    # ── ficha de serie / libro ───────────────────────────────────────────

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        slug = str(item.get("slug") or "")

        # Libro directo (de{cid})
        if slug.startswith("de") and len(slug) > 2:
            return self._book_series(slug[2:], item)

        iid = str(item.get("id", ""))
        m = _UUID_RE.fullmatch(iid) or _UUID_RE.search(slug)
        if m:
            cid = m.group(0)
            return self._book_series(cid, item)

        if slug.startswith("series/") or iid.isdigit():
            sid = slug.split("/")[1] if slug.startswith("series/") else iid
            return self._series_from_id(sid, item)

        # Fallback: intenta extraer cid/series de la URL
        url = str(item.get("url") or "")
        m_de = _DE_RE.search(url)
        if m_de:
            return self._book_series(m_de.group(1), item)
        m_ser = _SERIES_RE.search(url)
        if m_ser:
            return self._series_from_id(m_ser.group(1), item)
        return {}, []

    def _book_series(self, cid: str, item: dict) -> tuple[dict, list[dict]]:
        title = str(item.get("title") or "").strip()
        cover = str(item.get("cover") or "")
        meta: dict[str, str] = {}
        extras: dict = {}

        try:
            r = self._get(f"{BASE_URL}/de{cid}/")
            if r is not None:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(r.text, "lxml")
                h1 = soup.select_one("h1.t-c-product-main-data__title")
                if h1 is not None and h1.get_text(strip=True):
                    title = h1.get_text(strip=True)
                auth = soup.select_one("dl.t-c-product-main-data__authors")
                if auth is not None and auth.get_text(strip=True):
                    meta["Autor"] = " ".join(auth.get_text(" ", strip=True).split())
                extras = extract_series_extras(soup, BASE_URL)
                if not cover and extras.get("cover"):
                    cover = extras["cover"]
        except Exception:
            pass

        trial_res = self._trial_resolve(cid)
        if trial_res is None and self._ensure_cookies():
            # Sin muestra gratuita: si el tomo es del usuario, se sirve completo
            trial_res = self._member_resolve(cid)
        series = {
            "id": cid,
            "slug": "de" + cid,
            "title": title or f"bookwalker {cid[:8]}",
            "url": f"{BASE_URL}/de{cid}/",
        }
        if cover:
            series["cover"] = cover
        if extras.get("tags"):
            series["tags"] = extras["tags"]
        if extras.get("meta"):
            meta.update(extras["meta"])
        if meta:
            series["meta"] = meta
        if trial_res is None:
            # No hay muestra gratuita disponible (requiere cuenta para este tomo)
            series["meta"] = {**(series.get("meta") or {}), "Estado": "Sin muestra — requiere cuenta"}
            return series, []

        chapters = [
            {
                "id": cid,
                "cid": cid,
                "title": series["title"],
                "url": f"{BASE_URL}/de{cid}/",
                "trial": True,
            }
        ]
        return series, chapters

    def _series_from_id(self, sid: str, item: dict) -> tuple[dict, list[dict]]:
        cover = str(item.get("cover") or "")
        extras: dict = {}
        chapters: list[dict] = []
        authors: set[str] = set()
        tags: set[str] = set()
        first_vol_title = ""

        try:
            r = self._get(f"{BASE_URL}/series/{sid}/list/")
            if r is None:
                return {}, []
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(r.text, "lxml")
            h1 = soup.select_one(".o-contents-section__title")
            item_title = str(item.get("title") or "").strip()
            if item_title:
                series_title = item_title
            elif h1 is not None:
                series_title = _clean_series_title(h1.get_text(strip=True))
            else:
                series_title = ""
            extras = extract_series_extras(soup, BASE_URL)
            if not cover and extras.get("cover"):
                cover = extras["cover"]

            for tile in soup.select("li.m-tile"):
                it = self._tile_item(tile)
                if it is None or it["kind"] != "book":
                    continue
                cid = it["id"]
                vol_title = it.get("title") or first_vol_title
                if not first_vol_title:
                    first_vol_title = vol_title
                chapters.append(
                    {
                        "id": cid,
                        "cid": cid,
                        "title": vol_title,
                        "url": it.get("url"),
                        "trial": bool(it.get("trial")),
                    }
                )
                if not cover and it.get("cover"):
                    cover = it["cover"]
                author_el = tile.select_one(".m-book-item__author")
                if author_el is not None:
                    links = [a.get_text(" ", strip=True) for a in author_el.select("a")]
                    if links:
                        authors.update(l for l in links if l)
                    else:
                        txt = re.sub(
                            r"^(著|著者):?\s*", "",
                            author_el.get_text(" ", strip=True),
                        )
                        if txt:
                            authors.add(txt)
                for sp in tile.select(".m-book-item__tag-box span"):
                    t = sp.get_text(strip=True)
                    if t:
                        tags.add(t)
        except Exception:
            return {}, []

        if not chapters:
            return {}, []

        series = {
            "id": sid,
            "slug": f"series/{sid}",
            "title": str(item.get("title") or "").strip() or first_vol_title,
            "url": f"{BASE_URL}/series/{sid}/list/",
        }
        if cover:
            series["cover"] = cover
        meta: dict[str, str] = {}
        if authors:
            meta["Autor"] = ", ".join(sorted(authors))
        if tags:
            series["tags"] = sorted(tags)
        if extras.get("meta"):
            meta.update(extras["meta"])
        if meta:
            series["meta"] = meta
        return series, chapters

    # ── descarga ─────────────────────────────────────────────────────────

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        cid = (
            chapter.get("cid")
            or chapter.get("id")
            or series.get("id")
            or (str(series.get("slug", "")).removeprefix("de"))
        )
        if not cid:
            return []
        if not chapter.get("trial", True) and not self._ensure_cookies():
            # Tomo sin muestra gratuita y sin cuenta vinculada
            return []

        res = None
        if self._logged:
            # Con cuenta, prefiere el tomo COMPLETO de `member`
            res = self._member_resolve(cid)
        if res is None:
            res = self._trial_resolve(cid)
        if res is None:
            return []
        cfg = self._configuration(res)
        if cfg is None:
            return []
        return self._build_page_urls(res, cfg)

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        for _ in range(3):
            try:
                r = self._sess.get(
                    url,
                    headers={"Referer": referer or TRIAL_REFERER},
                    timeout=CFG.get("timeout", (15, 45)),
                )
                if r.status_code == 200 and r.content:
                    return r.content
                if r.status_code in (403, 404, 410):
                    return None
            except Exception:
                pass
            time.sleep(CFG.get("retry_delay", 2.0))
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        return FILE_REFERER

    def get_image_count(self, chapter: dict, series: dict) -> Optional[int]:
        try:
            return len(self.get_chapter_images(chapter, series))
        except Exception:
            return None

    # ── cuenta (etapa 2: tomos comprados) ────────────────────────────────

    def login(self, **kwargs) -> bool:
        """Inyecta y persiste cookies de sesión de bookwalker.jp (p.ej. `bwmember=…`).

        cookies: str 'k=v; k2=v2' o dict, obtenidas del navegador logueado en
        https://member.bookwalker.jp/ (Google-linked no usa credenciales).
        Se guardan en bookwalker_cookies.txt; con cookies vacías se limpian.
        """
        cookies = kwargs.get("cookies")
        clear = kwargs.get("clear", False)
        if clear or (cookies is not None and not str(cookies).strip()):
            self._logged = False
            save_site_cookies("")
            return True
        if isinstance(cookies, str) and cookies.strip():
            self._inject_cookies(cookies)
            save_site_cookies(cookies)
            return True
        if isinstance(cookies, dict) and cookies:
            self._inject_cookies(cookies)
            if cookies:
                save_site_cookies("; ".join(f"{k}={v}" for k, v in cookies.items()))
            return True
        return False


# ═══════════════════════ DownloaderBookwalkerHar (member/HAR) ═══════════════════════

class DownloaderBookwalkerHar(DownloaderBookwalker):
    NAME = "BOOKWALKER (bookwalker.jp)"
    HAS_SEARCH = True
    HAS_CATALOG = True
    NEEDS_LOGIN = False
    HAR_SESSION = True

    def __init__(self) -> None:
        super().__init__()
        self._active: Optional[dict] = None
        self._last_chapter: Optional[dict] = None
        self._last_series: Optional[dict] = None

    # ── sesión automática (navegador gestionado) ─────────────────────────────

    def login_via_browser(self) -> bool:
        """Toma la sesión desde el navegador REAL del usuario (Chrome/Edge).

        Abre el perfil real donde ya está logueada su cuenta una sola vez
        (si está abierto, pedirá cerrarlo). Persiste la SESSION y las cookies
        del visor en `bw_session.bookwalker_session.json`; después todo se
        reusa con `requests`. True si quedó una SESSION activa.
        """
        try:
            import bw_session as _s
        except Exception:
            return False
        if not _s.importable():
            return False
        try:
            sess = _s.capture_login()
        except Exception:
            return False
        finally:
            try:
                _s.close()
            except Exception:
                pass
        return bool(sess and sess.get("sid"))

    def capture_via_browser(self, cid: str) -> bool:
        """Captura automática de un tomo member SIN pegar cURL.

        Abre el visor del cid en el navegador REAL del usuario (donde su
        cuenta ya está logueada) y, cuando dispara el `/c` (one-shot), guarda
        la captura HAR como si viniera de un cURL pegado: base + auth_info
        (firma ~1h) + cookies, y re-deriva los tokens desde
        configuration_pack.json headless.
        """
        if not _UUID_RE.fullmatch(cid):
            return False
        try:
            import bw_session as _s
        except Exception:
            return False
        if not _s.importable():
            return False
        try:
            data = _s.capture_c_for_cid(cid)
        except Exception:
            return False
        finally:
            try:
                _s.close()
            except Exception:
                pass
        if not data:
            return False
        base = str(data.get("url") or "").rstrip("/")
        info = data.get("auth_info") or {}
        if not base or not info:
            return False
        img_base = base + "/OEBPS/text/"
        query = urlencode(
            {k: str(info[k]) for k in _AUTH_KEYS if info.get(k) is not None and str(info[k]) != ""}
        )
        headers: Dict[str, str] = {
            "User-Agent": str(data.get("ua") or "") or _DEFAULT_UA,
            "Referer": str(data.get("referer") or "").strip() or _DEFAULT_REFERER,
        }
        if data.get("cookie"):
            headers["Cookie"] = str(data["cookie"])

        auto = self._derive_tokens_auto(base, query, headers)

        caps = load_har_captures()
        cap = caps.get(cid, {"cid": cid, "created_at": time.time()})
        cap["session"] = {
            "flow": "c",
            "img_base": img_base,
            "query": query,
            "cookie": str(data.get("cookie") or ""),
            "referer": headers.get("Referer") or _DEFAULT_REFERER,
            "captured_at": time.time(),
        }
        cap["pages_base"] = img_base
        if auto is not None:
            cap["tokens"] = auto["tokens"]
            cap["tokens_from"] = "config"
            cap["config"] = auto["config"]
            cap["keys"] = auto["keys"]
        if not cap.get("title"):
            cap["title"] = f"bookwalker {cid[:8]}"
        caps[cid] = cap
        save_har_captures(caps)
        self._active = cap
        return True

    # ── importación de HAR ────────────────────────────────────────────────────

    def _import_har_text(self, text: str) -> list:
        tokens: Dict[str, str] = {}
        bases: set[str] = set()
        for m in _XHTML_TOKEN_RE.finditer(text or ""):
            page, tok = m.group(1).lower(), m.group(2).lower()
            tokens.setdefault(page, tok)
        for m in _BASE_BEFORE_OEBPS_RE.finditer(text or ""):
            bases.add(m.group(1).rstrip("/") + "/")
        if not tokens:
            return []
        base = min(bases, key=len) if bases else ""
        cid = _extract_cid(text or "", base)
        key = cid or ("gen_" + hashlib.md5("\n".join(sorted(tokens)).encode()).hexdigest()[:10])

        caps = load_har_captures()
        cap = caps.get(key, {"cid": key, "created_at": time.time()})
        cap["tokens"] = tokens
        if base:
            cap["pages_base"] = base
        if not cap.get("title"):
            cap["title"] = f"bookwalker {key[:8]}"
        cap.setdefault("session", None)
        caps[key] = cap
        save_har_captures(caps)
        return [_flow_item(key, _capture_title(cap, key))]

    def _import_curl_c(self, curl: str) -> list:
        """FLUJO A: cURL de browserWebApi/c → auth_info, base y tokens de
        configuration_pack.json descifrado (ya no hace falta el HAR)."""
        parsed = _parse_curl_request(curl)
        url = parsed["url"]
        if "/browserWebApi/c" not in url:
            return []
        qs = dict(parse_qsl(url.split("?", 1)[1] if "?" in url else ""))
        cid = str(qs.get("cid") or _extract_cid(url) or "").lower()

        headers = {"User-Agent": _DEFAULT_UA, "Referer": _DEFAULT_REFERER}
        if parsed["cookie"]:
            headers["Cookie"] = parsed["cookie"]
        for k in ("user-agent", "referer"):
            v = parsed["headers"].get(k)
            if v:
                headers["User-Agent" if k == "user-agent" else "Referer"] = v

        try:
            r = self._sess.get(url, headers=headers, timeout=CFG.get("timeout", (15, 45)))
        except Exception:
            return []
        try:
            d = r.json()
        except Exception:
            return []
        if not isinstance(d, dict) or str(d.get("status")) != "200" or not d.get("url"):
            return []

        info = d.get("auth_info") or {}
        query = urlencode(
            {k: str(info[k]) for k in _AUTH_KEYS if info.get(k) is not None and str(info[k]) != ""}
        )
        base = str(d["url"]).rstrip("/")
        img_base = base + "/OEBPS/text/"

        caps = load_har_captures()
        key = _pick_capture(caps, cid)
        if key is None:
            key = cid or "gen_" + hashlib.md5(img_base.encode()).hexdigest()[:10]
        cap = caps.get(key, {"cid": key, "created_at": time.time()})
        cap.setdefault("tokens", {})
        cap["source"] = curl
        cap["session"] = {
            "flow": "c",
            "img_base": img_base,
            "query": query,
            "cookie": parsed["cookie"],
            "referer": headers.get("Referer") or _DEFAULT_REFERER,
            "captured_at": time.time(),
        }

        # Tokens automáticos: bajar y descifrar configuration_pack.json
        auto = self._derive_tokens_auto(base, query, headers)
        if auto is not None:
            cap["tokens"] = auto["tokens"]
            cap["tokens_from"] = "config"
            cap["config"] = auto["config"]
            cap["keys"] = auto["keys"]

        if not cap.get("title"):
            cap["title"] = f"bookwalker {(cid or key)[:8]}"
        caps[key] = cap
        save_har_captures(caps)
        return [_flow_item(key, _capture_title(cap, key))]

    def _derive_tokens_auto(self, base: str, query: str, headers: dict) -> Optional[dict]:
        """Baja configuration_pack.json, lo descifra y saca los tokens por
        página. Devuelve {tokens: {fid: token}, config, keys} o None si algo
        falla. config y keys se guardan en la captura para poder calcular las
        semillas de de-scramble offline."""
        cfg_url = f"{base}/configuration_pack.json?{query}"
        try:
            r = self._sess.get(
                cfg_url, headers=headers, timeout=CFG.get("timeout", (15, 60))
            )
            if r.status_code != 200:
                return None
            cfg, k1, k2, k3 = decrypt_config(r.text)
        except Exception:
            return None
        if not cfg:
            return None
        tokens = build_tokens(cfg, k1, k2, k3)
        # Las claves de build_tokens son "OEBPS/text/p-001.xhtml"; aquí se
        # guardan como basename "p-001.xhtml", igual que vienen del HAR
        return {
            "tokens": {fid.rsplit("/", 1)[-1]: tok for fid, tok in tokens.items()},
            "config": cfg,
            "keys": [k1, k2, k3],
        }

    def _import_curl_pages(self, curl: str) -> list:
        """FLUJO B: cURL de una imagen 'pages' del HAR → headers + base."""
        parsed = _parse_curl_request(curl)
        url = parsed["url"]
        m = _BASE_BEFORE_OEBPS_RE.search(url)
        if not m:
            return []
        img_base = m.group(1).rstrip("/") + "/"
        cid = _extract_cid(url, img_base)

        headers: Dict[str, str] = {"User-Agent": _DEFAULT_UA, "Referer": _DEFAULT_REFERER}
        if parsed["cookie"]:
            headers["Cookie"] = parsed["cookie"]
        for k in ("user-agent", "referer"):
            v = parsed["headers"].get(k)
            if v:
                headers["User-Agent" if k == "user-agent" else "Referer"] = v

        caps = load_har_captures()
        key = _pick_capture(caps, cid)
        if key is None:
            key = cid or "pages_" + hashlib.md5(img_base.encode()).hexdigest()[:10]
        cap = caps.get(key, {"cid": key, "created_at": time.time()})
        cap.setdefault("tokens", {})
        cap["session"] = {
            "flow": "pages",
            "img_base": img_base,
            "headers": headers,
            "captured_at": time.time(),
        }
        cap["pages_base"] = img_base
        if not cap.get("title"):
            cap["title"] = f"bookwalker {(cid or key)[:8]}"
        caps[key] = cap
        save_har_captures(caps)
        return [_flow_item(key, _capture_title(cap, key))]

    # ── interfaz BaseDownloader ───────────────────────────────────────────────

    def search(self, query: str) -> list:
        q = (query or "").strip()
        if not q:
            return []

        # archivo .har en disco
        if os.path.isfile(q):
            try:
                with open(q, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                return []
            return self._import_har_text(text)

        # cURL de /c → FLUJO A
        if "/browserWebApi/c" in q:
            return self._import_curl_c(q)

        # cURL de una imagen (pages) → FLUJO B
        if "/OEBPS/text/" in q:
            return self._import_curl_pages(q)

        # HAR pegado como texto/JSON
        if q.lstrip().startswith("{"):
            return self._import_har_text(q)

        # Búsqueda web en la tienda + capturas guardadas que coincidan
        web = super().search(q)
        caps = load_har_captures()
        out = list(web)
        for cid, cap in caps.items():
            title = _capture_title(cap, cid)
            if q.lower() in title.lower() or cid.lower().startswith(q.lower()):
                out.append(_flow_item(cid, title))
        return out

    def get_catalog_page(
        self, page: int = 1, page_size: int = 20, **kwargs
    ) -> tuple[list[dict], bool]:
        """Catálogo: ranking de la tienda (páginas) + capturas guardadas al final."""
        items, has_more = super().get_catalog_page(page=page, page_size=page_size, **kwargs)
        caps = load_har_captures()
        if caps:
            mine = [
                _flow_item(cid, _capture_title(cap, cid))
                for cid, cap in sorted(
                    caps.items(), key=lambda kv: str(kv[1].get("created_at") or ""), reverse=True
                )
            ]
            all_items = [i for i in items if i.get("kind") != "capture"] + mine
            start = (page - 1) * page_size
            chunk = all_items[start : start + page_size]
            has_more = (has_more and len(items) >= page_size) or start + page_size < len(all_items)
            return chunk, has_more
        return items, has_more

    def get_catalog(self, **kwargs) -> list:
        caps = load_har_captures()
        return [_flow_item(cid, _capture_title(cap, cid)) for cid, cap in caps.items()]

    def get_series(self, item: dict) -> tuple[dict, list[dict]]:
        if item.get("kind") == "capture":
            return self._capture_series(item)
        if item.get("kind") in ("book", "series") or str(item.get("slug", "")).startswith(
            ("de", "series/")
        ):
            return super().get_series(item)
        return self._capture_series(item)

    def _capture_series(self, item: dict) -> tuple[dict, list[dict]]:
        cid = str(item.get("cid") or item.get("id") or "").lower()
        caps = load_har_captures()
        cap = caps.get(cid)
        if not cap:
            return {}, []
        title = _capture_title(cap, cid)
        series = {
            "id": cid,
            "slug": cid,
            "title": title,
            "url": f"https://bookwalker.jp/de{cid}/",
        }
        tokens = cap.get("tokens") or {}
        meta = {"Páginas": str(len(tokens))}
        fuente = cap.get("tokens_from")
        if fuente == "config":
            meta["Tokens"] = "derivados (config descifrado — sin HAR)"
        elif tokens:
            meta["Tokens"] = "extraídos del HAR"
        has_session = bool(cap.get("session"))
        meta["Estado"] = "Listo" if has_session else "Sin sesión — pega el cURL /c o de pages"
        if _HAS_UNSCRAMBLE and cap.get("config") and len(cap.get("keys") or []) == 3:
            meta["Imágenes"] = "de-scrambleadas (config+keys)"
        series["meta"] = meta
        chapter = {
            "id": cid,
            "cid": cid,
            "title": title,
            "url": series["url"],
        }
        return series, [chapter]

    def get_chapter_images(self, chapter: dict, series: dict) -> list[str]:
        cid = str(
            chapter.get("cid")
            or chapter.get("id")
            or series.get("id")
            or (str(series.get("slug", "")).removeprefix("de"))
        ).lower()
        cap = load_har_captures().get(cid)
        if cap and cap.get("session"):
            return self._har_chapter_images(chapter, series, cap)
        # Sin captura member: flujo trial/member web del sitio (heredado)
        return super().get_chapter_images(chapter, series)

    def _har_chapter_images(
        self, chapter: dict, series: dict, cap: dict
    ) -> list[str]:
        tokens = cap.get("tokens") or {}
        session = cap.get("session")
        if not session:
            return []
        self._last_chapter = chapter
        self._last_series = series

        self._active = cap
        self._seeds: Optional[dict] = None
        self._seeds_by_base: Dict[str, dict] = {}
        if _HAS_UNSCRAMBLE and cap.get("config") and cap.get("keys"):
            ks = cap.get("keys") or []
            if len(ks) == 3:
                try:
                    self._seeds = build_seeds_for_book(cap["config"], ks[0], ks[1], ks[2])
                    for fid, s in (self._seeds or {}).items():
                        self._seeds_by_base[fid.rsplit("/", 1)[-1]] = s
                except Exception:
                    self._seeds = None
        img_base = str(session.get("img_base") or "").rstrip("/") + "/"
        query = str(session.get("query") or "")
        urls: list[str] = []
        for page in sorted(tokens, key=_sort_key):
            tok = str(tokens[page])
            suffix = ".jpegbvCoverImage" if page == "p-cover.xhtml" else ".jpeg"
            u = f"{img_base}{page}/{tok}{suffix}"
            if query:
                u += "?" + query
            urls.append(u)
        return urls

    def _policy_expired(self) -> bool:
        """True si la firma firmada (Policy AWS) de la sesión activa ya expiró.
        Se usa para abortar limpio en vez de reintentar 5×167 imágenes."""
        cap = self._active or {}
        sess = cap.get("session") or {}
        policy = quota = ""
        for k, v in parse_qsl(str(sess.get("query") or "")):
            if k == "Policy":
                policy = v
            elif k == "Key-Pair-Id":
                quota = "key=" + v
        if not policy:
            return False
        try:
            dec = base64.b64decode(policy + "===").decode("utf-8", "replace")
        except Exception:
            return False
        m = re.search(r'"AWS:EpochTime":\s*(\d+)', dec)
        if not m:
            return False
        exp = int(m.group(1))
        now = int(time.time())
        # un margen de 30s menos por si el reloj del cliente se adelanta
        return now >= exp - 30

    _PAGE_SEED_RE = re.compile(r"/((?:p-[a-z0-9_-]+)\.xhtml)/", re.I)

    def _unscramble_bytes(self, url: str, raw: bytes) -> bytes:
        """Si la captura lleva config+keys (y la URL es de una pagina del
        tomo), recompone la imagen scrambleada del visor a su disposicion
        original y recorta al Size declarado. Devuelve el JPEG de-scrambleado
        (calidad 92) con el mismo re-encode que la herramienta standalone;
        si no aplica, devuelve `raw` intacto."""
        seed = None
        if _HAS_UNSCRAMBLE:
            m = self._PAGE_SEED_RE.search(url or "")
            if m:
                seed = self._seeds_by_base.get(m.group(1).lower())
        if not seed or not seed.get("Size"):
            return raw
        try:
            import io as _io
            import PIL.Image as _PILImage
            img = _PILImage.open(_io.BytesIO(raw))
            w, h = img.size
            rgba = img.convert("RGBA")
            if seed.get("Scrambled"):
                out = unscramble(rgba.tobytes(), w, h, seed)
                dec = _PILImage.frombytes("RGBA", (w, h), bytes(out)).convert("RGB")
            else:
                dec = rgba.convert("RGB")  # portada: llega sin scramble
            size = seed.get("Size") or {}
            sw, sh = size.get("Width"), size.get("Height")
            if sw and sh:
                sw, sh = min(int(sw), w), min(int(sh), h)
                if (sw, sh) != (w, h):
                    dec = dec.crop((0, 0, min(sw, w), min(sh, h)))
            buf = _io.BytesIO()
            dec.save(buf, format="JPEG", quality=92)
            return buf.getvalue()
        except Exception:
            return raw

    def dl_image(self, url: str, referer: str = "") -> Optional[bytes]:
        cap = self._active or {}
        if cap.get("session"):
            return self._dl_member_image(url, referer, cap)
        return super().dl_image(url, referer)

    def _dl_member_image(
        self, url: str, referer: str, cap: Optional[dict] = None
    ) -> Optional[bytes]:
        cap = cap or self._active or {}
        session = cap.get("session") or {}
        headers: Dict[str, str] = {}
        if session.get("flow") == "pages":
            headers = dict(session.get("headers") or {})
        if session.get("cookie") and "cookie" not in {k.lower() for k in headers}:
            headers["Cookie"] = session["cookie"]
        headers.setdefault("User-Agent", _DEFAULT_UA)
        headers.setdefault("Referer", session.get("referer") or referer or _DEFAULT_REFERER)
        headers.setdefault("Accept", "image/avif,image/webp,image/jpeg,*/*")

        expired = self._policy_expired()
        for attempt in range(5):
            if attempt:
                if expired:
                    break  # firma vencida: no tiene sentido reintentar
                time.sleep(8 + attempt * 6)  # cooldown tras 403/429 del CDN
            try:
                r = self._sess.get(
                    url, headers=headers, timeout=CFG.get("timeout", (15, 45))
                )
                if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                    return self._unscramble_bytes(url, r.content)
                if r.status_code in (403, 429):
                    if r.status_code == 403:
                        expired = self._policy_expired()
                    continue
                if r.status_code in (404, 410):
                    return None
            except Exception:
                pass
        return None

    def get_referer(self, chapter: dict, series: dict) -> str:
        cap = self._active or {}
        if cap.get("session"):
            return _DEFAULT_REFERER
        return super().get_referer(chapter, series)

    def dl_batch(
        self, urls: list[str], referer: str = "", max_workers: int = 8
    ) -> List[Optional[bytes]]:
        """Descarga varias imágenes. En flujo "c" (member, firma /c one-shot)
        baja por tramos y re-importa el /c cada ~25 páginas porque la firma
        expira a mitad de tomo; en el resto usa paralelismo simple."""
        if self._active and (self._active.get("session") or {}).get("flow") == "c" and self._active.get("source"):
            return self._dl_batch_member_tramos(urls, referer, CHUNK=25, MAX_REIMPORT=4)
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return list(pool.map(lambda u: self.dl_image(u, referer), urls))

    def _dl_batch_member_tramos(
        self, urls: list[str], referer: str, CHUNK: int = 25, MAX_REIMPORT: int = 4
    ) -> List[Optional[bytes]]:
        """Porta el fix del 403: la firma /c (one-shot) del flujo member expira
        a mitad de la descarga (~39 págs). Se descarga por tramos y cuando un
        tramo falla, re-importa el /c guardado en la captura para renovar la
        firma y reintenta las imágenes pendientes."""
        cap = self._active
        source = (cap or {}).get("source")
        ch = getattr(self, "_last_chapter", None) or {}
        sv = getattr(self, "_last_series", None) or {}
        out: List[Optional[bytes]] = [None] * len(urls)

        def import_fresh_urls():
            nonlocal urls
            if not source or not ch:
                return False
            try:
                if self._import_curl_c(source):
                    fresh = self.get_chapter_images(ch, sv)
                    if len(fresh) == len(urls):
                        urls = fresh
                        return True
            except Exception:
                pass
            return False

        done = [False] * len(urls)
        for start in range(0, len(urls), CHUNK):
            end = min(start + CHUNK, len(urls))
            pending = [i for i in range(start, end) if not done[i]]
            attempts = 0
            while pending and attempts < MAX_REIMPORT:
                for i in list(pending):
                    raw = self.dl_image(urls[i], referer)
                    if raw and raw[:2] == b"\xff\xd8":
                        out[i] = raw
                        done[i] = True
                pending = [i for i in pending if not done[i]]
                if pending and import_fresh_urls():
                    attempts += 1
                else:
                    break
        return out


__all__ = [
    "DownloaderBookwalker",
    "DownloaderBookwalkerHar",
    "decrypt_config",
    "page_token",
    "build_tokens",
    "build_urls",
    "build_seeds_for_book",
    "unscramble",
    "a3f",
    "a9p_move_list",
    "page_seeds",
    "load_site_cookies",
    "save_site_cookies",
    "load_member_captures",
    "save_member_captures",
    "clear_member_captures",
    "load_har_captures",
    "save_har_captures",
    "clear_har_captures",
    "import_bookwalker_curl",
]