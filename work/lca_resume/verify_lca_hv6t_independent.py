#!/usr/bin/env python3
"""Independent verifier for HV6T-14C217-AC_LCA_ENABLED.VBF.

Deliberately does NOT import the builder's parser or checksum code. Its own
container walk, its own CRC-16/CCITT, CRC-32, CRC-16/MCRF4XX and sum16le, its
own restated patch/guard constants. Recomputes both internal words and every
container CRC from scratch and diffs against stock.
"""
from __future__ import annotations
import binascii, hashlib, re, struct, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STOCK = ROOT / "HV6T-14C217-AC.VBF"
CAL = ROOT / "HV6T-14C218-AD.VBF"
BUILT = ROOT / "HV6T-14C217-AC_LCA_ENABLED.VBF"

BLK0, BLK1, BLK2 = 0x00000000, 0x0001C000, 0x04008C00
CAL_ADDR = 0x00009800
WORD_A_OFF, WORD_B_OFF = 0x63FEA, 0x63FEC
START = 0x1800
SUM_B_END = 0x0007FFEC
OEM_WORD_A, OEM_WORD_B = 0xB022, 0xD35E

PATCHES = {  # blk1 offset -> (stock words, patched words)
    0x39144: ((0xF07C, 0x090A, 0x4C01, 0xA203), (0xE700,) * 4),
    0x39A3A: ((0xFF7C, 0x2E1B, 0xA209), (0xE700,) * 3),
    0x3B3A4: ((0xA303,), (0xE700,)),
    0x3A1A0: ((0xB104,), (0xB0FA,)),
    0x3A2A2: ((0xB170,), (0xB153,)),
}
D1, D2 = 0x3A18C, 0x3A28E   # dispatch tables; entries 0..4 must be frozen


def walk(raw):
    """Independent VBF walk: find header end by brace depth, probe data_start."""
    depth = 0; started = False; hend = None
    for i, b in enumerate(raw):
        if b == 0x7B: depth += 1; started = True
        elif b == 0x7D and started:
            depth -= 1
            if depth == 0: hend = i + 1; break
    for ds in range(hend, hend + 8):
        off = ds; blocks = []; ok = True
        while off < len(raw):
            if off + 10 > len(raw): ok = False; break
            a, ln = struct.unpack_from(">II", raw, off)
            d0 = off + 8; c = d0 + ln
            if c + 2 > len(raw): ok = False; break
            crc = struct.unpack_from(">H", raw, c)[0]
            blocks.append((a, raw[d0:c], d0, c, crc))
            off = c + 2
        if ok and off == len(raw) and blocks:
            m = re.search(rb"file_checksum\s*=\s*0x([0-9A-Fa-f]+)", raw[:ds])
            return ds, blocks, m.span(1)
    raise SystemExit("independent walk failed")


def block(blocks, addr):
    return next(b for b in blocks if b[0] == addr)


