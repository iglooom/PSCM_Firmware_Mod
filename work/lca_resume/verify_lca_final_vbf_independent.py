#!/usr/bin/env python3
"""Independent verification of the FINAL production LCA enabler VBF.

A second code path with its own container reader and its own restated
constants. If this disagrees with the builder, do not flash.

The defining property of this image versus the development ones: it carries
the five control patches and **no telemetry**, so the FD22 callback pointer
and the 60-word code cave must still hold their OEM values.

    python3 work/lca_resume/verify_lca_final_vbf_independent.py
"""
from __future__ import annotations

import binascii
import hashlib
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "CV6T-14C217-AR_LCA_ENABLED.VBF"
STOCK = ROOT / "CV6T-14C217-AR.VBF"
CAL = ROOT / "CV6T-14C218-AX.VBF"
DRIVEN = ROOT / "CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF"

BLK0, BLK1, BLK2 = 0x00000000, 0x0001C000, 0x04008C00
WORD_A_OFF, WORD_B_OFF = 0x63FEA, 0x63FEC
SUM_B_END = 0x0007FFEC
START = 0x1800

# offset -> (stock words, patched words)
PATCHES = {
    0x38F00: ((0xF07C, 0x0904, 0x4C01, 0xA203), (0xE700,) * 4),
    0x397F6: ((0xFF7C, 0x2DC1, 0xA209), (0xE700,) * 3),
    0x3B160: ((0xA303,), (0xE700,)),
    0x39F5C: ((0xAFE2,), (0xAFD8,)),
    0x3A05E: ((0xB04E,), (0xB031,)),
}

OEM_POINTER_OFF, OEM_POINTER = 0x01D48, (0x058F, 0x0001)
OEM_CAVE_OFF, OEM_CAVE_WORD, CAVE_WORDS = 0x4B000, 0xE70A, 60
OEM_WORD_A, OEM_WORD_B = 0xD110, 0x3216


def parse(path):
    raw = path.read_bytes()
    depth, end = 0, None
    for i, byte in enumerate(raw):
        if byte == 0x7B:
            depth += 1
        elif byte == 0x7D:
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise SystemExit(f"{path.name}: unterminated header")
    match = re.search(rb"file_checksum\s*=\s*0x([0-9A-Fa-f]+)", raw[:end])
    if not match:
        raise SystemExit(f"{path.name}: no file_checksum")
    blocks, pos = [], end
    while pos + 8 <= len(raw):
        addr, length = struct.unpack_from(">II", raw, pos)
        if length == 0 or pos + 8 + length + 2 > len(raw):
            break
        blocks.append((addr, raw[pos + 8:pos + 8 + length],
                       struct.unpack_from(">H", raw, pos + 8 + length)[0]))
        pos += 8 + length + 2
    return raw, blocks, (match.start(1), match.end(1)), end


def crc16_mcrf4xx(data, init=0xFFFF):
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def sum16le(data):
    return sum(struct.unpack_from("<H", data, o)[0]
               for o in range(0, len(data) - 1, 2)) & 0xFFFF


def words(data, offset, count):
    return struct.unpack_from("<%dH" % count, data, offset)


