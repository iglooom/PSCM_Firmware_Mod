#!/usr/bin/env python3
"""Build the joint9 LCA authority-limiter telemetry firmware.

Retains every proven joint8 patch unchanged and adds FD22 **format 6**,
which reports the per-state limiter chain instead of the availability chain.

Patches carried forward (all previously flashed and driven):
  1. dispatcher gate   P:$2A77E guard -> 4x E700   (joint2, opens LCA state 4)
  2. phase-1 entry     P:$2ABFB..FD   -> 3x E700   (joint4, reaches torque path)
  3. code-5 availability P:$2B8B0 A303 -> E700     (joint8, sustains request 6)

New in joint9: telemetry only. No control-path instruction is changed, so
behaviour should be identical to the joint8 image the user already drove.

    python3 work/lca_resume/build_lca_joint9_limiter_vbf.py --selftest
    python3 work/lca_resume/build_lca_joint9_limiter_vbf.py
"""
from __future__ import annotations

import argparse
import binascii
import struct
import sys
from pathlib import Path

import build_fd22_snapshot_vbf as base
import build_lca_gate_fd22_vbf as gatebase
import build_lca_entry_bypass_fd22_vbf as entrybase
import build_lca_code5_availability_vbf as code5base

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "CV6T-14C217-AR_LCA_JOINT9_LIMITER.VBF"

FORMAT_ID = 6

# The limiter chain, in dataflow order. Exactly 7 fit the 60-word cave.
#
#   X:$2DB9  per-state code   -> selects which dispatcher arm runs
#   X:$2D50  rate-limit source for codes 4/5 -- THE headline word.
#            BV6T recomputes its equivalent (X:$23A7) every cycle from a
#            producer; CV6T writes a literal 0x0400 once at init. If this
#            reads a fixed 0x0400 across a whole LCA episode, the
#            constant-source mechanism is confirmed on the car.
#   X:$2D46  rate limit actually in force (dispatcher 1 output)
#   X:$2D4A  authority increment (dispatcher 2 output)
#   X:$2DA0  runtime schedule input used by the code-1/4 arm
#   X:$2D4B  integrator state
#   X:$2D53  torque accumulator (the output already trended in joint8)
TRACE_SOURCES = (0x2DB9, 0x2D50, 0x2D46, 0x2D4A, 0x2DA0, 0x2D4B, 0x2D53)

FIELD_NAMES = (
    "per_state_code",
    "rate_limit_source",
    "rate_limit",
    "authority_increment",
    "schedule_input",
    "integrator",
    "torque_accumulator",
)


def make_handler(sources=TRACE_SOURCES, format_id=FORMAT_ID):
    """Emit the 60-word snapshot cave for `sources`.

    Same shape as the joint8 handler, with the format nibble changed. Each
    source costs 8 words: read, move, split into two response bytes.
    """
    if len(sources) != 7:
        raise base.BuildError(f"expected 7 sources, got {len(sources)}")
    if not 0 <= format_id <= 0xF:
        raise base.BuildError("format id must fit one nibble")

    words = [0xE080 | format_id, 0xD0B6]
    offset = 1
    for src in sources:
        words += [0xF07C, src, 0x8110, 0x5C28, 0xD0E6, offset, 0xD1E6, offset + 1]
        offset += 2
    words += [0xE58F, 0xE708]

    if len(words) != 60:
        raise base.BuildError(f"handler length {len(words)} != 60")
    return tuple(words)


TRACE_HANDLER = make_handler()


