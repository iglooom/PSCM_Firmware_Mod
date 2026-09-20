#!/usr/bin/env python3
"""Independent verification of the joint11 LCA ramp-fix VBF.

A SECOND code path: its own container reader, its own restated addresses.
If this disagrees with the builder, do not flash.

Two control patches must both be present and nothing else may move:

    dispatcher-1 code-5  P:$2AFAE  AFE2 -> AFD8   (joint11, ramp increment)
    dispatcher-2 code-5  P:$2B02F  B04E -> B031   (joint10, authority +40)

    python3 work/lca_resume/verify_lca_joint11_vbf_independent.py
"""
from __future__ import annotations

import binascii
import hashlib
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF"
STOCK = ROOT / "CV6T-14C217-AR.VBF"

BLK1_FLASH = 0x0001C000
BLK1_PWORD_BASE = 0x0E000

POINTER_PWORD = 0x0EEA4
HANDLER_PWORD = 0x33800
GATE_GUARD_PWORD = 0x2A780
ENTRY_GUARD_PWORD = 0x2ABFB
CODE5_BRANCH_PWORD = 0x2B8B0
DISPATCH1_TABLE_PWORD = 0x2AFA4
DISPATCH2_TABLE_PWORD = 0x2B025
WORD_A_OFF = 0x63FEA
WORD_B_OFF = 0x63FEC

D1_OEM_CODE5 = 0xAFE2
D1_NEW_CODE5 = 0xAFD8
D2_OEM_CODE5 = 0xB04E
D2_NEW_CODE5 = 0xB031

EXPECTED_SOURCES = (0x2DB9, 0x2D47, 0x2D54, 0x2D49, 0x2D46, 0x2D4B, 0x2D53)
EXPECTED_FORMAT = 7

# The zero-increment instruction the fix routes around: MOVE.W #0,Y0.
ZERO_Y0_OPCODE = 0xE580


def off(pword):
    return (pword - BLK1_PWORD_BASE) * 2


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
        raise SystemExit("unterminated VBF header")
    match = re.search(rb"file_checksum\s*=\s*0x([0-9A-Fa-f]+)", raw[:end])
    if not match:
        raise SystemExit("no file_checksum")
    span = (match.start(1), match.end(1))
    blocks, pos = [], end
    while pos + 8 <= len(raw):
        addr, length = struct.unpack_from(">II", raw, pos)
        if length == 0 or pos + 8 + length + 2 > len(raw):
            break
        blocks.append((addr, raw[pos + 8:pos + 8 + length],
                       struct.unpack_from(">H", raw, pos + 8 + length)[0]))
        pos += 8 + length + 2
    return raw, blocks, span, end


def words(data, offset, count):
    return struct.unpack_from("<%dH" % count, data, offset)


