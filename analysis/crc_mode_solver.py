"""
Brute-force helper pro CRC u MODE (Setting) frame.

Hledá shodu mezi uloženými MODE framy z `logs/payloads_boiler.db` a známými CRC16
preset parametry (poly/init/refin/refout/xorout) přes různé vstupní reprezentace.

Poznámka: Nepokrývá plný prostor 16bit CRC (to by bylo ~miliony kombinací);
zaměřuje se na standardní polynomy. Slouží jako rychlý sanity-check.
"""
# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,too-many-locals,too-many-branches,dangerous-default-value,too-many-nested-blocks,too-many-statements,too-many-return-statements,invalid-name,line-too-long,duplicate-value,broad-exception-caught,too-many-arguments,too-many-positional-arguments,unused-import

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from itertools import product
from typing import Iterable, List, Sequence

DB_PATH = "logs/payloads_boiler.db"


@dataclass
class CRCParams:
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int


def reflect_bits(value: int, bits: int) -> int:
    """Bit reverse helper."""
    return int(f"{value:0{bits}b}"[::-1], 2)


def crc16(
    data: bytes, params: CRCParams, width: int = 16, table: list[int] | None = None
) -> int:
    """Byte-wise CRC16, optionally using 256-entry table for speed."""
    poly = params.poly & 0xFFFF
    init = params.init & 0xFFFF
    xorout = params.xorout & 0xFFFF
    refin = params.refin
    refout = params.refout

    # Build table per params if provided table container is empty
    if table is not None and not table:
        for byte in range(256):
            cur = byte
            if refin:
                cur = reflect_bits(cur, 8)
            cur <<= width - 8
            for _ in range(8):
                if cur & (1 << (width - 1)):
                    cur = ((cur << 1) ^ poly) & 0xFFFF
                else:
                    cur = (cur << 1) & 0xFFFF
            table.append(cur)

    crc_val = init
    if table:
        top = 1 << (width - 1)
        mask = 0xFFFF
        for byte in data:
            b = reflect_bits(byte, 8) if refin else byte
            idx = ((crc_val >> (width - 8)) ^ b) & 0xFF
            crc_val = ((crc_val << 8) ^ table[idx]) & mask
    else:
        top = 1 << (width - 1)
        mask = 0xFFFF
        for byte in data:
            b = reflect_bits(byte, 8) if refin else byte
            crc_val ^= b << (width - 8)
            for _ in range(8):
                if crc_val & top:
                    crc_val = ((crc_val << 1) ^ poly) & mask
                else:
                    crc_val = (crc_val << 1) & mask

    if refout:
        crc_val = reflect_bits(crc_val, width)
    return (crc_val ^ xorout) & 0xFFFF


# Rozšířený seznam známých CRC16 polynomů (viz crccatalog)
KNOWN_POLYS = sorted(
    {
        0x1021,
        0x8005,
        0xA001,
        0x8408,
        0x3D65,
        0x2F15,
        0x8BB7,
        0xA6BC,
        0xC867,
        0x9EB2,
        0x3D9D,
        0x755B,
        0x5935,
        0x1D0F,
        0x0589,
        0x3D95,
        0xAC9A,
        0x1021 ^ 0xFFFF,  # občas zapisované inverzně
        0xC599,  # CRC16/OPENSAFETY-A
        0x755B,  # CRC16/OPENSAFETY-B
        0xA097,  # CRC16/MODBUS inverted
        0x1EDC,  # CRC16/DNP inverted
    }
)

# Možné init/xor kombinace – rozumné předvolby (použité jen v rychlém módu)
INIT_CANDIDATES = [0x0000, 0xFFFF, 0x1D0F, 0x3692]
XOR_CANDIDATES = [0x0000, 0xFFFF]
REF_FLAGS = list(product((False, True), repeat=2))  # (refin, refout)


