#!/usr/bin/env python3
"""Build the joint10 LCA authority FIX firmware.

**This image changes CONTROL BEHAVIOUR.** Unlike joint9 (telemetry only), it
alters what the PSCM does during sustained lane centering.

The one-word change, justified by the joint9 measurement:

    dispatcher-2 table, code-5 entry   P:$2B02F   B04E -> B031

Joint9 measured the per-state authority increment X:$2D4A live:

    code 1 (LKA sustained)  arm P:$2B031  ->  +40   builds torque
    code 4 (LCA entry)      arm P:$2B031  ->  +40   builds torque
    code 5 (LCA sustained)  arm P:$2B04E  ->   -9   BLEEDS torque to zero

A negative increment is a decay term, so sustained LCA cannot hold torque.
This patch points code 5 at the SAME arm codes 1 and 4 already use. It is the
minimal change that reuses an OEM code path already proven live in LCA
context (code 4 runs it during every LCA entry), and it edits no constant, so
code 2 (which shares the -9 cell X:$08F6) is unaffected.

Everything else is carried forward from joint9 unchanged, including the
format-6 telemetry, so the same logger and analyzer measure the result.

    python3 work/lca_resume/build_lca_joint10_fix_vbf.py --selftest
    python3 work/lca_resume/build_lca_joint10_fix_vbf.py
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
import build_lca_joint9_limiter_vbf as j9

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "CV6T-14C217-AR_LCA_JOINT10_FIX.VBF"

# Dispatcher-2 jump table: 6 entries, stride 2 words, at P:$2B025.
DISPATCH2_TABLE_PWORD = 0x2B025
CODE5_ENTRY_PWORD = DISPATCH2_TABLE_PWORD + 2 * 5          # P:$2B02F
CODE1_ENTRY_PWORD = DISPATCH2_TABLE_PWORD + 2 * 1          # P:$2B027
CODE5_ENTRY_OFF = (CODE5_ENTRY_PWORD - base.BLK1_PWORD_BASE) * 2   # 0x3A05E

OEM_CODE5_ARM = 0xB04E      # flat bleed arm  P:$2B04E  (X:$08F6 = -9)
NEW_CODE5_ARM = 0xB031      # build arm       P:$2B031  (+40), as code 1 / code 4

# Telemetry is identical to joint9 so the same tools decode both drives.
TRACE_SOURCES = j9.TRACE_SOURCES
TRACE_HANDLER = j9.TRACE_HANDLER
FIELD_NAMES = j9.FIELD_NAMES


def build_bytes(stock, cal):
    base.verify_oem_inputs(stock, cal)
    block0 = stock.block_at(base.BLK0_FLASH).data
    record = stock.block_at(base.BLK1_FLASH)
    block1 = bytearray(record.data)

    # Assert every OEM byte before overwriting it.
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
    base.expect_bytes(block1, CODE5_ENTRY_OFF,
                      struct.pack("<H", OEM_CODE5_ARM), "dispatcher-2 code-5 entry")
    base.expect_bytes(block1, base.WORD_A_OFF, struct.pack("<H", base.OEM_WORD_A), "word A")
    base.expect_bytes(block1, base.WORD_B_OFF, struct.pack("<H", base.OEM_WORD_B), "word B")

    # The redirect target must be exactly what code 1 already uses. If the
    # table ever shifts, this refuses rather than patching a wrong arm.
    code1_off = (CODE1_ENTRY_PWORD - base.BLK1_PWORD_BASE) * 2
    if base._u16le(block1, code1_off) != NEW_CODE5_ARM:
        raise base.BuildError("code-1 arm is not B031; table layout changed")

    # Telemetry (unchanged from joint9).
    block1[base.POINTER_OFF:base.POINTER_OFF + 4] = struct.pack("<2H", *base.NEW_POINTER_WORDS)
    block1[base.HANDLER_OFF:base.HANDLER_OFF + 120] = struct.pack("<60H", *TRACE_HANDLER)
    # Proven control patches carried forward.
    block1[gatebase.GATE_GUARD_OFF:gatebase.GATE_GUARD_OFF + 8] = struct.pack("<4H", *([0xE700] * 4))
    block1[entrybase.ENTRY_GUARD_OFF:entrybase.ENTRY_GUARD_OFF + 6] = struct.pack("<3H", *([0xE700] * 3))
    struct.pack_into("<H", block1, code5base.CODE5_BRANCH_OFF, code5base.NEW_CODE5_BRANCH)
    # THE FIX.
    struct.pack_into("<H", block1, CODE5_ENTRY_OFF, NEW_CODE5_ARM)

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
    base.expect_bytes(new, CODE5_ENTRY_OFF,
                      struct.pack("<H", NEW_CODE5_ARM), "built code-5 dispatch redirect")

    # Only the code-5 entry may move. Every other table slot must be OEM, or
    # we have corrupted the dispatch of an unrelated state.
    for code in (0, 1, 2, 3, 4):
        offset = (DISPATCH2_TABLE_PWORD + 2 * code - base.BLK1_PWORD_BASE) * 2
        if base._u16le(new, offset) != base._u16le(old, offset):
            raise base.BuildError(f"dispatcher-2 entry for code {code} changed")
    # The stride filler after the patched entry must be intact.
    if base._u16le(new, CODE5_ENTRY_OFF + 2) != base._u16le(old, CODE5_ENTRY_OFF + 2):
        raise base.BuildError("stride word after code-5 entry changed")
    # Both arms themselves must be untouched OEM code.
    for label, pword, count in (("build arm P:$2B031", 0x2B031, 14),
                                ("bleed arm P:$2B04E", 0x2B04E, 6),
                                ("torque function", 0x2B07C, 60),
                                ("dispatcher-1 table", 0x2AFA4, 12)):
        offset = (pword - base.BLK1_PWORD_BASE) * 2
        if new[offset:offset + count * 2] != old[offset:offset + count * 2]:
            raise base.BuildError(f"{label} modified")

    allowed = set()
    for start, length in (
        (base.POINTER_OFF, 4),
        (base.HANDLER_OFF, 120),
        (gatebase.GATE_GUARD_OFF, 8),
        (entrybase.ENTRY_GUARD_OFF, 6),
        (code5base.CODE5_BRANCH_OFF, 2),
        (CODE5_ENTRY_OFF, 2),
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


def _diff_vs_joint9(stock, cal, built):
    """Joint10 must differ from joint9 ONLY in the one table word + checksums."""
    joint9 = base.parse_vbf_bytes(j9.build_bytes(stock, cal).output,
                                  Path("joint9-reference.vbf"))
    a = joint9.block_at(base.BLK1_FLASH).data
    b = built.block_at(base.BLK1_FLASH).data
    changed = {i for i, (x, y) in enumerate(zip(a, b)) if x != y}
    allowed = set(range(CODE5_ENTRY_OFF, CODE5_ENTRY_OFF + 2))
    allowed |= {base.WORD_A_OFF, base.WORD_A_OFF + 1,
                base.WORD_B_OFF, base.WORD_B_OFF + 1}
    stray = changed - allowed
    if stray:
        raise base.BuildError(
            f"joint10 changes {len(stray)} bytes beyond the single table word")
    return len(changed)


def selftest():
    try:
        if CODE5_ENTRY_OFF != 0x3A05E:
            raise base.BuildError(f"code-5 entry offset {CODE5_ENTRY_OFF:#X} != 0x3A05E")
        if NEW_CODE5_ARM == OEM_CODE5_ARM:
            raise base.BuildError("patch is a no-op")

        stock = base.parse_vbf(base.DEFAULT_STOCK)
        cal = base.parse_vbf(base.DEFAULT_CAL)
        first = build_bytes(stock, cal)
        second = build_bytes(stock, cal)
        if first.output != second.output:
            raise base.BuildError("nondeterministic output")

        parsed = base.parse_vbf_bytes(first.output, Path("joint10-selftest.vbf"))
        audit(stock, parsed, cal)
        n = _diff_vs_joint9(stock, cal, parsed)

        # The patched entry must now equal the code-1 and code-4 entries.
        blk = parsed.block_at(base.BLK1_FLASH).data
        for code in (1, 4, 5):
            offset = (DISPATCH2_TABLE_PWORD + 2 * code - base.BLK1_PWORD_BASE) * 2
            if base._u16le(blk, offset) != NEW_CODE5_ARM:
                raise base.BuildError(f"code {code} entry is not {NEW_CODE5_ARM:04X}")

        print(f"SELFTEST: ALL PASS (delta vs joint9: {n} bytes = 1 word + 2 checksums)")
        return 0
    except Exception as exc:  # noqa: BLE001
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
        _diff_vs_joint9(stock, cal, parsed)
        base.write_atomic(args.output, result.output)
        readback = base.parse_vbf(args.output)
        audit(stock, readback, cal)
        if readback.raw != result.output:
            raise base.BuildError("read-back mismatch")

        print(f"wrote: {args.output}")
        print(f"sha256: {base.sha256(result.output)}")
        print(f"FIX: dispatcher-2 code-5 entry P:${CODE5_ENTRY_PWORD:05X} "
              f"{OEM_CODE5_ARM:04X} -> {NEW_CODE5_ARM:04X}")
        print("     sustained LCA now uses the build arm (+40) that codes 1 and 4 use,")
        print("     instead of the bleed arm (-9). No constant was edited.")
        print("control patches retained: dispatcher gate + phase-1 entry + code-5 availability")
        print(f"FD22 format 6 (same as joint9): " + ", ".join(
            f"{n}=X:${s:04X}" for n, s in zip(FIELD_NAMES, TRACE_SOURCES)))
        print(f"word A/B: {result.new_word_a:04X}/{result.new_word_b:04X}")
        print(f"block CRC/file checksum: {result.new_block_crc:04X}/{result.new_file_checksum:08X}")
        print("\n*** THIS IMAGE CHANGES STEERING BEHAVIOUR. Read the test plan. ***")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
