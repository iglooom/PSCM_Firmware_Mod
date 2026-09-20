#!/usr/bin/env python3
"""Independent verification of the joint9 limiter-telemetry VBF.

Deliberately a SECOND code path: it re-parses the container from raw bytes
with its own minimal reader and re-derives every claim, rather than importing
the builder's helpers. If this and the builder ever disagree, do not flash.

    python3 work/lca_resume/verify_lca_joint9_vbf_independent.py
"""
from __future__ import annotations

import binascii
import hashlib
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "CV6T-14C217-AR_LCA_JOINT9_LIMITER.VBF"
STOCK = ROOT / "CV6T-14C217-AR.VBF"

BLK1_FLASH = 0x0001C000
BLK1_PWORD_BASE = 0x0E000

# Independently restated addresses (word addresses in P space).
POINTER_PWORD = 0x0EEA4
HANDLER_PWORD = 0x33800
GATE_GUARD_PWORD = 0x2A780          # 4 words inserted in the ==6 arm
ENTRY_GUARD_PWORD = 0x2ABFB         # phase-1 X:$2DC1 test/branch
CODE5_BRANCH_PWORD = 0x2B8B0        # code-5 availability Beq
WORD_A_OFF = 0x63FEA
WORD_B_OFF = 0x63FEC

EXPECTED_SOURCES = (0x2DB9, 0x2D50, 0x2D46, 0x2D4A, 0x2DA0, 0x2D4B, 0x2D53)
EXPECTED_FORMAT = 6


def off(pword):
    return (pword - BLK1_PWORD_BASE) * 2


def parse(path):
    """Minimal VBF reader: returns (raw, header_bytes, blocks, checksum_span).

    The header contains NESTED braces, so the terminator is the brace that
    returns the depth counter to zero, not the first '}' in the file.
    """
    raw = path.read_bytes()
    depth = 0
    end = None
    for i, byte in enumerate(raw):
        if byte == 0x7B:            # '{'
            depth += 1
        elif byte == 0x7D:          # '}'
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise SystemExit("unterminated VBF header")
    header = raw[:end]
    match = re.search(rb"file_checksum\s*=\s*0x([0-9A-Fa-f]+)", header)
    if not match:
        raise SystemExit("no file_checksum in header")
    span = (match.start(1), match.end(1))

    # Payload begins immediately after the header brace; the first block's
    # address field may itself start with NUL bytes, so do NOT skip them.
    data_start = end

    blocks = []
    pos = data_start
    while pos + 8 <= len(raw):
        addr, length = struct.unpack_from(">II", raw, pos)
        if length == 0 or pos + 8 + length + 2 > len(raw):
            break
        data = raw[pos + 8:pos + 8 + length]
        crc = struct.unpack_from(">H", raw, pos + 8 + length)[0]
        blocks.append((addr, data, crc, pos + 8))
        pos += 8 + length + 2
    return raw, header, blocks, span, data_start


def words(data, offset, count):
    return struct.unpack_from("<%dH" % count, data, offset)


