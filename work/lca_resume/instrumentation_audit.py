#!/usr/bin/env python3
"""Read-only address audit for proposed CV6T PSCM instrumentation.

This script NEVER writes a VBF/bin and never opens CAN.  It checks the exact
CV6T-14C217-AR OEM words, task callback table, candidate P-space cave, proposed
X-RAM ring window, DID entries, and raw FD0C/FD0E getter sources.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "work/disasm"), str(ROOT / "work/wordA")]
from flow56800e import ABS_PREFIX, Image  # noqa: E402
from wordA_solved import find_start_offset, word_a  # noqa: E402

BLK0 = ROOT / "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin"
BLK1 = ROOT / "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin"
XINIT = ROOT / "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk2_0x04008C00.bin"
SIGCFG = ROOT / "bins/CV6T-14C386-AB/CV6T-14C386-AB_blk0_0x04008000.bin"
CAL = ROOT / "bins/CV6T-14C218-AX/CV6T-14C218-AX_blk0_0x00009800.bin"

# Primary hook: the unconditional lane cyclic callback calls the torque/output
# stage here.  A trampoline calls the OEM target first, then records one sample.
HOOK_P = 0x2A74B
HOOK_OEM = (0xE256, 0xAE86)          # JSR P:$2AE86
# Secondary/conditional inner hook, useful only for pinpointing the torque math.
INNER_HOOK_P = 0x2B078
INNER_HOOK_OEM = (0xE256, 0xB07C)    # JSR P:$2B07C
CAVE_START = 0x33783
CAVE_END = 0x3FFF2                   # exclusive; footer begins here
CAVE_RESERVE = (0x33800, 0x33900)    # proposed, 0x100 words
RING = (0x6600, 0x6D00)              # 256 records * 7 words
META = (0x6D00, 0x6D10)              # logger-only metadata
DID_TABLE = 0x0EDC4
DID_STRIDE = 6
XINIT_BASE = 0x4600


def words(path: Path) -> tuple[int, ...]:
    data = path.read_bytes()
    return struct.unpack("<%dH" % (len(data) // 2), data)


def xword(xw: tuple[int, ...], addr: int) -> int:
    return xw[addr - XINIT_BASE]


def pwindow(img: Image, addr: int, count: int) -> tuple[int, ...]:
    return tuple(img.word(addr + i) for i in range(count))


def sum16le(data: bytes) -> int:
    if len(data) & 1:
        data += b"\x00"
    return sum(struct.unpack("<%dH" % (len(data) // 2), data)) & 0xFFFF


def did_entry(img: Image, did: int) -> tuple[int, tuple[int, ...]]:
    for a in range(DID_TABLE, DID_TABLE + 40 * DID_STRIDE, DID_STRIDE):
        row = pwindow(img, a, DID_STRIDE)
        if row[0] == did:
            return a, row
    raise AssertionError("DID %04X not found" % did)


def extraction_destinations() -> set[int]:
    """Known stride-5 14C386 extraction destination/area words."""
    w = words(SIGCFG)
    out: set[int] = set()
    for i in range(0, min(0x1E0, len(w) - 5), 5):
        spec, byte_slot = w[i + 1], w[i + 2]
        mask, shift, slot = spec >> 8, spec & 0xFF, byte_slot & 0xFF
        if mask and shift <= 7 and slot and not (slot & (slot - 1)):
            out.update((w[i + 3], w[i + 4]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true", help="same audit; exit nonzero on drift")
    ap.parse_args()

    img = Image()
    xw = words(XINIT)
    blk0 = BLK0.read_bytes()
    blk1 = BLK1.read_bytes()
    cal = CAL.read_bytes()
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, bool(condition), detail))

    check("primary hook OEM words", pwindow(img, HOOK_P, 2) == HOOK_OEM,
          "P:%05X = %s" % (HOOK_P, " ".join("%04X" % x for x in pwindow(img, HOOK_P, 2))))
    check("primary hook flash byte address", HOOK_P * 2 == 0x54E96, "0x%06X" % (HOOK_P * 2))
    check("inner hook OEM words", pwindow(img, INNER_HOOK_P, 2) == INNER_HOOK_OEM,
          "P:%05X = %s" % (INNER_HOOK_P,
                            " ".join("%04X" % x for x in pwindow(img, INNER_HOOK_P, 2))))
    check("inner hook flash byte address", INNER_HOOK_P * 2 == 0x560F0,
          "0x%06X" % (INNER_HOOK_P * 2))
    check("entire linked tail cave is E70A",
          all(img.word(a) == 0xE70A for a in range(CAVE_START, CAVE_END)),
          "P:%05X..%05X (%d words)" % (CAVE_START, CAVE_END, CAVE_END - CAVE_START))
    check("reserved 0x100-word cave lies inside tail cave",
          CAVE_START <= CAVE_RESERVE[0] < CAVE_RESERVE[1] <= CAVE_END,
          "P:%05X..%05X" % CAVE_RESERVE)
    start_off, start_word = find_start_offset(blk1)
    stored_a = struct.unpack_from("<H", blk1, 0x63FEA)[0]
    calc_a = word_a(blk0, blk1, start_off)
    check("word A recipe and AR START reproduce OEM",
          start_off == 0x1800 and start_word == 0xEC00 and calc_a == stored_a,
          "START=0x%X calc=%04X stored=%04X" % (start_off, calc_a, stored_a))
    linear = bytearray(b"\xFF" * 0x7FFEC)
    linear[0:len(blk0)] = blk0
    linear[0x9800:0x9800 + len(cal)] = cal
    linear[0x1C000:0x1C000 + 0x63FEC] = blk1[:0x63FEC]
    stored_b = struct.unpack_from("<H", blk1, 0x63FEC)[0]
    calc_b = sum16le(bytes(linear))
    check("word B whole-image sum reproduces OEM", calc_b == stored_b,
          "calc=%04X stored=%04X" % (calc_b, stored_b))

    # Five 28-word lifecycle objects.  Object #2 owns the lane component.
    callbacks = tuple(xword(xw, a) for a in range(0x6132, 0x613A))
    check("lane lifecycle callback words",
          callbacks == (0xA756, 0x0002, 0xA743, 0x0002,
                         0xA75E, 0x0002, 0xA75B, 0x0002),
          " ".join("%04X" % x for x in callbacks))

    for did, addr, expected in (
        (0xF112, 0x0EDD0, (0xF112, 0, 0, 0, 0, 0)),
        (0xFD0C, 0x0EE5A, (0xFD0C, 0, 0xC0C0, 1, 0, 0)),
        (0xFD0E, 0x0EE66, (0xFD0E, 0, 0xC0C6, 1, 0, 0)),
        (0xFD20, 0x0EE96, (0xFD20, 0, 0xC0F9, 1, 0, 0)),
        (0xFD22, 0x0EEA2, (0xFD22, 0, 0x058F, 1, 0, 0)),
    ):
        got_addr, got = did_entry(img, did)
        check("DID %04X table entry" % did, got_addr == addr and got == expected,
              "P:%05X %s" % (got_addr, " ".join("%04X" % x for x in got)))

    check("FD22 metadata length is 15 bytes",
          tuple(xword(xw, a) for a in range(0x6388, 0x638C)) == (0xFD22, 0, 1, 0x000F),
          "X:6388 " + " ".join("%04X" % x for x in
                                 (xword(xw, a) for a in range(0x6388, 0x638C))))
    check("FD0C getter reads X:171B", pwindow(img, 0x1C177, 2) == (0xF07C, 0x171B))
    check("FD0E getter reads X:1CB1", pwindow(img, 0x1C1A7, 2) == (0xF07C, 0x1CB1))
    check("FD22 current handler begins with source base X:1DAC",
          pwindow(img, 0x1058F, 2) == (0x8748, 0x1DAC))

    # RAM candidacy checks are necessary but intentionally not claimed sufficient:
    # indirect/indexed users cannot be excluded by an absolute-operand scan.
    ram_words = tuple(xword(xw, a) for a in range(RING[0], META[1]))
    check("candidate ring+metadata is erased in OEM X-init", set(ram_words) == {0xFFFF},
          "X:%04X..%04X (%d words)" % (RING[0], META[1], len(ram_words)))
    refs = []
    for paddr, op in img.iter_words():
        if op in ABS_PREFIX:
            xa = img.word(paddr + 1)
            if RING[0] <= xa < META[1]:
                refs.append((paddr, op, xa))
    check("no recognized absolute/immediate P refs into ring+metadata", not refs,
          repr(refs))
    overlap = sorted(a for a in extraction_destinations() if RING[0] <= a < META[1])
    check("no known 14C386 extraction destination overlaps ring+metadata", not overlap,
          " ".join("%04X" % x for x in overlap))

    for p in (BLK0, BLK1, XINIT, SIGCFG, CAL):
        print("sha256 %s  %s" % (hashlib.sha256(p.read_bytes()).hexdigest(), p.name))
    print()
    for name, ok, detail in checks:
        print("%-4s %-62s %s" % ("PASS" if ok else "FAIL", name, detail))
    print("\nNOTE: RAM checks exclude known absolute and 14C386-table users only;")
    print("      they do NOT prove absence of an indexed/heap/stack user.")
    print("AUDIT:", "ALL STATIC CHECKS PASS" if all(x[1] for x in checks) else "FAILED")
    return 0 if all(x[1] for x in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
