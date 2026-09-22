#!/usr/bin/env python3
"""Build a PSCM CV6T-14C217-AR image that ALWAYS transmits "hands on".

TEST/RESEARCH image. Forces the transmitted `LaHandsOff_B_Actl` bit in CAN
frame 0x140 (PSCM_h_FrP01, byte 7 bit 5) to 0 = "Hands on", so the IPMA camera
never escalates the hands-off warning in the IPC cluster.

Mechanism (see PSCM_handsoff_disable.md for the full trace)
-----------------------------------------------------------
The status-frame composer at P:$1DB5D reads three producer cells and packs them
into frame 0x140 byte 7:

    X:$2252  LaActAvail_D_Actl   (bits 3:2)
    X:$2251  LaActDeny_B_Actl    (bit 4)     <- from X:$2DB8
    X:$2250  LaHandsOff_B_Actl   (bit 5)     <- from X:$2DAA

X:$2250 is written ONLY at P:$2B8E0 by copying the hands-off detection machine
output X:$2DAA, and is read ONLY by the composer.  Neutralising that one copy
so it stores 0 forces the transmitted bit to "hands on" while leaving intact:
  - the hands-off detection state machine (X:$2DAA, cals 0357/0358/0359),
  - active-deny and availability,
  - the OEM checksum (byte 6) + rolling counter, recomputed by OEM code over
    the honest bit5=0, so the frame stays self-consistent (no DTC / no camera
    fault).

Single patch, 3 words at blk1 byte offset 0x3B1C0 (P:$2B8E0):

    stock    F67C 2DAA 2250   MOVE.W X:$2DAA,X:$2250
    patched  E680 2250 E700   MOVE.W #0,X:$2250 ; NOP

Usage:
    python3 work/lca_resume/build_handsoff_off_vbf.py --selftest
    python3 work/lca_resume/build_handsoff_off_vbf.py
"""
from __future__ import annotations

import argparse
import binascii
import struct
import sys
from pathlib import Path

import build_fd22_snapshot_vbf as base

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "CV6T-14C217-AR_HANDSOFF_OFF.VBF"

# (label, blk1 byte offset, stock words, patched words)
PATCHES = (
    ("force transmitted hands-off = 0   P:$2B8E0",
     0x3B1C0, (0xF67C, 0x2DAA, 0x2250), (0xE680, 0x2250, 0xE700)),
)

# Context that must survive verbatim; a mismatch means the wrong image/layout.
# The two neighbouring status copies frame the patched instruction:
#   P:$2B8DA  F67C 2D28 2252   X:$2D28 -> X:$2252  (availability)
#   P:$2B8DD  F67C 2DB8 2251   X:$2DB8 -> X:$2251  (deny)
#   P:$2B8E0  F67C 2DAA 2250   X:$2DAA -> X:$2250  (hands-off)  <- patched
#   P:$2B8E3  E708             RTS
GUARDS = (
    ("status-copy block", 0x3B1B4,
     (0xF67C, 0x2D28, 0x2252, 0xF67C, 0x2DB8, 0x2251,
      0xF67C, 0x2DAA, 0x2250)),
)

# Telemetry sites must remain OEM in this build.
POINTER_OFF = base.POINTER_OFF          # 0x01D48
HANDLER_OFF = base.HANDLER_OFF          # 0x4B000


def _w(words):
    return struct.pack("<%dH" % len(words), *words)


def build_bytes(stock, cal):
    base.verify_oem_inputs(stock, cal)
    block0 = stock.block_at(base.BLK0_FLASH).data
    record = stock.block_at(base.BLK1_FLASH)
    block1 = bytearray(record.data)

    for label, offset, old, _new in PATCHES:
        base.expect_bytes(block1, offset, _w(old), label)
    for label, offset, words in GUARDS:
        base.expect_bytes(block1, offset, _w(words), f"context: {label}")
    base.expect_bytes(block1, base.WORD_A_OFF, _w((base.OEM_WORD_A,)), "word A")
    base.expect_bytes(block1, base.WORD_B_OFF, _w((base.OEM_WORD_B,)), "word B")
    base.expect_bytes(block1, POINTER_OFF, _w(base.OEM_POINTER_WORDS), "OEM callback pointer")
    base.expect_bytes(block1, HANDLER_OFF, _w((base.OEM_CAVE_WORD,) * 60), "OEM cave")

    for _label, offset, _old, new in PATCHES:
        block1[offset:offset + len(new) * 2] = _w(new)

    old_a = base._u16le(block1, base.WORD_A_OFF)
    new_a = base.calculate_word_a(block0, bytes(block1))
    struct.pack_into("<H", block1, base.WORD_A_OFF, new_a)
    old_b = base._u16le(block1, base.WORD_B_OFF)
    new_b = base.calculate_word_b(stock, cal, bytes(block1))
    struct.pack_into("<H", block1, base.WORD_B_OFF, new_b)

    out = bytearray(stock.raw)
    out[record.data_offset:record.crc_offset] = block1
    crc = binascii.crc_hqx(block1, 0xFFFF)
    struct.pack_into(">H", out, record.crc_offset, crc)
    old_file = int(stock.raw[slice(*stock.checksum_span)], 16)
    new_file = binascii.crc32(out[stock.data_start:]) & 0xFFFFFFFF
    width = stock.checksum_span[1] - stock.checksum_span[0]
    out[slice(*stock.checksum_span)] = f"{new_file:0{width}X}".encode()

    return base.BuildResult(bytes(out), old_a, new_a, old_b, new_b,
                            record.stored_crc, crc, old_file, new_file)