def main():
    checks = []

    def ok(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    if not TARGET.exists():
        raise SystemExit(f"missing {TARGET}; run the builder first")

    raw, blocks, span, data_start = parse(TARGET)
    _sraw, sblocks, _sspan, _sds = parse(STOCK)
    print("INDEPENDENT VERIFICATION — joint11 LCA ramp fix")
    print(f"  file   : {TARGET.name}")
    print(f"  sha256 : {hashlib.sha256(raw).hexdigest()}\n")

    ok("3 data blocks", len(blocks) == 3)
    ok("block addresses match stock", [b[0] for b in blocks] == [b[0] for b in sblocks])
    ok("block sizes match stock",
       [len(b[1]) for b in blocks] == [len(b[1]) for b in sblocks])
    for addr, data, crc in blocks:
        ok(f"block {addr:#010x} CRC-16", binascii.crc_hqx(data, 0xFFFF) == crc)
    ok("header file_checksum",
       f"{binascii.crc32(raw[data_start:]) & 0xFFFFFFFF:08X}" == raw[slice(*span)].decode())

    blk1 = dict((b[0], b[1]) for b in blocks)[BLK1_FLASH]
    sblk1 = dict((b[0], b[1]) for b in sblocks)[BLK1_FLASH]

    # --- both dispatch tables ---------------------------------------------
    for label, table, oem, new in (
        ("dispatcher-1", DISPATCH1_TABLE_PWORD, D1_OEM_CODE5, D1_NEW_CODE5),
        ("dispatcher-2", DISPATCH2_TABLE_PWORD, D2_OEM_CODE5, D2_NEW_CODE5),
    ):
        cur = words(blk1, off(table), 11)
        stock_cur = words(sblk1, off(table), 11)
        entries = [cur[2 * i] for i in range(6)]
        stock_entries = [stock_cur[2 * i] for i in range(6)]
        ok(f"{label}: code-5 was OEM {oem:04X}", stock_entries[5] == oem)
        ok(f"{label}: code-5 is now {new:04X}", entries[5] == new,
           f"{stock_entries[5]:04X} -> {entries[5]:04X}")
        ok(f"{label}: code-5 now matches code-4 arm", entries[5] == entries[4])
        ok(f"{label}: codes 0-4 UNCHANGED", entries[:5] == stock_entries[:5],
           " ".join(f"{e:04X}" for e in entries[:5]))
        ok(f"{label}: stride words unchanged",
           [cur[2 * i + 1] for i in range(5)] == [stock_cur[2 * i + 1] for i in range(5)])

    # The zero-increment arm must still EXIST untouched -- the fix routes
    # around it, it does not rewrite it. Codes 0/2/3 still use it.
    arm5 = words(blk1, off(0x2AFE2), 5)
    ok("d1 code-5 arm still contains MOVE.W #0,Y0", ZERO_Y0_OPCODE in arm5,
       " ".join(f"{x:04X}" for x in arm5))
    ok("d1 code-4 arm calls the divider",
       (words(blk1, off(0x2AFD8), 10))[7:9] == (0xE254, 0x011F))

    # --- all arms and downstream code byte-identical to stock --------------
    for label, pword, count in (
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
        ("divider helper", 0x0011F, 20),
    ):
        a = words(blk1, off(pword), count) if pword >= BLK1_PWORD_BASE else None
        if a is None:
            continue    # divider lives in blk0; not covered by this blk1 audit
        ok(f"{label} identical to stock", a == words(sblk1, off(pword), count))

    # --- retained earlier patches -----------------------------------------
    ok("dispatcher gate guard is 4 NOPs",
       words(blk1, off(GATE_GUARD_PWORD), 4) == (0xE700,) * 4)
    ok("phase-1 entry guard is 3 NOPs",
       words(blk1, off(ENTRY_GUARD_PWORD), 3) == (0xE700,) * 3)
    ok("code-5 availability branch NOPed",
       words(blk1, off(CODE5_BRANCH_PWORD), 1) == (0xE700,))

    handler = words(blk1, off(HANDLER_PWORD), 60)
    ok("telemetry is format 7", handler[0] == (0xE080 | EXPECTED_FORMAT))
    ok("telemetry traces the demand chain",
       tuple(handler[3 + 8 * i] for i in range(7)) == EXPECTED_SOURCES,
       " ".join(f"{handler[3 + 8 * i]:04X}" for i in range(7)))
    slots = [handler[7 + 8 * i] for i in range(7)] + [handler[9 + 8 * i] for i in range(7)]
    ok("response byte slots are 1..14 unique", sorted(slots) == list(range(1, 15)))
    ok("handler ends RTS", handler[-1] == 0xE708)
    ok("FD22 callback pointer redirected",
       words(blk1, off(POINTER_PWORD), 2) == (0x3800, 0x0003))

    # --- change accounting -------------------------------------------------
    changed = [i for i, (x, y) in enumerate(zip(sblk1, blk1)) if x != y]
    allowed = set(range(off(POINTER_PWORD), off(POINTER_PWORD) + 4))
    allowed |= set(range(off(HANDLER_PWORD), off(HANDLER_PWORD) + 120))
    allowed |= set(range(off(GATE_GUARD_PWORD), off(GATE_GUARD_PWORD) + 8))
    allowed |= set(range(off(ENTRY_GUARD_PWORD), off(ENTRY_GUARD_PWORD) + 6))
    allowed |= set(range(off(CODE5_BRANCH_PWORD), off(CODE5_BRANCH_PWORD) + 2))
    allowed |= set(range(off(DISPATCH1_TABLE_PWORD + 10),
                         off(DISPATCH1_TABLE_PWORD + 10) + 2))
    allowed |= set(range(off(DISPATCH2_TABLE_PWORD + 10),
                         off(DISPATCH2_TABLE_PWORD + 10) + 2))
    allowed |= {WORD_A_OFF, WORD_A_OFF + 1, WORD_B_OFF, WORD_B_OFF + 1}
    stray = [i for i in changed if i not in allowed]
    ok("every changed byte accounted for", not stray,
       f"{len(changed)} changed, {len(stray)} unexplained")
    # Exactly two control WORDS changed. Count words, not bytes: both
    # redirects happen to differ only in their low byte (AFE2->AFD8,
    # B04E->B031), so a byte count here would read 2, not 4.
    control_words = []
    for table, oem, new in ((DISPATCH1_TABLE_PWORD, D1_OEM_CODE5, D1_NEW_CODE5),
                            (DISPATCH2_TABLE_PWORD, D2_OEM_CODE5, D2_NEW_CODE5)):
        o = off(table + 10)
        control_words.append((words(sblk1, o, 1)[0], words(blk1, o, 1)[0], oem, new))
    ok("exactly two control words changed, each as specified",
       all(was == oem and now == new for was, now, oem, new in control_words),
       "; ".join(f"{was:04X}->{now:04X}" for was, now, _o, _n in control_words))
    ok("blocks 0 and 2 untouched",
       all(a[1] == b[1] for a, b in zip(blocks, sblocks) if a[0] != BLK1_FLASH))

    failed = 0
    for name, good, detail in checks:
        print(f"  {'PASS' if good else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        failed += not good

    print("\n" + "=" * 62)
    if failed:
        print(f"ACCEPTANCE: DO NOT FLASH — {failed} check(s) failed")
        return 1
    print("ACCEPTANCE: READY TO FLASH")
    print("  TWO control changes now active (joint10 authority + joint11 ramp).")
    print("  Expect MORE steering authority than joint10. Follow the test plan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