def load_mode_frames(db_path: str) -> list[tuple[bytes, int]]:
    """Načte MODE setting framy a vrátí (payload_bez_crc, target_crc)."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT raw FROM frames WHERE raw LIKE '%<TblItem>MODE%' AND raw LIKE '%<CRC>%'"
    ).fetchall()
    frames: list[tuple[bytes, int]] = []
    for (raw,) in rows:
        try:
            root = ET.fromstring(raw)
        except Exception:
            continue
        crc_text = root.findtext("CRC")
        if not (crc_text and crc_text.isdigit()):
            continue
        payload = re.sub(r"<CRC>\\d+</CRC>", "", raw).encode()
        frames.append((payload, int(crc_text)))
    return frames


def search_crc(
    frames: Sequence[tuple[bytes, int]],
    polys: Iterable[int] = KNOWN_POLYS,
    inits: Iterable[int] = INIT_CANDIDATES,
    xors: Iterable[int] = XOR_CANDIDATES,
    ref_flags: Iterable[tuple[bool, bool]] = REF_FLAGS,
    max_frames: int | None = None,
) -> list[CRCParams]:
    """Prohledá daný parametrický prostor a vrátí shody."""
    subset = frames if max_frames is None else frames[:max_frames]
    matches: list[CRCParams] = []
    for poly in polys:
        for init in inits:
            for xorout in xors:
                for refin, refout in ref_flags:
                    params = CRCParams(poly=poly, init=init, refin=refin, refout=refout, xorout=xorout)
                    table: list[int] = []
                    ok = True
                    for data, target in subset:
                        if crc16(data, params, table=table) != target:
                            ok = False
                            break
                    if ok:
                        matches.append(params)
    return matches


def compute_columns(
    data: bytes, params: CRCParams, table: list[int] | None = None
) -> tuple[int, list[int]]:
    """Vrátí (base_crc s init=0,xorout=0, sloupce vlivu jednotlivých init bitů)."""
    cols_table: list[int] = table if table is not None else []
    base = crc16(
        data, CRCParams(params.poly, 0, params.refin, params.refout, 0), table=cols_table
    )
    cols: list[int] = []
    for bit in range(16):
        val = crc16(
            data,
            CRCParams(params.poly, 1 << bit, params.refin, params.refout, 0),
            table=cols_table,
        )
        cols.append(val ^ base)  # vliv bitu init
    return base, cols


def solve_init_xor(
    frames: Sequence[tuple[bytes, int]],
    params: CRCParams,
    use_frames: int = 3,
) -> tuple[bool, int, int]:
    """
    Řeší lineární systém pro init a xorout (oba 16bit) při fixním poly/refin/refout.
    Vrací (found, init, xorout). Pokud není řešení, found=False.
    """
    subset = frames[:use_frames]
    rows: list[list[int]] = []
    rhs: list[int] = []
    shared_table: list[int] = []
    for data, target in subset:
        base, cols = compute_columns(data, params, table=shared_table)
        for k in range(16):
            row = [0] * 32  # 16 init bitů + 16 xorout bitů
            for j in range(16):
                if (cols[j] >> k) & 1:
                    row[j] = 1
            row[16 + k] = 1  # xorout bit
            rows.append(row)
            rhs.append(((target >> k) & 1) ^ ((base >> k) & 1))
    # Gauss mod 2
    m = len(rows)
    n = 32
    A = [row[:] for row in rows]
    B = rhs[:]
    r = 0
    pivots = [-1] * n
    for c in range(n):
        pivot = None
        for i in range(r, m):
            if A[i][c]:
                pivot = i
                break
        if pivot is None:
            continue
        A[r], A[pivot] = A[pivot], A[r]
        B[r], B[pivot] = B[pivot], B[r]
        for i in range(m):
            if i != r and A[i][c]:
                for j in range(c, n):
                    A[i][j] ^= A[r][j]
                B[i] ^= B[r]
        pivots[c] = r
        r += 1
        if r == m:
            break
    # Check consistency
    for i in range(r, m):
        if all(v == 0 for v in A[i]) and B[i]:
            return False, 0, 0
    # Back substitute (free vars = 0)
    sol = [0] * n
    for c in range(n - 1, -1, -1):
        r_idx = pivots[c]
        if r_idx == -1:
            continue
        val = B[r_idx]
        for j in range(c + 1, n):
            if A[r_idx][j]:
                val ^= sol[j]
        sol[c] = val
    init = sum(sol[i] << i for i in range(16))
    xorout = sum(sol[16 + i] << i for i in range(16))
    return True, init, xorout


def search_crc_full(frames: Sequence[tuple[bytes, int]]) -> list[CRCParams]:
    """Brute-force všech lichých polynomů s linear-solve pro init/xorout."""
    matches: list[CRCParams] = []
    quick_check = frames[:5]
    total = 0
    for poly in range(1, 0x10000, 2):
        for refin, refout in REF_FLAGS:
            base_params = CRCParams(poly=poly, init=0, refin=refin, refout=refout, xorout=0)
            found, init, xorout = solve_init_xor(frames, base_params, use_frames=3)
            if not found:
                continue
            test_params = CRCParams(poly=poly, init=init, refin=refin, refout=refout, xorout=xorout)
            table: list[int] = []
            if not all(crc16(d, test_params, table=table) == t for d, t in quick_check):
                continue
            if all(crc16(d, test_params, table=table) == t for d, t in frames):
                matches.append(test_params)
        total += 2  # dvě refin kombinace na poly (refin/out iteruje 4×)
        if total % 2000 == 0:
            print(f"Progress poly ~{poly:#06x}, matches so far {len(matches)}")
    return matches


def main() -> None:
    frames = load_mode_frames(DB_PATH)
    print(f"Loaded MODE frames: {len(frames)}")
    if not frames:
        return
    matches = search_crc(frames, max_frames=10)  # nejdřív top 10 pro rychlý check
    print(f"Matches on first 10 frames: {len(matches)}")
    if matches:
        for m in matches:
            print(m)
        # Ověření na všech framech
        full_ok = []
        for m in matches:
            if all(crc16(d, m, table=[]) == t for d, t in frames):
                full_ok.append(m)
        print(f"Matches on ALL frames: {len(full_ok)}")
        for m in full_ok:
            print(m)
    else:
        print("No match found within preset parameter space.")
        print("Running full poly search with linear solve for init/xorout (may take a while)...")
        full_matches = search_crc_full(frames)
        print(f"Full search matches: {len(full_matches)}")
        for m in full_matches:
            print(m)


if __name__ == "__main__":
    main()
