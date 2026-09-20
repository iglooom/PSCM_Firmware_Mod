#!/usr/bin/env python3
"""Build AR application with the LCA gate open and FD22 telemetry retained.

Offline only. Starts from exact OEM CV6T-14C217-AR, neutralises only the
four-word X:$0904 guard in the request-6 dispatcher arm, installs the already
validated FD22 snapshot handler, repairs internal checksum A then B, and repairs
VBF block/file checksums. Stock inputs are never modified.
"""
from __future__ import annotations

import argparse
import binascii
from pathlib import Path
import struct
import sys

import build_fd22_snapshot_vbf as base

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "CV6T-14C217-AR_LCA_GATE_FD22.VBF"

GATE_ARM_PWORD = 0x2A77E
GATE_GUARD_PWORD = 0x2A780
GATE_GUARD_OFF = (GATE_GUARD_PWORD - base.BLK1_PWORD_BASE) * 2
GATE_ARM_OFF = (GATE_ARM_PWORD - base.BLK1_PWORD_BASE) * 2
OEM_GATE_ARM = (
    0x4C06, 0xA207, 0xF07C, 0x0904, 0x4C01, 0xA203,
    0xE684, 0x2DDE, 0xA902, 0xE680, 0x2DDE,
)
OPEN_GATE_ARM = (
    0x4C06, 0xA207, 0xE700, 0xE700, 0xE700, 0xE700,
    0xE684, 0x2DDE, 0xA902, 0xE680, 0x2DDE,
)


def build_bytes(stock: base.ParsedVbf, calibration: base.ParsedVbf) -> base.BuildResult:
    base.verify_oem_inputs(stock, calibration)
    block0 = stock.block_at(base.BLK0_FLASH).data
    old_record = stock.block_at(base.BLK1_FLASH)
    block1 = bytearray(old_record.data)

    base.expect_bytes(block1, base.POINTER_OFF, struct.pack("<2H", *base.OEM_POINTER_WORDS),
                      "FD22 callback pointer")
    base.expect_bytes(block1, base.HANDLER_OFF,
                      struct.pack("<60H", *([base.OEM_CAVE_WORD] * 60)),
                      "FD22 handler cave")
    base.expect_bytes(block1, GATE_ARM_OFF, struct.pack("<11H", *OEM_GATE_ARM),
                      "LCA dispatcher arm")
    base.expect_bytes(block1, base.WORD_A_OFF, struct.pack("<H", base.OEM_WORD_A),
                      "internal word A")
    base.expect_bytes(block1, base.WORD_B_OFF, struct.pack("<H", base.OEM_WORD_B),
                      "internal word B")

    block1[base.POINTER_OFF:base.POINTER_OFF + 4] = struct.pack("<2H", *base.NEW_POINTER_WORDS)
    block1[base.HANDLER_OFF:base.HANDLER_OFF + 120] = struct.pack("<60H", *base.HANDLER_WORDS)
    block1[GATE_GUARD_OFF:GATE_GUARD_OFF + 8] = struct.pack("<4H", *([0xE700] * 4))

    old_a = base._u16le(block1, base.WORD_A_OFF)
    new_a = base.calculate_word_a(block0, bytes(block1))
    struct.pack_into("<H", block1, base.WORD_A_OFF, new_a)

    old_b = base._u16le(block1, base.WORD_B_OFF)
    new_b = base.calculate_word_b(stock, calibration, bytes(block1))
    struct.pack_into("<H", block1, base.WORD_B_OFF, new_b)

    output = bytearray(stock.raw)
    output[old_record.data_offset:old_record.crc_offset] = block1
    new_block_crc = binascii.crc_hqx(block1, 0xFFFF)
    struct.pack_into(">H", output, old_record.crc_offset, new_block_crc)

    old_file = int(stock.raw[slice(*stock.checksum_span)], 16)
    new_file = binascii.crc32(output[stock.data_start:]) & 0xFFFFFFFF
    width = stock.checksum_span[1] - stock.checksum_span[0]
    encoded = f"{new_file:0{width}X}".encode("ascii")
    if len(encoded) != width:
        raise base.BuildError("new file checksum does not fit OEM header field")
    output[slice(*stock.checksum_span)] = encoded

    return base.BuildResult(bytes(output), old_a, new_a, old_b, new_b,
                            old_record.stored_crc, new_block_crc, old_file, new_file)