def build_bytes(stock, cal):
    """Return a BuildResult for the joint9 image."""
    base.verify_oem_inputs(stock, cal)
    block0 = stock.block_at(base.BLK0_FLASH).data
    record = stock.block_at(base.BLK1_FLASH)
    block1 = bytearray(record.data)

    # Assert every OEM byte we are about to overwrite. A silent mismatch here
    # is how a patch lands on the wrong instruction.
    base.expect_bytes(block1, base.POINTER_OFF,
                      struct.pack("<2H", *base.OEM_POINTER_WORDS), "FD22 pointer")
    base.expect_bytes(block1, base.HANDLER_OFF,
                      struct.pack("<60H", *([base.OEM_CAVE_WORD] * 60)), "handler cave")
    base.expect_bytes(block1, gatebase.GATE_ARM_OFF,
                      struct.pack("<11H", *gatebase.OEM_GATE_ARM), "dispatcher gate")
    base.expect_bytes(block1, entrybase.ENTRY_GUARD_OFF,
                      struct.pack("<3H", *entrybase.OEM_ENTRY_GUARD), "entry guard")
    base.expect_bytes(block1, code5base.CODE5_BRANCH_OFF,
                      struct.pack("<H", code5base.OEM_CODE5_BRANCH),
                      "code-5 availability branch")
    base.expect_bytes(block1, base.WORD_A_OFF, struct.pack("<H", base.OEM_WORD_A), "word A")
    base.expect_bytes(block1, base.WORD_B_OFF, struct.pack("<H", base.OEM_WORD_B), "word B")

    # Telemetry.
    block1[base.POINTER_OFF:base.POINTER_OFF + 4] = struct.pack("<2H", *base.NEW_POINTER_WORDS)
    block1[base.HANDLER_OFF:base.HANDLER_OFF + 120] = struct.pack("<60H", *TRACE_HANDLER)
    # Proven control-path patches, carried forward verbatim.
    block1[gatebase.GATE_GUARD_OFF:gatebase.GATE_GUARD_OFF + 8] = struct.pack("<4H", *([0xE700] * 4))
    block1[entrybase.ENTRY_GUARD_OFF:entrybase.ENTRY_GUARD_OFF + 6] = struct.pack("<3H", *([0xE700] * 3))
    struct.pack_into("<H", block1, code5base.CODE5_BRANCH_OFF, code5base.NEW_CODE5_BRANCH)

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
    """Independent re-check of a parsed built image. Raises on any surprise."""
    base.verify_container(built)
    if built.data_start != stock.data_start:
        raise base.BuildError("data_start changed")
    topology = [(b.address, len(b.data), b.data_offset, b.crc_offset) for b in built.blocks]
    if topology != [(b.address, len(b.data), b.data_offset, b.crc_offset) for b in stock.blocks]:
        raise base.BuildError("block topology changed")

    old = stock.block_at(base.BLK1_FLASH).data
    new = built.block_at(base.BLK1_FLASH).data

    base.expect_bytes(new, base.POINTER_OFF,
                      struct.pack("<2H", *base.NEW_POINTER_WORDS), "built pointer")
    base.expect_bytes(new, base.HANDLER_OFF,
                      struct.pack("<60H", *TRACE_HANDLER), "built handler")
    base.expect_bytes(new, gatebase.GATE_ARM_OFF,
                      struct.pack("<11H", *gatebase.OPEN_GATE_ARM), "built dispatcher gate")
    base.expect_bytes(new, entrybase.ENTRY_GUARD_OFF,
                      struct.pack("<3H", *([0xE700] * 3)), "built entry bypass")
    base.expect_bytes(new, code5base.CODE5_BRANCH_OFF,
                      struct.pack("<H", code5base.NEW_CODE5_BRANCH),
                      "built code-5 availability patch")
    # Neighbouring availability predicates must survive untouched.
    base.expect_bytes(new, code5base.CODE5_BRANCH_OFF - 8,
                      struct.pack("<8H", 0x4C02, 0xA306, 0xE700, 0x4C05,
                                  0xE700, 0xE700, 0x4C03, 0xA203),
                      "retained neighboring predicates")

    allowed = set()
    for start, length in (
        (base.POINTER_OFF, 4),
        (base.HANDLER_OFF, 120),
        (gatebase.GATE_GUARD_OFF, 8),
        (entrybase.ENTRY_GUARD_OFF, 6),
        (code5base.CODE5_BRANCH_OFF, 2),
        (base.WORD_A_OFF, 2),
        (base.WORD_B_OFF, 2),
    ):
        allowed |= set(range(start, start + length))
    changed = {i for i, (a, b) in enumerate(zip(old, new)) if a != b}
    if changed - allowed:
        raise base.BuildError(f"{len(changed - allowed)} unexpected payload changes")

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


def _diff_vs_joint8(stock, cal, built):
    """Joint9 must differ from joint8 ONLY in the handler and the checksums."""
    joint8 = base.parse_vbf_bytes(code5base.build_bytes(stock, cal).output,
                                  Path("joint8-reference.vbf"))
    a = joint8.block_at(base.BLK1_FLASH).data
    b = built.block_at(base.BLK1_FLASH).data
    changed = {i for i, (x, y) in enumerate(zip(a, b)) if x != y}
    allowed = set(range(base.HANDLER_OFF, base.HANDLER_OFF + 120))
    allowed |= set(range(base.WORD_A_OFF, base.WORD_A_OFF + 2))
    allowed |= set(range(base.WORD_B_OFF, base.WORD_B_OFF + 2))
    stray = changed - allowed
    if stray:
        raise base.BuildError(
            f"joint9 changes {len(stray)} control-path bytes vs joint8; expected none")
    return len(changed)


def selftest():
    try:
        handler = make_handler()
        if len(handler) != 60:
            raise base.BuildError("handler not 60 words")
        if handler[0] != 0xE086:
            raise base.BuildError(f"format nibble wrong: {handler[0]:04X}")
        if len(set(TRACE_SOURCES)) != 7:
            raise base.BuildError("duplicate trace source")
        # Every source must be a lane runtime cell, never calibration.
        for src in TRACE_SOURCES:
            if not 0x2D00 <= src < 0x2E00:
                raise base.BuildError(f"source X:${src:04X} outside lane RAM")

        stock = base.parse_vbf(base.DEFAULT_STOCK)
        cal = base.parse_vbf(base.DEFAULT_CAL)
        first = build_bytes(stock, cal)
        second = build_bytes(stock, cal)
        if first.output != second.output:
            raise base.BuildError("nondeterministic output")

        parsed = base.parse_vbf_bytes(first.output, Path("joint9-selftest.vbf"))
        audit(stock, parsed, cal)
        n = _diff_vs_joint8(stock, cal, parsed)
        print(f"SELFTEST: ALL PASS (telemetry-only delta vs joint8: {n} bytes)")
        return 0
    except Exception as exc:  # noqa: BLE001 - report any failure verbatim
        print(f"SELFTEST FAILURE: {exc}", file=sys.stderr)
        return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
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
        _diff_vs_joint8(stock, cal, parsed)
        base.write_atomic(args.output, result.output)
        readback = base.parse_vbf(args.output)
        audit(stock, readback, cal)
        if readback.raw != result.output:
            raise base.BuildError("read-back mismatch")

        print(f"wrote: {args.output}")
        print(f"sha256: {base.sha256(result.output)}")
        print("control patches retained: dispatcher gate + phase-1 entry + code-5 availability")
        print("control path vs joint8: IDENTICAL (telemetry-only change)")
        print(f"FD22 format {FORMAT_ID}: " + ", ".join(
            f"{n}=X:${s:04X}" for n, s in zip(FIELD_NAMES, TRACE_SOURCES)))
        print(f"word A/B: {result.new_word_a:04X}/{result.new_word_b:04X}")
        print(f"block CRC/file checksum: {result.new_block_crc:04X}/{result.new_file_checksum:08X}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
