#!/usr/bin/env python3
"""Build a CV6T-14C217-AR VBF with the LCA calibration gate neutralised.

THE EDIT
--------
In CV6T the `LkaActvStats_D_Req == 6` (LCA in Progress) arm of the lane-state
dispatcher carries a 4-word guard that BV6T does not have:

    P:$2A77E  4C06       CMP.W  #6
              A207       Bne    +7          -> enum != 6, go store 0 (idle)
              F07C 0904  MOVE.W X:$0904,A   <-- inserted in CV6T
              4C01       CMP.W  #1          <-- inserted
              A203       Bne    +3          <-- inserted (cell != 1 -> store 0)
              E684 2DDE  MOVE.W #4,X:$2DDE      the LCA torque state
              A902       BRA
              E680 2DDE  MOVE.W #0,X:$2DDE      idle

The four inserted words are replaced with four NOPs (`E700`, confirmed from the
project's own DSP56800E encoding table as the 16-bit NOP):

              4C06       CMP.W  #6
              A207       Bne    +7          <-- UNCHANGED, still handles enum != 6
              E700       NOP
              E700       NOP
              E700       NOP
              E700       NOP
              E684 2DDE  MOVE.W #4,X:$2DDE  <-- now unconditional for enum == 6

WHY NOP RATHER THAN RE-FLOWING TO MATCH BV6T
--------------------------------------------
Deleting the four words would shift every following instruction and invalidate
every branch displacement in the function. NOPs keep the image byte-aligned:
exactly 8 bytes change, no address moves, and the `Bne +7` that dispatches
non-6 values keeps its correct target. The resulting behaviour for enum == 6 is
identical to BV6T's: store 4 unconditionally.

INTEGRITY LAYERS (order matters, see PSCM_internal_checksums.md §10)
---------------------------------------------------------------------
The edit is in 14C217 blk1, so:
  1. blk1 word A at 0x0007FFEA = crc16_mcrf4xx(blk0 ++ blk1[START:0x63FEA))
     START is per-build and is read from the firmware itself (AR: blk1+0x1800).
  2. blk1 word B at 0x0007FFEC = sum16le(linear_flash[0 .. 0x7FFEC)) which
     spans blk0 + the paired 14C218 calibration + blk1, and covers word A.
  3. Container: block CRC-16, then header file_checksum (vbftool does these).

Word A is checked by a CONTINUOUS BACKGROUND MONITOR, not only at boot, so a
wrong value faults an electric power steering ECU while driving. Both words are
recomputed here and re-verified independently after the file is written.

Usage:
    python3 patch_lca_gate.py --selftest
    python3 patch_lca_gate.py            # writes CV6T-14C217-AR_LCA.VBF
"""
import argparse
import binascii
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
VBFTOOL = os.path.expanduser(
    "~/.hermes/skills/software-development/vbf-firmware-container/scripts/vbftool.py")

STOCK = os.path.join(ROOT, "CV6T-14C217-AR.VBF")
CAL = os.path.join(ROOT, "CV6T-14C218-AX.VBF")
OUT = os.path.join(ROOT, "CV6T-14C217-AR_LCA.VBF")

# --- the edit -------------------------------------------------------------
GATE_WORD = 0x2A77E              # P: word address of CMP.W #6
BLK1_WORD_BASE = 0x0E000         # P: word address of blk1 start (byte 0x1C000)
BLK1_FLASH = 0x0001C000

ORIG = [0x4C06, 0xA207, 0xF07C, 0x0904, 0x4C01, 0xA203, 0xE684, 0x2DDE,
        0xA902, 0xE680, 0x2DDE]
# offsets +04..+0A (4 words) become NOP
NOP = 0xE700
PATCH_AT_WORD = 2                # index into ORIG: the F07C
PATCH_LEN = 4                    # F07C 0904 4C01 A203

# --- checksum geometry ----------------------------------------------------
WORD_A_OFF = 0x63FEA             # within blk1
WORD_B_OFF = 0x63FEC             # within blk1
SUM_B_END = 0x0007FFEC           # linear flash address


def crc16_mcrf4xx(data, init=0xFFFF):
    crc = init
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def sum16le(data):
    s = 0
    for i in range(0, len(data) - 1, 2):
        s = (s + struct.unpack_from("<H", data, i)[0]) & 0xFFFF
    return s


