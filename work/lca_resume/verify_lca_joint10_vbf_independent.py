#!/usr/bin/env python3
"""Independent verification of the joint10 LCA authority-fix VBF.

A SECOND code path: its own container reader, its own restated addresses. If
this disagrees with the builder, do not flash.

This image changes steering behaviour, so the audit is stricter than joint9's:
it proves the code-5 dispatch entry moved to exactly the arm codes 1 and 4
use, that no OTHER dispatch slot moved, and that both arms and the torque
function are byte-identical to stock.

    python3 work/lca_resume/verify_lca_joint10_vbf_independent.py
"""
from __future__ import annotations

import binascii
import hashlib
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "CV6T-14C217-AR_LCA_JOINT10_FIX.VBF"
STOCK = ROOT / "CV6T-14C217-AR.VBF"

BLK1_FLASH = 0x0001C000
BLK1_PWORD_BASE = 0x0E000

POINTER_PWORD = 0x0EEA4
HANDLER_PWORD = 0x33800
GATE_GUARD_PWORD = 0x2A780
ENTRY_GUARD_PWORD = 0x2ABFB
CODE5_BRANCH_PWORD = 0x2B8B0
DISPATCH2_TABLE_PWORD = 0x2B025
WORD_A_OFF = 0x63FEA
WORD_B_OFF = 0x63FEC

OEM_CODE5_ARM = 0xB04E
NEW_CODE5_ARM = 0xB031
EXPECTED_SOURCES = (0x2DB9, 0x2D50, 0x2D46, 0x2D4A, 0x2DA0, 0x2D4B, 0x2D53)
EXPECTED_FORMAT = 6


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
    header = raw[:end]
    match = re.search(rb"file_checksum\s*=\s*0x([0-9A-Fa-f]+)", header)
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
    print("INDEPENDENT VERIFICATION — joint10 LCA authority fix")
    print(f"  file   : {TARGET.name}")
    print(f"  sha256 : {hashlib.sha256(raw).hexdigest()}\n")

    ok("3 data blocks", len(blocks) == 3)
    ok("block addresses match stock", [b[0] for b in blocks] == [b[0] for b in sblocks])
    ok("block sizes match stock",
       [len(b[1]) for b in blocks] == [len(b[1]) for b in sblocks])
    for addr, data, crc in blocks:
        ok(f"block {addr:#010x} CRC-16", binascii.crc_hqx(data, 0xFFFF) == crc)
    recomputed = binascii.crc32(raw[data_start:]) & 0xFFFFFFFF
    ok("header file_checksum", f"{recomputed:08X}" == raw[slice(*span)].decode())

    blk1 = dict((b[0], b[1]) for b in blocks)[BLK1_FLASH]
    sblk1 = dict((b[0], b[1]) for b in sblocks)[BLK1_FLASH]

    # --- THE FIX ----------------------------------------------------------
    table = words(blk1, off(DISPATCH2_TABLE_PWORD), 11)
    stock_table = words(sblk1, off(DISPATCH2_TABLE_PWORD), 11)
    entries = [table[2 * i] for i in range(6)]
    stock_entries = [stock_table[2 * i] for i in range(6)]

    ok("code-5 entry was OEM B04E", stock_entries[5] == OEM_CODE5_ARM)
    ok("code-5 entry is now B031", entries[5] == NEW_CODE5_ARM,
       f"{stock_entries[5]:04X} -> {entries[5]:04X}")
    ok("code-5 now matches code-1 arm", entries[5] == entries[1])
    ok("code-5 now matches code-4 arm", entries[5] == entries[4])
    ok("codes 0-4 dispatch UNCHANGED", entries[:5] == stock_entries[:5],
       " ".join(f"{e:04X}" for e in entries[:5]))
    ok("stride words unchanged",
       [table[2 * i + 1] for i in range(5)] == [stock_table[2 * i + 1] for i in range(5)])

    # Both arms must remain OEM: the fix redirects, it does not rewrite logic.
    for label, pword, count in (
        ("build arm P:$2B031", 0x2B031, 14),
        ("bleed arm P:$2B04E", 0x2B04E, 6),
        ("dispatcher-2 preamble", 0x2B00B, 8),
        ("dispatcher-1 table", 0x2AFA4, 12),
        ("code-1 rate arm", 0x2AFB0, 31),
        ("code-5 rate arm", 0x2AFE2, 5),
        ("torque function", 0x2B07C, 60),
        ("rate limiter", 0x2AFEA, 33),
    ):
        a = words(blk1, off(pword), count)
        b = words(sblk1, off(pword), count)
        ok(f"{label} identical to stock", a == b)

    # --- retained joint8/joint9 patches -----------------------------------
    ok("dispatcher gate guard is 4 NOPs",
       words(blk1, off(GATE_GUARD_PWORD), 4) == (0xE700,) * 4)
    ok("phase-1 entry guard is 3 NOPs",
       words(blk1, off(ENTRY_GUARD_PWORD), 3) == (0xE700,) * 3)
    ok("code-5 availability branch NOPed",
       words(blk1, off(CODE5_BRANCH_PWORD), 1) == (0xE700,))
    handler = words(blk1, off(HANDLER_PWORD), 60)
    ok("telemetry still format 6", handler[0] == (0xE080 | EXPECTED_FORMAT))
    ok("telemetry sources unchanged from joint9",
       tuple(handler[3 + 8 * i] for i in range(7)) == EXPECTED_SOURCES)
    ok("FD22 callback pointer redirected",
       words(blk1, off(POINTER_PWORD), 2) == (0x3800, 0x0003))

    # --- change accounting -------------------------------------------------
    changed = [i for i, (x, y) in enumerate(zip(sblk1, blk1)) if x != y]
    allowed = set(range(off(POINTER_PWORD), off(POINTER_PWORD) + 4))
    allowed |= set(range(off(HANDLER_PWORD), off(HANDLER_PWORD) + 120))
    allowed |= set(range(off(GATE_GUARD_PWORD), off(GATE_GUARD_PWORD) + 8))
    allowed |= set(range(off(ENTRY_GUARD_PWORD), off(ENTRY_GUARD_PWORD) + 6))
    allowed |= set(range(off(CODE5_BRANCH_PWORD), off(CODE5_BRANCH_PWORD) + 2))
    allowed |= set(range(off(DISPATCH2_TABLE_PWORD + 10),
                         off(DISPATCH2_TABLE_PWORD + 10) + 2))
    allowed |= {WORD_A_OFF, WORD_A_OFF + 1, WORD_B_OFF, WORD_B_OFF + 1}
    stray = [i for i in changed if i not in allowed]
    ok("every changed byte accounted for", not stray,
       f"{len(changed)} changed, {len(stray)} unexplained")
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
    print("  single control change: sustained LCA (code 5) now uses the")
    print("  +40 build arm instead of the -9 bleed arm. Steering behaviour")
    print("  WILL change. Follow the joint10 test plan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
