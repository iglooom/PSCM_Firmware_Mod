#!/usr/bin/env python3
"""Build the FINAL production LCA enabler VBF: fix only, no telemetry.

Applies the five control-word edits documented in PSCM_LCA_enabler.md and
nothing else. Unlike the joint9/10/11 development images this contains NO
FD22 instrumentation: the callback pointer and the 60-word code cave are left
exactly as OEM, so the only deltas versus stock are the five patch words plus
the two internal checksum words.

    #  offset    stock                patched              effect
    1  0x38F00   F07C 0904 4C01 A203  E700 E700 E700 E700  LCA state entry gate
    2  0x397F6   FF7C 2DC1 A209       E700 E700 E700       phase-1 entry inhibit
    3  0x3B160   A303                 E700                 code-5 availability
    4  0x39F5C   AFE2                 AFD8                 code-5 ramp increment
    5  0x3A05E   B04E                 B031                 code-5 authority

Usage:
    python3 work/lca_resume/build_lca_final_vbf.py --selftest
    python3 work/lca_resume/build_lca_final_vbf.py
"""
from __future__ import annotations

import argparse
import binascii
import struct
import sys
from pathlib import Path

import build_fd22_snapshot_vbf as base

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "CV6T-14C217-AR_LCA_ENABLED.VBF"

# (label, blk1 byte offset, stock words, patched words)
PATCHES = (
    ("LCA state entry gate      P:$2A780",
     0x38F00, (0xF07C, 0x0904, 0x4C01, 0xA203), (0xE700,) * 4),
    ("phase-1 entry inhibit     P:$2ABFB",
     0x397F6, (0xFF7C, 0x2DC1, 0xA209), (0xE700,) * 3),
    ("code-5 availability       P:$2B8B0",
     0x3B160, (0xA303,), (0xE700,)),
    ("code-5 ramp increment     P:$2AFAE",
     0x39F5C, (0xAFE2,), (0xAFD8,)),
    ("code-5 authority          P:$2B02F",
     0x3A05E, (0xB04E,), (0xB031,)),
)

# Context that must survive verbatim; a mismatch means the wrong image.
GUARDS = (
    ("dispatcher ==6 arm", 0x38EFC,
     (0x4C06, 0xA207, 0xF07C, 0x0904, 0x4C01, 0xA203, 0xE684, 0x2DDE)),
    ("phase-1 state test", 0x397EE,
     (0xF07C, 0x2DDE, 0x4C04, 0xA20C, 0xFF7C, 0x2DC1, 0xA209)),
    ("availability predicates", 0x3B150,
     (0xAC01, 0xE181, 0xF07C, 0x2DB9, 0x4C02, 0xA306, 0xE700, 0x4C05, 0xA303)),
    ("dispatcher-1 table", 0x39F48,
     (0xAFE7, 0x0002, 0xAFB0, 0x0002, 0xAFCF, 0x0002,
      0xAFD4, 0x0002, 0xAFD8, 0x0002, 0xAFE2)),
    ("dispatcher-2 table", 0x3A04A,
     (0xB04F, 0x0002, 0xB031, 0x0002, 0xB04E, 0x0002,
      0xB04F, 0x0002, 0xB031, 0x0002, 0xB04E)),
)

# Arms and downstream code that must be byte-identical to stock afterwards.
UNTOUCHED = (
    ("d1 code-0 arm", 0x2AFE7, 3),
    ("d1 code-1 arm", 0x2AFB0, 31),
    ("d1 code-2 arm", 0x2AFCF, 5),
    ("d1 code-3 arm", 0x2AFD4, 4),
    ("d1 code-4 arm", 0x2AFD8, 10),
    ("d1 code-5 arm", 0x2AFE2, 5),
    ("d2 build arm", 0x2B031, 14),
    ("d2 bleed arm", 0x2B04E, 6),
    ("d2 preamble", 0x2B00B, 8),
    ("rate limiter", 0x2AFEA, 33),
    ("demand stage", 0x2B054, 40),
    ("torque function", 0x2B07C, 60),
)

