"""
d_bookwalker_unscramble.py — de-scramble de imágenes BOOKWALKER member.

Port a Python de bookworm (aaa4xu, GPL) / bookwalker-native-downloader:
las páginas se sirven divididas en bloques de 32x32 px desordenados con una
permutación determinista; el visor las reensambla al pintarlas en un canvas.

  - B2y  : PRNG xorshift de 32 bits con tablas (v_cgh/v_dgh).
  - a3f  : deriva la permutación (latin square + Fisher-Yates) con 4 seeds.
  - A9p  : convierte la permutación en lista de movimientos (srcX,srcY,..).
  - page_seeds(): semillas por página desde pageId+No+NS/PS/RS+keys (Page.ts).
  - unscramble(): aplica los movimientos a los píxeles (RGBA).

Validado byte a byte contra los fixtures de bookworm (A9p-001.json y
image-001-*.png, uso individual/Solo lectura).
"""

from __future__ import annotations

import json
from typing import List, Tuple

# ── B2y: PRNG ────────────────────────────────────────────────────────────────

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


# ── a3f: permutación ─────────────────────────────────────────────────────────

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


# ── A9p: lista de movimientos de bloques ─────────────────────────────────────

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


# ── semillas por página (Page.ts) ────────────────────────────────────────────

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


def page_seeds(page_id: str, no: int, ns: int, ps: int, rs: int,
               block_width: int, block_height: int,
               k1: List[int], k2: List[int], k3: List[int]) -> dict:
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


def build_seeds_for_book(config: dict, k1: List[int], k2: List[int], k3: List[int]) -> dict:
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
            pl.get("BlockWidth") and pl.get("BlockHeight")
            and "NS" in pl and "PS" in pl and "RS" in pl
        )
        seeds = page_seeds(
            fid,
            pl.get("No", 0),
            pl.get("NS", 0),
            pl.get("PS", 0),
            pl.get("RS", 0),
            pl.get("BlockWidth", 32),
            pl.get("BlockHeight", 32),
            k1, k2, k3,
        )
        seeds["Size"] = pl.get("Size") or {}
        seeds["Scrambled"] = scrambled
        out[fid] = seeds
    return out


# ── aplicar el de-scramble a un buffer RGBA ──────────────────────────────────

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