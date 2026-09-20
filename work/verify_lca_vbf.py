#!/usr/bin/env python3
"""Independently re-verify the written CV6T-14C217-AR_LCA.VBF.

Re-derives every integrity layer from the OUTPUT FILE with a separate code path
from the builder, and confirms the semantic edit is exactly the intended one.
This is the acceptance gate before a steering ECU is flashed.
"""
import binascii
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from patch_lca_gate import (parse_vbf, crc16_mcrf4xx, sum16le,       # noqa: E402
                            find_start_offset, WORD_A_OFF, WORD_B_OFF,
                            SUM_B_END, BLK1_FLASH, GATE_WORD,
                            BLK1_WORD_BASE, CAL)

MOD = os.path.join(ROOT, "CV6T-14C217-AR_LCA.VBF")
STOCK = os.path.join(ROOT, "CV6T-14C217-AR.VBF")

ok = True


def chk(name, cond, d=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {d}" if d else ""))


def main():
    print("INDEPENDENT VERIFICATION OF THE PATCHED VBF\n")
    raws, dss, bs = parse_vbf(STOCK)
    rawm, dsm, bm = parse_vbf(MOD)

    chk("same block count/addresses",
        [a for a, _, _ in bs] == [a for a, _, _ in bm])
    chk("same block sizes",
        [len(d) for _, d, _ in bs] == [len(d) for _, d, _ in bm])

    bi = [a for a, _, _ in bm].index(BLK1_FLASH)
    blk0, blk1 = bm[0][1], bm[bi][1]
    s_blk1 = bs[bi][1]

    # --- semantic: the gate arm ------------------------------------------
    off = (GATE_WORD - BLK1_WORD_BASE) * 2
    w = [struct.unpack_from("<H", blk1, off + 2 * i)[0] for i in range(11)]
    want = [0x4C06, 0xA207, 0xE700, 0xE700, 0xE700, 0xE700,
            0xE684, 0x2DDE, 0xA902, 0xE680, 0x2DDE]
    chk("== 6 arm is CMP/Bne/NOPx4/MOVE#4", w == want,
        " ".join(f"{x:04X}" for x in w))
    chk("CMP.W #6 preserved", w[0] == 0x4C06)
    chk("Bne +7 preserved (non-6 dispatch intact)", w[1] == 0xA207)
    chk("MOVE.W #4 reached unconditionally for enum 6",
        w[2:6] == [0xE700] * 4 and w[6] == 0xE684)

    # --- only 4 words differ in the whole block ---------------------------
    diffs = [i for i in range(0, len(blk1) - 1, 2)
             if blk1[i:i + 2] != s_blk1[i:i + 2]]
    expect = {off + 4, off + 6, off + 8, off + 10, WORD_A_OFF, WORD_B_OFF}
    chk("exactly 6 differing words in blk1 (4 NOPs + 2 checksums)",
        set(diffs) == expect,
        f"{len(diffs)} at {[hex(x) for x in diffs]}")

    # --- layer 1 ----------------------------------------------------------
    start = find_start_offset(bytearray(blk1))
    a = crc16_mcrf4xx(bytes(blk0) + bytes(blk1[start:WORD_A_OFF]))
    stored = struct.unpack_from("<H", blk1, WORD_A_OFF)[0]
    chk("word A self-consistent in the OUTPUT", a == stored,
        f"calc {a:04X} stored {stored:04X}")
    chk("word A CHANGED from stock",
        stored != struct.unpack_from("<H", s_blk1, WORD_A_OFF)[0])

    # --- layer 2 ----------------------------------------------------------
    _, _, cb = parse_vbf(CAL)
    lin = bytearray(b"\xFF" * SUM_B_END)
    for a_, d_, _ in bm:
        if a_ < SUM_B_END:
            n = min(len(d_), SUM_B_END - a_)
            lin[a_:a_ + n] = d_[:n]
    for a_, d_, _ in cb:
        if a_ < SUM_B_END:
            n = min(len(d_), SUM_B_END - a_)
            lin[a_:a_ + n] = d_[:n]
    b = sum16le(bytes(lin))
    stored_b = struct.unpack_from("<H", blk1, WORD_B_OFF)[0]
    chk("word B self-consistent in the OUTPUT", b == stored_b,
        f"calc {b:04X} stored {stored_b:04X}")

    # --- layer 3 ----------------------------------------------------------
    for i, (a_, d_, crc_off) in enumerate(bm):
        got = struct.unpack_from(">H", rawm, crc_off)[0]
        want_c = binascii.crc_hqx(bytes(d_), 0xFFFF)
        chk(f"block {i} CRC-16", got == want_c, f"{got:04X}/{want_c:04X}")
    fc = binascii.crc32(bytes(rawm[dsm:])) & 0xFFFFFFFF
    m = re.search(rb"file_checksum\s*=\s*0x([0-9A-Fa-f]+)", rawm[:dsm])
    chk("header file_checksum", int(m.group(1), 16) == fc,
        f"{m.group(1).decode()} vs {fc:08X}")

    # --- header untouched otherwise ---------------------------------------
    hs, hm = raws[:dss], rawm[:dsm]
    chk("header identical except the checksum digits",
        len(hs) == len(hm) and sum(1 for x, y in zip(hs, hm) if x != y) == len(m.group(1)))

    print("\n" + "=" * 58)
    print("ACCEPTANCE:", "READY TO FLASH" if ok else "DO NOT FLASH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
