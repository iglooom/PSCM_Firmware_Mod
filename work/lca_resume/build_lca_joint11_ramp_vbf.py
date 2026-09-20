#!/usr/bin/env python3
"""Build the joint11 LCA ramp fix + demand-chain telemetry firmware.

**This image changes CONTROL BEHAVIOUR**, on top of joint10's change.

Two control patches now active:

  joint10  dispatcher-2 code-5  P:$2B02F  B04E -> B031   (authority +40, was -9)
  joint11  dispatcher-1 code-5  P:$2AFAE  AFE2 -> AFD8   (ramp increment computed, was 0)

Why joint11. The rate limiter at P:$2AFEA takes TWO inputs:

    X:$2D46  the clamp   -- measured 1024 in BOTH modes (equal)
    Y0       the per-cycle ramp INCREMENT

Joint9/joint10 instrumented the clamp and never the increment. The arms
differ on the increment:

    code 1 P:$2AFB0   Y0 = divider result   (computed)
    code 4 P:$2AFD8   Y0 = divider result   (computed)   <- LCA entry
    code 5 P:$2AFE2   Y0 = 0                (E580)       <- LCA sustained

The demand is X:$2D49 = (X:$2D54 * X:$2D47) >> 10 at P:$2B054, where X:$2D47
is the ramp. With a zero increment the ramp never grows, so the demand stays
near zero no matter what the control law produces. That gates the ENTIRE LCA
demand and explains the ~50x shortfall joint10 measured.

The fix points code 5 at the code-4 arm. That arm writes the SAME clamp
(X:$2D46 = X:$2D50), so the bound is unchanged; the only difference is a
computed increment instead of a literal zero. Code 4 runs during every LCA
entry, so the arm is proven live in LCA context.

Telemetry becomes FD22 format 7, retargeted at the demand chain.

    python3 work/lca_resume/build_lca_joint11_ramp_vbf.py --selftest
    python3 work/lca_resume/build_lca_joint11_ramp_vbf.py
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
import build_lca_joint10_fix_vbf as j10

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF"

FORMAT_ID = 7

# Dispatcher-1 jump table: 6 entries, stride 2 words, at P:$2AFA4.
DISPATCH1_TABLE_PWORD = 0x2AFA4
CODE5_ENTRY_PWORD = DISPATCH1_TABLE_PWORD + 2 * 5        # P:$2AFAE
CODE4_ENTRY_PWORD = DISPATCH1_TABLE_PWORD + 2 * 4        # P:$2AFAC
CODE5_ENTRY_OFF = (CODE5_ENTRY_PWORD - base.BLK1_PWORD_BASE) * 2   # 0x39F5C

OEM_CODE5_ARM = 0xAFE2      # zero-increment arm P:$2AFE2 (E580 = MOVE.W #0,Y0)
NEW_CODE5_ARM = 0xAFD8      # computed arm      P:$2AFD8, as code 4

# Demand chain. The limiter words are answered; these are the open ones.
#   X:$2DB9  per-state code   (which arm ran)
#   X:$2D47  RAMP VALUE       <- the word this patch is meant to move
#   X:$2D54  control-law output (the other demand factor)
#   X:$2D49  demand = (2D54 * 2D47) >> 10
#   X:$2D46  clamp            (control: must stay 1024, proves bound untouched)
#   X:$2D4B  integrator
#   X:$2D53  torque accumulator
TRACE_SOURCES = (0x2DB9, 0x2D47, 0x2D54, 0x2D49, 0x2D46, 0x2D4B, 0x2D53)

FIELD_NAMES = (
    "per_state_code",
    "ramp",
    "control_law",
    "demand",
    "rate_limit",
    "integrator",
    "torque_accumulator",
)

TRACE_HANDLER = j9.make_handler(TRACE_SOURCES, FORMAT_ID)


def build_bytes(stock, cal):
    base.verify_oem_inputs(stock, cal)
    block0 = stock.block_at(base.BLK0_FLASH).data
    record = stock.block_at(base.BLK1_FLASH)
    block1 = bytearray(record.data)

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
    base.expect_bytes(block1, j10.CODE5_ENTRY_OFF,
                      struct.pack("<H", j10.OEM_CODE5_ARM), "dispatcher-2 code-5 entry")
    base.expect_bytes(block1, CODE5_ENTRY_OFF,
                      struct.pack("<H", OEM_CODE5_ARM), "dispatcher-1 code-5 entry")
    base.expect_bytes(block1, base.WORD_A_OFF, struct.pack("<H", base.OEM_WORD_A), "word A")
    base.expect_bytes(block1, base.WORD_B_OFF, struct.pack("<H", base.OEM_WORD_B), "word B")

    # Refuse if either redirect target is not what code 4 actually uses.
    code4_off = (CODE4_ENTRY_PWORD - base.BLK1_PWORD_BASE) * 2
    if base._u16le(block1, code4_off) != NEW_CODE5_ARM:
        raise base.BuildError("dispatcher-1 code-4 arm is not AFD8; table changed")
    code1_off = (j10.DISPATCH2_TABLE_PWORD + 2 - base.BLK1_PWORD_BASE) * 2
    if base._u16le(block1, code1_off) != j10.NEW_CODE5_ARM:
        raise base.BuildError("dispatcher-2 code-1 arm is not B031; table changed")

    block1[base.POINTER_OFF:base.POINTER_OFF + 4] = struct.pack("<2H", *base.NEW_POINTER_WORDS)
    block1[base.HANDLER_OFF:base.HANDLER_OFF + 120] = struct.pack("<60H", *TRACE_HANDLER)
    block1[gatebase.GATE_GUARD_OFF:gatebase.GATE_GUARD_OFF + 8] = struct.pack("<4H", *([0xE700] * 4))
    block1[entrybase.ENTRY_GUARD_OFF:entrybase.ENTRY_GUARD_OFF + 6] = struct.pack("<3H", *([0xE700] * 3))
    struct.pack_into("<H", block1, code5base.CODE5_BRANCH_OFF, code5base.NEW_CODE5_BRANCH)
    # joint10 fix, carried forward.
    struct.pack_into("<H", block1, j10.CODE5_ENTRY_OFF, j10.NEW_CODE5_ARM)
    # joint11 fix.
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

    base.expect_bytes(new, base.HANDLER_OFF,
                      struct.pack("<60H", *TRACE_HANDLER), "built handler")
    base.expect_bytes(new, gatebase.GATE_ARM_OFF,
                      struct.pack("<11H", *gatebase.OPEN_GATE_ARM), "built dispatcher gate")
    base.expect_bytes(new, entrybase.ENTRY_GUARD_OFF,
                      struct.pack("<3H", *([0xE700] * 3)), "built entry bypass")
    base.expect_bytes(new, code5base.CODE5_BRANCH_OFF,
                      struct.pack("<H", code5base.NEW_CODE5_BRANCH), "built availability patch")
    base.expect_bytes(new, j10.CODE5_ENTRY_OFF,
                      struct.pack("<H", j10.NEW_CODE5_ARM), "built joint10 redirect")
    base.expect_bytes(new, CODE5_ENTRY_OFF,
                      struct.pack("<H", NEW_CODE5_ARM), "built joint11 redirect")

    # Only code 5 may move, in BOTH tables.
    for table, label in ((DISPATCH1_TABLE_PWORD, "dispatcher-1"),
                         (j10.DISPATCH2_TABLE_PWORD, "dispatcher-2")):
        for code in (0, 1, 2, 3, 4):
            offset = (table + 2 * code - base.BLK1_PWORD_BASE) * 2
            if base._u16le(new, offset) != base._u16le(old, offset):
                raise base.BuildError(f"{label} entry for code {code} changed")
        stride = (table + 2 * 5 + 1 - base.BLK1_PWORD_BASE) * 2
        if base._u16le(new, stride) != base._u16le(old, stride):
            raise base.BuildError(f"{label} stride word after code-5 changed")

    # Every arm and the torque/limiter code must remain OEM.
    for label, pword, count in (
        ("d1 code-1 arm", 0x2AFB0, 31),
        ("d1 code-4 arm", 0x2AFD8, 10),
        ("d1 code-5 arm", 0x2AFE2, 5),
        ("d2 build arm", 0x2B031, 14),
        ("d2 bleed arm", 0x2B04E, 6),
        ("rate limiter", 0x2AFEA, 33),
        ("demand stage", 0x2B054, 40),
        ("torque function", 0x2B07C, 60),
    ):
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
        (j10.CODE5_ENTRY_OFF, 2),
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


def _diff_vs_joint10(stock, cal, built):
    """vs joint10: one table word + the new handler + checksums, nothing else."""
    joint10 = base.parse_vbf_bytes(j10.build_bytes(stock, cal).output,
                                   Path("joint10-reference.vbf"))
    a = joint10.block_at(base.BLK1_FLASH).data
    b = built.block_at(base.BLK1_FLASH).data
    changed = {i for i, (x, y) in enumerate(zip(a, b)) if x != y}
    allowed = set(range(CODE5_ENTRY_OFF, CODE5_ENTRY_OFF + 2))
    allowed |= set(range(base.HANDLER_OFF, base.HANDLER_OFF + 120))
    allowed |= {base.WORD_A_OFF, base.WORD_A_OFF + 1,
                base.WORD_B_OFF, base.WORD_B_OFF + 1}
    stray = changed - allowed
    if stray:
        raise base.BuildError(f"joint11 changes {len(stray)} unexpected bytes vs joint10")
    return len(changed)


def selftest():
    try:
        if CODE5_ENTRY_OFF != 0x39F5C:
            raise base.BuildError(f"d1 code-5 offset {CODE5_ENTRY_OFF:#X} != 0x39F5C")
        if NEW_CODE5_ARM == OEM_CODE5_ARM:
            raise base.BuildError("patch is a no-op")
        if TRACE_HANDLER[0] != (0xE080 | FORMAT_ID):
            raise base.BuildError("format nibble wrong")
        if len(set(TRACE_SOURCES)) != 7:
            raise base.BuildError("duplicate trace source")
        for src in TRACE_SOURCES:
            if not 0x2D00 <= src < 0x2E00:
                raise base.BuildError(f"source X:${src:04X} outside lane RAM")

        stock = base.parse_vbf(base.DEFAULT_STOCK)
        cal = base.parse_vbf(base.DEFAULT_CAL)
        first = build_bytes(stock, cal)
        if first.output != build_bytes(stock, cal).output:
            raise base.BuildError("nondeterministic output")

        parsed = base.parse_vbf_bytes(first.output, Path("joint11-selftest.vbf"))
        audit(stock, parsed, cal)
        n = _diff_vs_joint10(stock, cal, parsed)

        blk = parsed.block_at(base.BLK1_FLASH).data
        # code 5 must now match code 4 in d1, and code 1/4 in d2.
        for table, code, expect in ((DISPATCH1_TABLE_PWORD, 5, NEW_CODE5_ARM),
                                    (DISPATCH1_TABLE_PWORD, 4, NEW_CODE5_ARM),
                                    (j10.DISPATCH2_TABLE_PWORD, 5, j10.NEW_CODE5_ARM),
                                    (j10.DISPATCH2_TABLE_PWORD, 1, j10.NEW_CODE5_ARM)):
            offset = (table + 2 * code - base.BLK1_PWORD_BASE) * 2
            if base._u16le(blk, offset) != expect:
                raise base.BuildError(f"table {table:#X} code {code} != {expect:04X}")

        print(f"SELFTEST: ALL PASS (delta vs joint10: {n} bytes = 1 word + handler + checksums)")
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
        _diff_vs_joint10(stock, cal, parsed)
        base.write_atomic(args.output, result.output)
        readback = base.parse_vbf(args.output)
        audit(stock, readback, cal)
        if readback.raw != result.output:
            raise base.BuildError("read-back mismatch")

        print(f"wrote: {args.output}")
        print(f"sha256: {base.sha256(result.output)}")
        print(f"FIX (new): dispatcher-1 code-5 P:${CODE5_ENTRY_PWORD:05X} "
              f"{OEM_CODE5_ARM:04X} -> {NEW_CODE5_ARM:04X}")
        print("     ramp increment is now COMPUTED (was literal 0), so the ramp")
        print("     X:$2D47 can grow and the demand (2D54*2D47)>>10 is no longer gated.")
        print(f"FIX (kept): dispatcher-2 code-5 P:$2B02F B04E -> B031 (joint10)")
        print("control patches retained: dispatcher gate + phase-1 entry + code-5 availability")
        print(f"FD22 format {FORMAT_ID}: " + ", ".join(
            f"{n}=X:${s:04X}" for n, s in zip(FIELD_NAMES, TRACE_SOURCES)))
        print(f"word A/B: {result.new_word_a:04X}/{result.new_word_b:04X}")
        print(f"block CRC/file checksum: {result.new_block_crc:04X}/{result.new_file_checksum:08X}")
        print("\n*** CHANGES STEERING BEHAVIOUR, MORE THAN JOINT10. Read the test plan. ***")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