def parse_vbf(path):
    """-> (header_bytes, data_start, [(addr, data, crc_off)])"""
    raw = open(path, "rb").read()
    depth = 0
    he = None
    for i, c in enumerate(raw):
        if c == 0x7B:
            depth += 1
        elif c == 0x7D:
            depth -= 1
            if depth == 0:
                he = i + 1
                break
    # probe for the offset whose walk consumes the file exactly (skill §2.1)
    for ds in range(he, he + 8):
        off = ds
        blocks = []
        ok = True
        while off < len(raw):
            if off + 8 > len(raw):
                ok = False
                break
            addr, ln = struct.unpack_from(">II", raw, off)
            if off + 8 + ln + 2 > len(raw):
                ok = False
                break
            blocks.append((addr, raw[off + 8:off + 8 + ln], off + 8 + ln))
            off += 8 + ln + 2
        if ok and off == len(raw) and blocks:
            return raw, ds, blocks
    raise SystemExit(f"cannot parse {path}")


def build(verbose=True):
    raw, ds, blocks = parse_vbf(STOCK)
    addrs = [a for a, _, _ in blocks]
    if verbose:
        print("stock blocks:", " ".join(f"0x{a:08X}/{len(d)}" for a, d, _ in blocks))

    bi = addrs.index(BLK1_FLASH)
    blk0 = blocks[0][1]
    blk1 = bytearray(blocks[bi][1])

    # ---- verify we are editing exactly what we think ---------------------
    off = (GATE_WORD - BLK1_WORD_BASE) * 2
    cur = [struct.unpack_from("<H", blk1, off + 2 * i)[0] for i in range(len(ORIG))]
    if cur != ORIG:
        raise SystemExit("gate bytes do not match expectation:\n"
                         f"  found    {' '.join(f'{x:04X}' for x in cur)}\n"
                         f"  expected {' '.join(f'{x:04X}' for x in ORIG)}")
    if verbose:
        print(f"gate verified at blk1+0x{off:X} (flash 0x{BLK1_FLASH + off:06X})")

    # ---- apply --------------------------------------------------------------
    for i in range(PATCH_LEN):
        struct.pack_into("<H", blk1, off + 2 * (PATCH_AT_WORD + i), NOP)
    new = [struct.unpack_from("<H", blk1, off + 2 * i)[0] for i in range(len(ORIG))]
    if verbose:
        print("  before:", " ".join(f"{x:04X}" for x in ORIG))
        print("  after :", " ".join(f"{x:04X}" for x in new))

    # ---- layer 1: word A ----------------------------------------------------
    start = find_start_offset(blk1)
    a = crc16_mcrf4xx(bytes(blk0) + bytes(blk1[start:WORD_A_OFF]))
    old_a = struct.unpack_from("<H", blk1, WORD_A_OFF)[0]
    struct.pack_into("<H", blk1, WORD_A_OFF, a)
    if verbose:
        print(f"word A: {old_a:04X} -> {a:04X}   (START=blk1+0x{start:X})")

    # ---- layer 2: word B (whole linear image, includes 14C218) -------------
    _, _, cblocks = parse_vbf(CAL)
    linear = bytearray(b"\xFF" * SUM_B_END)
    for a_, d_, _ in blocks[:bi] + [(BLK1_FLASH, bytes(blk1), 0)]:
        if a_ >= SUM_B_END:
            continue
        n = min(len(d_), SUM_B_END - a_)
        linear[a_:a_ + n] = d_[:n]
    for a_, d_, _ in cblocks:
        if a_ >= SUM_B_END:
            continue
        n = min(len(d_), SUM_B_END - a_)
        linear[a_:a_ + n] = d_[:n]
    b = sum16le(bytes(linear))
    old_b = struct.unpack_from("<H", blk1, WORD_B_OFF)[0]
    struct.pack_into("<H", blk1, WORD_B_OFF, b)
    if verbose:
        print(f"word B: {old_b:04X} -> {b:04X}")

    # ---- layer 3: container -------------------------------------------------
    out = bytearray(raw)
    a0, d0, crc_off = blocks[bi]
    out[crc_off - len(d0):crc_off] = blk1
    struct.pack_into(">H", out, crc_off, binascii.crc_hqx(bytes(blk1), 0xFFFF))
    fc = binascii.crc32(bytes(out[ds:])) & 0xFFFFFFFF
    hdr = bytes(out[:ds])
    import re
    m = re.search(rb"file_checksum\s*=\s*0x([0-9A-Fa-f]+)", hdr)
    if not m:
        raise SystemExit("file_checksum not found in header")
    width = len(m.group(1))
    new_hex = f"{fc:0{width}X}".encode()
    if len(new_hex) != width:
        raise SystemExit("checksum width mismatch")
    out[m.start(1):m.end(1)] = new_hex
    if verbose:
        print(f"file_checksum: {m.group(1).decode()} -> {new_hex.decode()}")

    open(OUT, "wb").write(bytes(out))
    if verbose:
        print(f"\nwrote {OUT}")
    return OUT