def audit(stock, built, cal):
    base.verify_container(built)
    if built.data_start != stock.data_start:
        raise base.BuildError("data_start changed")
    topology = [(b.address, len(b.data), b.data_offset, b.crc_offset) for b in built.blocks]
    if topology != [(b.address, len(b.data), b.data_offset, b.crc_offset) for b in stock.blocks]:
        raise base.BuildError("block topology changed")

    old_blk = stock.block_at(base.BLK1_FLASH).data
    new = built.block_at(base.BLK1_FLASH).data

    for label, offset, _o, patched in PATCHES:
        base.expect_bytes(new, offset, _w(patched), f"built: {label}")

    # Telemetry sites must be byte-identical to OEM in the output.
    base.expect_bytes(new, POINTER_OFF, _w(base.OEM_POINTER_WORDS),
                      "built: callback pointer must stay OEM")
    base.expect_bytes(new, HANDLER_OFF, _w((base.OEM_CAVE_WORD,) * 60),
                      "built: code cave must stay OEM")

    # The two neighbouring status copies (avail, deny) must be untouched.
    base.expect_bytes(new, 0x3B1B4, _w((0xF67C, 0x2D28, 0x2252)),
                      "built: availability copy must survive")
    base.expect_bytes(new, 0x3B1BA, _w((0xF67C, 0x2DB8, 0x2251)),
                      "built: deny copy must survive")

    allowed = set()
    expected = 0
    for _label, offset, old, patched in PATCHES:
        allowed |= set(range(offset, offset + len(patched) * 2))
        expected += sum(1 for a, b in zip(_w(old), _w(patched)) if a != b)
    allowed |= {base.WORD_A_OFF, base.WORD_A_OFF + 1,
                base.WORD_B_OFF, base.WORD_B_OFF + 1}
    expected += 4                       # both checksum words
    changed = {i for i, (a, b) in enumerate(zip(old_blk, new)) if a != b}
    if changed - allowed:
        raise base.BuildError(f"{len(changed - allowed)} unexpected payload changes")
    if len(changed) != expected:
        raise base.BuildError(f"{len(changed)} bytes changed, expected {expected}")

    if base._u16le(new, base.WORD_A_OFF) != base.calculate_word_a(
            built.block_at(base.BLK0_FLASH).data, new):
        raise base.BuildError("checksum A invalid")
    if base._u16le(new, base.WORD_B_OFF) != base.calculate_word_b(built, cal, new):
        raise base.BuildError("checksum B invalid")
    if any(a.data != b.data or a.stored_crc != b.stored_crc
           for a, b in zip(stock.blocks, built.blocks) if a.address != base.BLK1_FLASH):
        raise base.BuildError("untouched block changed")
    header_changed = {i for i in range(stock.data_start) if stock.raw[i] != built.raw[i]}
    if header_changed - set(range(*stock.checksum_span)):
        raise base.BuildError("header changed outside file checksum")
    return len(changed)


def selftest():
    try:
        stock = base.parse_vbf(base.DEFAULT_STOCK)
        cal = base.parse_vbf(base.DEFAULT_CAL)
        first = build_bytes(stock, cal)
        if first.output != build_bytes(stock, cal).output:
            raise base.BuildError("nondeterministic output")
        parsed = base.parse_vbf_bytes(first.output, Path("handsoff-selftest.vbf"))
        n = audit(stock, parsed, cal)
        # 3 patch words all differ -> 6 payload bytes, plus 2 checksum words
        # = 10 bytes total.
        if n != 10:
            raise base.BuildError(f"expected 10 changed bytes, got {n}")
        print(f"SELFTEST: ALL PASS ({n} bytes changed = 6 payload + 4 checksum)")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"SELFTEST FAILURE: {exc}", file=sys.stderr)
        return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    try:
        stock = base.parse_vbf(base.DEFAULT_STOCK)
        cal = base.parse_vbf(base.DEFAULT_CAL)
        result = build_bytes(stock, cal)
        parsed = base.parse_vbf_bytes(result.output, args.output)
        audit(stock, parsed, cal)
        base.write_atomic(args.output, result.output)
        readback = base.parse_vbf(args.output)
        audit(stock, readback, cal)
        if readback.raw != result.output:
            raise base.BuildError("read-back mismatch")

        print(f"wrote: {args.output}")
        print(f"sha256: {base.sha256(result.output)}")
        print("\nPATCH APPLIED (1 site, 3 words):")
        for label, offset, old, new in PATCHES:
            o = " ".join(f"{x:04X}" for x in old)
            n = " ".join(f"{x:04X}" for x in new)
            print(f"  0x{offset:05X}  {label}\n            {o}  ->  {n}")
        print("\nTELEMETRY: none (callback pointer and code cave left OEM)")
        print(f"word A: {base.OEM_WORD_A:04X} -> {result.new_word_a:04X}")
        print(f"word B: {base.OEM_WORD_B:04X} -> {result.new_word_b:04X}")
        print(f"block CRC-16: {result.new_block_crc:04X}   "
              f"file CRC-32: {result.new_file_checksum:08X}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