def main():
    checks = []

    def ok(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    if not TARGET.exists():
        raise SystemExit(f"missing {TARGET}; run the builder first")

    raw, blocks, span, data_start = parse(TARGET)
    _sr, sblocks, _ss, _sd = parse(STOCK)
    cal_blocks = parse(CAL)[1]

    print("INDEPENDENT VERIFICATION — final LCA enabler (no telemetry)")
    print(f"  file   : {TARGET.name}")
    print(f"  sha256 : {hashlib.sha256(raw).hexdigest()}\n")

    bl = dict((a, d) for a, d, _ in blocks)
    sbl = dict((a, d) for a, d, _ in sblocks)
    ok("3 data blocks", len(blocks) == 3)
    ok("block map is 0 / 0x1C000 / 0x4008C00",
       sorted(bl) == [BLK0, BLK1, BLK2])
    ok("block sizes match stock",
       [len(d) for _a, d, _c in blocks] == [len(d) for _a, d, _c in sblocks])

    new, old = bl[BLK1], sbl[BLK1]

    # --- the five patches -------------------------------------------------
    for offset, (stock_words, patched_words) in sorted(PATCHES.items()):
        n = len(stock_words)
        ok(f"0x{offset:05X} stock was {' '.join(f'{w:04X}' for w in stock_words)}",
           words(old, offset, n) == stock_words)
        ok(f"0x{offset:05X} now   {' '.join(f'{w:04X}' for w in patched_words)}",
           words(new, offset, n) == patched_words)

    # --- NO TELEMETRY: the distinguishing property of this build -----------
    ok("FD22 callback pointer is OEM",
       words(new, OEM_POINTER_OFF, 2) == OEM_POINTER,
       " ".join(f"{w:04X}" for w in words(new, OEM_POINTER_OFF, 2)))
    cave = set(words(new, OEM_CAVE_OFF, CAVE_WORDS))
    ok("code cave is OEM filler (E70A x60)", cave == {OEM_CAVE_WORD})

    # --- only code-5 moved in either dispatch table ------------------------
    for label, table_off in (("dispatcher-1", 0x39F48), ("dispatcher-2", 0x3A04A)):
        cur = words(new, table_off, 11)
        stk = words(old, table_off, 11)
        ok(f"{label}: codes 0-4 unchanged",
           [cur[2 * i] for i in range(5)] == [stk[2 * i] for i in range(5)],
           " ".join(f"{cur[2 * i]:04X}" for i in range(5)))
        ok(f"{label}: stride words unchanged",
           [cur[2 * i + 1] for i in range(5)] == [stk[2 * i + 1] for i in range(5)])
        ok(f"{label}: code-5 now equals code-4", cur[10] == cur[8])

    # --- arms and downstream code untouched --------------------------------
    for label, pword, count in (
        ("d1 code-0 arm", 0x2AFE7, 3), ("d1 code-1 arm", 0x2AFB0, 31),
        ("d1 code-2 arm", 0x2AFCF, 5), ("d1 code-3 arm", 0x2AFD4, 4),
        ("d1 code-4 arm", 0x2AFD8, 10), ("d1 code-5 arm", 0x2AFE2, 5),
        ("d2 build arm", 0x2B031, 14), ("d2 bleed arm", 0x2B04E, 6),
        ("rate limiter", 0x2AFEA, 33), ("demand stage", 0x2B054, 40),
        ("torque function", 0x2B07C, 60),
    ):
        o = (pword - 0x0E000) * 2
        ok(f"{label} identical to stock", new[o:o + count * 2] == old[o:o + count * 2])

    # retained availability predicates around the patched branch
    ok("code-2/code-3 availability predicates retained",
       words(new, 0x3B160 - 8, 8) ==
       (0x4C02, 0xA306, 0xE700, 0x4C05, 0xE700, 0xE700, 0x4C03, 0xA203))

    # --- change accounting -------------------------------------------------
    changed = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
    allowed = set()
    for offset, (_s, patched_words) in PATCHES.items():
        allowed |= set(range(offset, offset + len(patched_words) * 2))
    allowed |= {WORD_A_OFF, WORD_A_OFF + 1, WORD_B_OFF, WORD_B_OFF + 1}
    ok("no change outside patches + checksums",
       not (set(changed) - allowed),
       f"{len(changed)} bytes changed")
    ok("exactly 22 bytes changed", len(changed) == 22, str(len(changed)))
    ok("blocks 0 and 2 byte-identical to stock",
       bl[BLK0] == sbl[BLK0] and bl[BLK2] == sbl[BLK2])

    # --- integrity, recomputed from scratch --------------------------------
    ok("stock word A/B were OEM",
       words(old, WORD_A_OFF, 1)[0] == OEM_WORD_A
       and words(old, WORD_B_OFF, 1)[0] == OEM_WORD_B)
    calc_a = crc16_mcrf4xx(bl[BLK0] + new[START:WORD_A_OFF])
    ok("word A recomputes", calc_a == words(new, WORD_A_OFF, 1)[0],
       f"calc {calc_a:04X} stored {words(new, WORD_A_OFF, 1)[0]:04X}")
    linear = bytearray(b"\xFF" * SUM_B_END)
    for addr, data in list(bl.items()) + [(0x9800, cal_blocks[0][1])]:
        if addr < SUM_B_END:
            n = min(len(data), SUM_B_END - addr)
            linear[addr:addr + n] = data[:n]
    calc_b = sum16le(linear)
    ok("word B recomputes", calc_b == words(new, WORD_B_OFF, 1)[0],
       f"calc {calc_b:04X} stored {words(new, WORD_B_OFF, 1)[0]:04X}")
    ok("all block CRC-16 valid",
       all(binascii.crc_hqx(d, 0xFFFF) == c for _a, d, c in blocks))
    calc_f = binascii.crc32(raw[data_start:]) & 0xFFFFFFFF
    ok("file CRC-32 valid", f"{calc_f:08X}" == raw[slice(*span)].decode())

    # --- control words match the image actually driven ---------------------
    if DRIVEN.exists():
        dbl = dict((a, d) for a, d, _ in parse(DRIVEN)[1])[BLK1]
        same = all(words(dbl, o, len(p)) == p for o, (_s, p) in PATCHES.items())
        ok("control words match the driven joint11 image", same)
        ok("telemetry NOT carried over from joint11",
           new[OEM_CAVE_OFF:OEM_CAVE_OFF + 120] != dbl[OEM_CAVE_OFF:OEM_CAVE_OFF + 120])

    failed = 0
    for name, good, detail in checks:
        print(f"  {'PASS' if good else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        failed += not good

    print("\n" + "=" * 62)
    if failed:
        print(f"ACCEPTANCE: DO NOT FLASH — {failed} check(s) failed")
        return 1
    print("ACCEPTANCE: READY TO FLASH")
    print("  5 control patches, no telemetry, both internal checksums valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