# OEM telemetry sites: must remain OEM in this build.
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
    # No telemetry in this build: both sites must still be OEM going in.
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

    # NO telemetry: these must be byte-identical to OEM in the output.
    base.expect_bytes(new, POINTER_OFF, _w(base.OEM_POINTER_WORDS),
                      "built: callback pointer must stay OEM")
    base.expect_bytes(new, HANDLER_OFF, _w((base.OEM_CAVE_WORD,) * 60),
                      "built: code cave must stay OEM")

    # Only entry 5 may move in either dispatch table.
    for table in (0x2AFA4, 0x2B025):
        for code in range(5):
            offset = (table + 2 * code - base.BLK1_PWORD_BASE) * 2
            if base._u16le(new, offset) != base._u16le(old_blk, offset):
                raise base.BuildError(f"table {table:#X} entry {code} changed")
        stride = (table + 11 - base.BLK1_PWORD_BASE) * 2
        if base._u16le(new, stride) != base._u16le(old_blk, stride):
            raise base.BuildError(f"table {table:#X} stride word changed")

    for label, pword, count in UNTOUCHED:
        offset = (pword - base.BLK1_PWORD_BASE) * 2
        if new[offset:offset + count * 2] != old_blk[offset:offset + count * 2]:
            raise base.BuildError(f"{label} modified")

    allowed = set()
    expected = 0
    for _label, offset, old, patched in PATCHES:
        allowed |= set(range(offset, offset + len(patched) * 2))
        # Two of the patches (AFE2->AFD8, B04E->B031) differ in their LOW BYTE
        # only, so count bytes that actually differ rather than words * 2.
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


def _diff_vs_joint11(stock, cal, built):
    """Control words must match the driven joint11 image exactly."""
    reference = ROOT / "CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF"
    if not reference.exists():
        return None
    j11 = base.parse_vbf(reference).block_at(base.BLK1_FLASH).data
    new = built.block_at(base.BLK1_FLASH).data
    for label, offset, _o, patched in PATCHES:
        got = struct.unpack_from("<%dH" % len(patched), j11, offset)
        if got != patched:
            raise base.BuildError(
                f"joint11 disagrees on {label}: {got} != {patched}")
    # And the final image must NOT carry joint11's telemetry.
    if new[HANDLER_OFF:HANDLER_OFF + 120] == j11[HANDLER_OFF:HANDLER_OFF + 120]:
        raise base.BuildError("final image still carries the joint11 handler")
    return True


def selftest():
    try:
        stock = base.parse_vbf(base.DEFAULT_STOCK)
        cal = base.parse_vbf(base.DEFAULT_CAL)
        first = build_bytes(stock, cal)
        if first.output != build_bytes(stock, cal).output:
            raise base.BuildError("nondeterministic output")
        parsed = base.parse_vbf_bytes(first.output, Path("final-selftest.vbf"))
        n = audit(stock, parsed, cal)
        # 9 patch words, but two differ in the low byte only -> 18 - 2 = 16,
        # plus 2 checksum words = 22 bytes total.
        if n != 22:
            raise base.BuildError(f"expected 22 changed bytes, got {n}")
        matched = _diff_vs_joint11(stock, cal, parsed)
        note = "control words match driven joint11" if matched else "joint11 absent, skipped"
        print(f"SELFTEST: ALL PASS ({n} bytes changed = 9 patch words + 2 checksums; {note})")
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
        _diff_vs_joint11(stock, cal, parsed)
        base.write_atomic(args.output, result.output)
        readback = base.parse_vbf(args.output)
        audit(stock, readback, cal)
        if readback.raw != result.output:
            raise base.BuildError("read-back mismatch")

        print(f"wrote: {args.output}")
        print(f"sha256: {base.sha256(result.output)}")
        print("\nPATCHES APPLIED (5 sites, 9 words):")
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