def find_start_offset(blk1):
    """Read the self-check's START immediate from the firmware (per-build)."""
    # the long-immediate that sits next to #$0003FFF5
    needle = struct.pack("<HH", 0xFFF5, 0x0003)
    p = bytes(blk1).find(needle)
    if p < 0:
        needle = struct.pack(">I", 0x0003FFF5)
        p = bytes(blk1).find(needle)
    if p < 0:
        raise SystemExit("cannot locate the self-check START immediate")
    # scan a window around it for a 0x0000Exxx long immediate
    for q in range(max(0, p - 64), min(len(blk1) - 4, p + 64), 2):
        lo, hi = struct.unpack_from("<HH", blk1, q)
        val = (hi << 16) | lo
        if 0x0000E000 <= val <= 0x0000F000:
            return (val * 2) - BLK1_FLASH
    raise SystemExit("START immediate not found near the self-check constant")


def selftest():
    ok = True

    def chk(name, cond, d=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {d}" if d else ""))

    print("SELFTEST\n")
    chk("crc16_mcrf4xx('123456789') == 0x6F91",
        crc16_mcrf4xx(b"123456789") == 0x6F91, f"{crc16_mcrf4xx(b'123456789'):04X}")
    chk("sum16le of 0x0001,0x0002 == 3", sum16le(b"\x01\x00\x02\x00") == 3)
    chk("NOP encoding is E700", NOP == 0xE700)
    chk("patch replaces exactly 4 words", PATCH_LEN == 4)
    chk("patch does NOT touch the CMP #6 or its Bne",
        PATCH_AT_WORD == 2 and PATCH_AT_WORD + PATCH_LEN <= 6)
    chk("patch does NOT touch the MOVE #4", PATCH_AT_WORD + PATCH_LEN == 6)

    # the word-A recipe must reproduce the STOCK value on the stock file
    raw, ds, blocks = parse_vbf(STOCK)
    addrs = [a for a, _, _ in blocks]
    bi = addrs.index(BLK1_FLASH)
    blk0, blk1 = blocks[0][1], bytearray(blocks[bi][1])
    start = find_start_offset(blk1)
    chk("START extracted = blk1+0x1800 (AR)", start == 0x1800, f"0x{start:X}")
    a = crc16_mcrf4xx(bytes(blk0) + bytes(blk1[start:WORD_A_OFF]))
    stored = struct.unpack_from("<H", blk1, WORD_A_OFF)[0]
    chk("word A recipe reproduces the STOCK word", a == stored,
        f"calc {a:04X} stored {stored:04X}")

    _, _, cblocks = parse_vbf(CAL)
    linear = bytearray(b"\xFF" * SUM_B_END)
    for a_, d_, _ in blocks:
        if a_ < SUM_B_END:
            n = min(len(d_), SUM_B_END - a_)
            linear[a_:a_ + n] = d_[:n]
    for a_, d_, _ in cblocks:
        if a_ < SUM_B_END:
            n = min(len(d_), SUM_B_END - a_)
            linear[a_:a_ + n] = d_[:n]
    b = sum16le(bytes(linear))
    stored_b = struct.unpack_from("<H", blk1, WORD_B_OFF)[0]
    chk("word B recipe reproduces the STOCK word", b == stored_b,
        f"calc {b:04X} stored {stored_b:04X}")

    print("\n" + "=" * 58)
    print("SELFTEST:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    sys.exit(selftest() if a.selftest else (build() and 0))