def crc_mcrf4xx(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def sum16le(data):
    return sum(struct.unpack_from("<H", data, o)[0]
               for o in range(0, len(data) - 1, 2)) & 0xFFFF


def word_a(b0, b1):
    return crc_mcrf4xx(b0 + b1[START:WORD_A_OFF])


def word_b(blocks, cal_blocks, b1):
    buf = bytearray(b"\xFF" * SUM_B_END)
    for a, data, *_ in blocks:
        src = b1 if a == BLK1 else data
        if a < SUM_B_END:
            n = min(len(src), SUM_B_END - a); buf[a:a + n] = src[:n]
    for a, data, *_ in cal_blocks:
        if a < SUM_B_END:
            n = min(len(data), SUM_B_END - a); buf[a:a + n] = data[:n]
    return sum16le(buf)


def u16(d, o): return struct.unpack_from("<H", d, o)[0]


def main():
    fails = []
    def chk(name, cond):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond: fails.append(name)

    sraw = STOCK.read_bytes(); braw = BUILT.read_bytes(); craw = CAL.read_bytes()
    sds, sbl, ssp = walk(sraw)
    bds, bbl, bsp = walk(braw)
    cds, cbl, csp = walk(craw)

    # reference self-tests on the algorithms
    chk("CRC-16/MCRF4XX vector (0x6F91)", crc_mcrf4xx(b"123456789") == 0x6F91)
    chk("sum16le vector", sum16le(b"\x01\x00\x02\x00") == 3)

    # stock internal words reproduce (algorithm sanity on this exact module)
    s0 = block(sbl, BLK0)[1]; s1 = block(sbl, BLK1)[1]
    chk("stock word A reproduces", word_a(s0, s1) == OEM_WORD_A == u16(s1, WORD_A_OFF))
    chk("stock word B reproduces", word_b(sbl, cbl, s1) == OEM_WORD_B == u16(s1, WORD_B_OFF))
    chk("cal self-sum == 0xFFFF", sum16le(block(cbl, CAL_ADDR)[1]) == 0xFFFF)

    # container topology preserved
    chk("topology unchanged",
        [(a, len(d)) for a, d, *_ in sbl] == [(a, len(d)) for a, d, *_ in bbl])
    chk("data_start unchanged", sds == bds)

    b0 = block(bbl, BLK0)[1]; b1 = block(bbl, BLK1)[1]; s1 = block(sbl, BLK1)[1]

    # patches applied, stock asserted, guards intact
    for off, (old, new) in PATCHES.items():
        chk(f"patch @0x{off:05X} stock was correct",
            struct.unpack_from("<%dH" % len(old), s1, off) == old)
        chk(f"patch @0x{off:05X} now patched",
            struct.unpack_from("<%dH" % len(new), b1, off) == new)

    # dispatch tables: entries 0..4 + separators frozen, only entry5 moved
    for t in (D1, D2):
        frozen = all(u16(b1, t + 4 * c) == u16(s1, t + 4 * c) for c in range(5)) \
                 and all(u16(b1, t + 2 * s) == u16(s1, t + 2 * s) for s in range(1, 11, 2))
        chk(f"table 0x{t:X} entries 0..4 + seps frozen", frozen)
        chk(f"table 0x{t:X} entry5 changed", u16(b1, t + 0x14) != u16(s1, t + 0x14))

    # recompute internal words from scratch on the BUILT image
    chk("built word A valid", u16(b1, WORD_A_OFF) == word_a(b0, b1))
    chk("built word B valid", u16(b1, WORD_B_OFF) == word_b(bbl, cbl, b1))

    # container CRCs
    ok_blk = all(binascii.crc_hqx(d, 0xFFFF) == crc for a, d, o, c, crc in bbl)
    chk("all block CRC-16 valid", ok_blk)
    file_calc = binascii.crc32(braw[bds:]) & 0xFFFFFFFF
    chk("file CRC-32 valid", int(braw[slice(*bsp)], 16) == file_calc)

    # exact diff: only the 5 sites + 2 checksum words differ in blk1; other blocks identical
    changed = {i for i, (x, y) in enumerate(zip(s1, b1)) if x != y}
    allowed = set()
    for off, (old, new) in PATCHES.items():
        allowed |= set(range(off, off + len(new) * 2))
    allowed |= {WORD_A_OFF, WORD_A_OFF + 1, WORD_B_OFF, WORD_B_OFF + 1}
    chk("no unexpected blk1 changes", not (changed - allowed))
    chk("blk0 identical", block(sbl, BLK0)[1] == block(bbl, BLK0)[1])
    chk("blk2 identical", block(sbl, BLK2)[1] == block(bbl, BLK2)[1])
    hdr = {i for i in range(sds) if sraw[i] != braw[i]}
    chk("header changes confined to file_checksum", not (hdr - set(range(*bsp))))

    print(f"\nchanged blk1 bytes: {len(changed)}  built sha256: {hashlib.sha256(braw).hexdigest()}")
    print("INDEPENDENT VERIFY:", "ALL PASS" if not fails else f"FAILURES: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
