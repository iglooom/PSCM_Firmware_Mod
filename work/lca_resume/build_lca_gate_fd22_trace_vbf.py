#!/usr/bin/env python3
"""Build gate-open CV6T-AR with FD22 format-1 consumer-state telemetry."""
from __future__ import annotations

import argparse
import binascii
from pathlib import Path
import struct
import sys

import build_fd22_snapshot_vbf as base
import build_lca_gate_fd22_vbf as gatebase

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "CV6T-14C217-AR_LCA_GATE_FD22_TRACE.VBF"
TRACE_SOURCES = (0x2DDE, 0x2DB9, 0x2D53, 0x2DC1, 0x2DC3, 0x2DAF, 0x2DB8)


def make_handler() -> tuple[int, ...]:
    # Format byte 1 distinguishes this payload from the original format-0 logger.
    words = [0xE081, 0xD0B6]
    offset = 1
    for source in TRACE_SOURCES:
        words += [0xF07C, source, 0x8110, 0x5C28,
                  0xD0E6, offset, 0xD1E6, offset + 1]
        offset += 2
    words += [0xE58F, 0xE708]
    if len(words) != 60:
        raise base.BuildError(f"handler length is {len(words)}, expected 60")
    return tuple(words)


TRACE_HANDLER = make_handler()


def build_bytes(stock: base.ParsedVbf, calibration: base.ParsedVbf) -> base.BuildResult:
    base.verify_oem_inputs(stock, calibration)
    block0 = stock.block_at(base.BLK0_FLASH).data
    rec = stock.block_at(base.BLK1_FLASH)
    block1 = bytearray(rec.data)
    base.expect_bytes(block1, base.POINTER_OFF, struct.pack("<2H", *base.OEM_POINTER_WORDS), "FD22 pointer")
    base.expect_bytes(block1, base.HANDLER_OFF, struct.pack("<60H", *([base.OEM_CAVE_WORD] * 60)), "handler cave")
    base.expect_bytes(block1, gatebase.GATE_ARM_OFF, struct.pack("<11H", *gatebase.OEM_GATE_ARM), "LCA gate arm")
    base.expect_bytes(block1, base.WORD_A_OFF, struct.pack("<H", base.OEM_WORD_A), "word A")
    base.expect_bytes(block1, base.WORD_B_OFF, struct.pack("<H", base.OEM_WORD_B), "word B")

    block1[base.POINTER_OFF:base.POINTER_OFF + 4] = struct.pack("<2H", *base.NEW_POINTER_WORDS)
    block1[base.HANDLER_OFF:base.HANDLER_OFF + 120] = struct.pack("<60H", *TRACE_HANDLER)
    block1[gatebase.GATE_GUARD_OFF:gatebase.GATE_GUARD_OFF + 8] = struct.pack("<4H", 0xE700, 0xE700, 0xE700, 0xE700)

    old_a = base._u16le(block1, base.WORD_A_OFF)
    new_a = base.calculate_word_a(block0, bytes(block1))
    struct.pack_into("<H", block1, base.WORD_A_OFF, new_a)
    old_b = base._u16le(block1, base.WORD_B_OFF)
    new_b = base.calculate_word_b(stock, calibration, bytes(block1))
    struct.pack_into("<H", block1, base.WORD_B_OFF, new_b)

    output = bytearray(stock.raw)
    output[rec.data_offset:rec.crc_offset] = block1
    new_block_crc = binascii.crc_hqx(block1, 0xFFFF)
    struct.pack_into(">H", output, rec.crc_offset, new_block_crc)
    old_file = int(stock.raw[slice(*stock.checksum_span)], 16)
    new_file = binascii.crc32(output[stock.data_start:]) & 0xFFFFFFFF
    width = stock.checksum_span[1] - stock.checksum_span[0]
    output[slice(*stock.checksum_span)] = f"{new_file:0{width}X}".encode()
    return base.BuildResult(bytes(output), old_a, new_a, old_b, new_b,
                            rec.stored_crc, new_block_crc, old_file, new_file)


def audit(stock: base.ParsedVbf, built: base.ParsedVbf, cal: base.ParsedVbf) -> None:
    base.verify_container(built)
    old = stock.block_at(base.BLK1_FLASH).data
    new = built.block_at(base.BLK1_FLASH).data
    base.expect_bytes(new, base.POINTER_OFF, struct.pack("<2H", *base.NEW_POINTER_WORDS), "built pointer")
    base.expect_bytes(new, base.HANDLER_OFF, struct.pack("<60H", *TRACE_HANDLER), "built handler")
    base.expect_bytes(new, gatebase.GATE_ARM_OFF, struct.pack("<11H", *gatebase.OPEN_GATE_ARM), "built open gate")
    allowed = set(range(base.POINTER_OFF, base.POINTER_OFF + 4))
    allowed |= set(range(base.HANDLER_OFF, base.HANDLER_OFF + 120))
    allowed |= set(range(gatebase.GATE_GUARD_OFF, gatebase.GATE_GUARD_OFF + 8))
    allowed |= set(range(base.WORD_A_OFF, base.WORD_A_OFF + 2))
    allowed |= set(range(base.WORD_B_OFF, base.WORD_B_OFF + 2))
    changed = {i for i, pair in enumerate(zip(old, new)) if pair[0] != pair[1]}
    if changed - allowed:
        raise base.BuildError(f"{len(changed - allowed)} unexpected payload byte changes")
    if base._u16le(new, base.WORD_A_OFF) != base.calculate_word_a(built.blocks[0].data, new):
        raise base.BuildError("checksum A invalid")
    if base._u16le(new, base.WORD_B_OFF) != base.calculate_word_b(built, cal, new):
        raise base.BuildError("checksum B invalid")
    if built.data_start != stock.data_start or [(b.address, len(b.data)) for b in built.blocks] != [(b.address, len(b.data)) for b in stock.blocks]:
        raise base.BuildError("container topology changed")


def selftest() -> int:
    try:
        stock, cal = base.parse_vbf(base.DEFAULT_STOCK), base.parse_vbf(base.DEFAULT_CAL)
        base.verify_oem_inputs(stock, cal)
        assert len(TRACE_HANDLER) == 60 and TRACE_HANDLER[:2] == (0xE081, 0xD0B6)
        assert tuple(TRACE_HANDLER[3 + 8*i] for i in range(7)) == TRACE_SOURCES
        assert TRACE_HANDLER[-2:] == (0xE58F, 0xE708)
        a, b = build_bytes(stock, cal), build_bytes(stock, cal)
        assert a.output == b.output
        audit(stock, base.parse_vbf_bytes(a.output, Path("trace-selftest.vbf")), cal)
        print("SELFTEST: ALL PASS")
        return 0
    except Exception as exc:
        print(f"SELFTEST FAILURE: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    try:
        stock, cal = base.parse_vbf(base.DEFAULT_STOCK), base.parse_vbf(base.DEFAULT_CAL)
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
        print("gate: open; FD22 format: 1; sources: " + ",".join(f"X:${x:04X}" for x in TRACE_SOURCES))
        print(f"word A/B: {result.new_word_a:04X}/{result.new_word_b:04X}")
        print(f"block CRC/file checksum: {result.new_block_crc:04X}/{result.new_file_checksum:08X}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