def audit(stock: base.ParsedVbf, built: base.ParsedVbf,
          calibration: base.ParsedVbf) -> tuple[int, int, int, int]:
    base.verify_container(built)
    if built.data_start != stock.data_start:
        raise base.BuildError("VBF data_start changed")
    if [(b.address, len(b.data), b.header_offset, b.data_offset, b.crc_offset)
            for b in built.blocks] != [
                (b.address, len(b.data), b.header_offset, b.data_offset, b.crc_offset)
                for b in stock.blocks]:
        raise base.BuildError("VBF block topology or offsets changed")

    old = stock.block_at(base.BLK1_FLASH).data
    new = built.block_at(base.BLK1_FLASH).data
    base.expect_bytes(new, base.POINTER_OFF, struct.pack("<2H", *base.NEW_POINTER_WORDS),
                      "built FD22 pointer")
    base.expect_bytes(new, base.HANDLER_OFF, struct.pack("<60H", *base.HANDLER_WORDS),
                      "built FD22 handler")
    base.expect_bytes(new, GATE_ARM_OFF, struct.pack("<11H", *OPEN_GATE_ARM),
                      "built open LCA arm")

    allowed = set(range(base.POINTER_OFF, base.POINTER_OFF + 4))
    allowed |= set(range(base.HANDLER_OFF, base.HANDLER_OFF + 120))
    allowed |= set(range(GATE_GUARD_OFF, GATE_GUARD_OFF + 8))
    allowed |= set(range(base.WORD_A_OFF, base.WORD_A_OFF + 2))
    allowed |= set(range(base.WORD_B_OFF, base.WORD_B_OFF + 2))
    changed = {i for i, (a, b) in enumerate(zip(old, new)) if a != b}
    unexpected = changed - allowed
    if unexpected:
        raise base.BuildError(f"payload audit found {len(unexpected)} unexpected byte(s)")

    gate_changed = sum(old[i:i + 2] != new[i:i + 2]
                       for i in range(GATE_GUARD_OFF, GATE_GUARD_OFF + 8, 2))
    pointer_changed = sum(old[base.POINTER_OFF + 2*i:base.POINTER_OFF + 2*i + 2] !=
                          new[base.POINTER_OFF + 2*i:base.POINTER_OFF + 2*i + 2]
                          for i in range(2))
    handler_changed = sum(old[base.HANDLER_OFF + 2*i:base.HANDLER_OFF + 2*i + 2] !=
                          new[base.HANDLER_OFF + 2*i:base.HANDLER_OFF + 2*i + 2]
                          for i in range(60))
    checksum_changed = sum(old[i:i + 2] != new[i:i + 2]
                           for i in (base.WORD_A_OFF, base.WORD_B_OFF))
    if (gate_changed, pointer_changed, handler_changed, checksum_changed) != (4, 2, 60, 2):
        raise base.BuildError("changed-word cardinality mismatch")

    if base._u16le(new, base.WORD_A_OFF) != base.calculate_word_a(
            built.block_at(base.BLK0_FLASH).data, new):
        raise base.BuildError("internal checksum A invalid")
    if base._u16le(new, base.WORD_B_OFF) != base.calculate_word_b(
            built, calibration, new):
        raise base.BuildError("internal checksum B invalid")

    checksum_header = set(range(*stock.checksum_span))
    header_changes = {i for i in range(stock.data_start) if stock.raw[i] != built.raw[i]}
    if header_changes - checksum_header:
        raise base.BuildError("header changed outside file_checksum")
    for ob, nb in zip(stock.blocks, built.blocks):
        if ob.address != base.BLK1_FLASH and (ob.data != nb.data or ob.stored_crc != nb.stored_crc):
            raise base.BuildError(f"untouched block 0x{ob.address:08X} changed")
    return gate_changed, pointer_changed, handler_changed, checksum_changed


def selftest() -> int:
    checks: list[tuple[str, bool]] = []
    def check(name: str, condition: bool) -> None:
        checks.append((name, bool(condition)))
        print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    try:
        stock = base.parse_vbf(base.DEFAULT_STOCK)
        cal = base.parse_vbf(base.DEFAULT_CAL)
        base.verify_oem_inputs(stock, cal)
        check("strict OEM provenance and stock checksums", True)
        check("guard begins at P:$2A780", GATE_GUARD_PWORD == 0x2A780)
        check("four guard words only", OEM_GATE_ARM[2:6] == (0xF07C, 0x0904, 0x4C01, 0xA203))
        check("outer request-6 compare/branch retained", OPEN_GATE_ARM[:2] == (0x4C06, 0xA207))
        check("state-4 and idle stores retained", OPEN_GATE_ARM[6:] == OEM_GATE_ARM[6:])
        first = build_bytes(stock, cal)
        second = build_bytes(stock, cal)
        check("two builds are byte-identical", first.output == second.output)
        built = base.parse_vbf_bytes(first.output, Path("combined-selftest.vbf"))
        counts = audit(stock, built, cal)
        check("exact gate/pointer/handler/checksum word counts", counts == (4, 2, 60, 2))
        check("combined output container valid", True)
    except (base.BuildError, OSError, ValueError, struct.error) as exc:
        print(f"  FAIL  exception: {exc}")
        checks.append(("exception", False))
    passed = all(v for _, v in checks)
    print(f"SELFTEST: {'ALL PASS' if passed else 'FAILURES'}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        stock = base.parse_vbf(base.DEFAULT_STOCK)
        cal = base.parse_vbf(base.DEFAULT_CAL)
        if args.output.resolve() in {base.DEFAULT_STOCK.resolve(), base.DEFAULT_CAL.resolve()}:
            raise base.BuildError("output must not overwrite an OEM input")
        result = build_bytes(stock, cal)
        parsed = base.parse_vbf_bytes(result.output, args.output)
        counts = audit(stock, parsed, cal)
        base.write_atomic(args.output, result.output)
        readback = base.parse_vbf(args.output)
        if readback.raw != result.output or audit(stock, readback, cal) != counts:
            raise base.BuildError("read-back differs from audited build")
        print(f"wrote: {args.output}")
        print(f"sha256: {base.sha256(result.output)}")
        print("gate: P:$2A780..$2A783 -> E700 E700 E700 E700")
        print("FD22: retained at P:$33800, original 7-field payload")
        print(f"word A: {result.old_word_a:04X} -> {result.new_word_a:04X}")
        print(f"word B: {result.old_word_b:04X} -> {result.new_word_b:04X}")
        print(f"block CRC: {result.old_block_crc:04X} -> {result.new_block_crc:04X}")
        print(f"file checksum: {result.old_file_checksum:08X} -> {result.new_file_checksum:08X}")
        print("AUDIT: only 4 gate + 2 pointer + 60 handler + 2 internal-checksum words differ")
        return 0
    except (base.BuildError, OSError, ValueError, struct.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