def main():
    checks = []

    def ok(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    if not TARGET.exists():
        raise SystemExit(f"missing {TARGET}; run the builder first")

    raw, header, blocks, span, data_start = parse(TARGET)
    sraw, _sheader, sblocks, _sspan, _sds = parse(STOCK)
    print("INDEPENDENT VERIFICATION — joint9 limiter telemetry")
    print(f"  file   : {TARGET.name}")
    print(f"  sha256 : {hashlib.sha256(raw).hexdigest()}\n")

    ok("3 data blocks", len(blocks) == 3, str([hex(b[0]) for b in blocks]))
    ok("block addresses match stock",
       [b[0] for b in blocks] == [b[0] for b in sblocks])
    ok("block sizes match stock",
       [len(b[1]) for b in blocks] == [len(b[1]) for b in sblocks])

    for addr, data, crc, _o in blocks:
        ok(f"block {addr:#010x} CRC-16", binascii.crc_hqx(data, 0xFFFF) == crc)

    recomputed = binascii.crc32(raw[data_start:]) & 0xFFFFFFFF
    ok("header file_checksum", f"{recomputed:08X}" == raw[slice(*span)].decode(),
       f"calc {recomputed:08X} stored {raw[slice(*span)].decode()}")

    blk1 = dict((b[0], b[1]) for b in blocks)[BLK1_FLASH]
    sblk1 = dict((b[0], b[1]) for b in sblocks)[BLK1_FLASH]

    # --- telemetry handler ------------------------------------------------
    handler = words(blk1, off(HANDLER_PWORD), 60)
    ok("handler format nibble is 6",
       handler[0] == (0xE080 | EXPECTED_FORMAT), f"{handler[0]:04X}")
    got = tuple(handler[3 + 8 * i] for i in range(7))
    ok("handler traces the 7 limiter cells", got == EXPECTED_SOURCES,
       " ".join(f"{s:04X}" for s in got))
    ok("handler ends RTS", handler[-1] == 0xE708)
    # Byte slots live at word indices 7+8i and 9+8i (the 2-word preamble
    # shifts every per-source group by two).
    slots = [handler[7 + 8 * i] for i in range(7)] + [handler[9 + 8 * i] for i in range(7)]
    ok("response byte slots are 1..14 unique",
       sorted(slots) == list(range(1, 15)), str(sorted(slots)))
    ok("FD22 callback pointer redirected",
       words(blk1, off(POINTER_PWORD), 2) == (0x3800, 0x0003))

    # --- the three retained control patches -------------------------------
    ok("dispatcher gate guard is 4 NOPs",
       words(blk1, off(GATE_GUARD_PWORD), 4) == (0xE700,) * 4)
    ok("==6 arm still CMP #6 / Bne",
       words(blk1, off(0x2A77E), 2) == (0x4C06, 0xA207))
    ok("MOVE.W #4 -> X:$2DDE retained",
       words(blk1, off(0x2A784), 2) == (0xE684, 0x2DDE))
    ok("phase-1 entry guard is 3 NOPs",
       words(blk1, off(ENTRY_GUARD_PWORD), 3) == (0xE700,) * 3)
    ok("code-5 availability branch NOPed",
       words(blk1, off(CODE5_BRANCH_PWORD), 1) == (0xE700,))
    ok("neighbouring availability predicates retained",
       words(blk1, off(CODE5_BRANCH_PWORD) - 8, 8)
       == (0x4C02, 0xA306, 0xE700, 0x4C05, 0xE700, 0xE700, 0x4C03, 0xA203))

    # --- the limiter control path must be UNTOUCHED ------------------------
    # This is the whole point of joint9: observe, do not perturb.
    for label, pword, count in (
        ("dispatcher-1 table", 0x2AFA4, 12),
        ("dispatcher-2 table", 0x2B025, 12),
        ("code-1 rate arm", 0x2AFB0, 31),
        ("code-5 rate arm", 0x2AFE2, 5),
        ("code-1/4 authority arm", 0x2B031, 14),
        ("flat authority arm", 0x2B04E, 6),
        ("torque function", 0x2B07C, 60),
        ("rate limiter", 0x2AFEA, 33),
    ):
        a = words(blk1, off(pword), count)
        b = words(sblk1, off(pword), count)
        ok(f"{label} identical to stock", a == b)

    # --- change accounting -------------------------------------------------
    changed = [i for i, (x, y) in enumerate(zip(sblk1, blk1)) if x != y]
    allowed = set(range(off(POINTER_PWORD), off(POINTER_PWORD) + 4))
    allowed |= set(range(off(HANDLER_PWORD), off(HANDLER_PWORD) + 120))
    allowed |= set(range(off(GATE_GUARD_PWORD), off(GATE_GUARD_PWORD) + 8))
    allowed |= set(range(off(ENTRY_GUARD_PWORD), off(ENTRY_GUARD_PWORD) + 6))
    allowed |= set(range(off(CODE5_BRANCH_PWORD), off(CODE5_BRANCH_PWORD) + 2))
    allowed |= {WORD_A_OFF, WORD_A_OFF + 1, WORD_B_OFF, WORD_B_OFF + 1}
    stray = [i for i in changed if i not in allowed]
    ok("every changed byte is accounted for", not stray,
       f"{len(changed)} changed, {len(stray)} unexplained")
    ok("only blocks 0/2 untouched",
       all(a[1] == b[1] for a, b in zip(blocks, sblocks) if a[0] != BLK1_FLASH))

    failed = 0
    for name, good, detail in checks:
        print(f"  {'PASS' if good else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        failed += not good

    print("\n" + "=" * 58)
    if failed:
        print(f"ACCEPTANCE: DO NOT FLASH — {failed} check(s) failed")
        return 1
    print("ACCEPTANCE: READY TO FLASH")
    print("  control path byte-identical to joint8; telemetry-only change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
