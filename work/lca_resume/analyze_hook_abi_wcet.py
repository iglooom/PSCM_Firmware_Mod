#!/usr/bin/env python3
"""Read-only ABI/WCET checks for the proposed P:$2A74B hook.

This script reads the OEM CV6T-14C217-AR image only.  It neither emits nor
patches firmware.  Cycle totals are DSP56800E architectural clock-cycle totals
for the explicitly listed templates; interrupt service time and memory wait
states are outside the model.
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BLK1 = ROOT / "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin"
BLK1_P_BASE = 0x0E000


def load_words() -> tuple[int, ...]:
    data = BLK1.read_bytes()
    return struct.unpack(f"<{len(data) // 2}H", data)


def pwords(words: tuple[int, ...], address: int, count: int) -> tuple[int, ...]:
    start = address - BLK1_P_BASE
    if start < 0 or start + count > len(words):
        raise ValueError(f"P:${address:05X} is outside blk1")
    return words[start : start + count]


def hx(values: tuple[int, ...]) -> str:
    return " ".join(f"{value:04X}" for value in values)


# Manual cycle counts from DSP56800E/EX Core Reference Manual Rev. 0:
# JSR <ABS19> 4 (A-162/A-163), RTS 8 (A-257/A-258), absolute word
# load/store 2 and absolute-to-absolute word move 3 (Tables 4-25/4-26).
FIXED_MAILBOX_5 = [
    ("inner JSR P:$2AE86", 4),
    ("MOVE.W X:$6D06,A", 2),
    ("INC.W A", 1),
    ("MOVE.W A1,X:$6D06", 2),
    ("MOVE.W A1,X:$6600", 2),
    ("MOVE.W X:$2DDE,X:$6601", 3),
    ("MOVE.W X:$2DB9,X:$6602", 3),
    ("MOVE.W X:$2D53,X:$6603", 3),
    ("MOVE.W X:$2D52,X:$6604", 3),
    ("RTS", 8),
]

FIXED_MAILBOX_7 = FIXED_MAILBOX_5[:-1] + [
    ("MOVE.W X:$1CB1,X:$6605", 3),
    ("MOVE.W X:$171B,X:$6606", 3),
    ("RTS", 8),
]

# A deliberately pessimistic design budget, not an encoding claim.  It is
# broken into auditable bounded blocks so later assembled code can replace it.
RING7_BUDGET = [
    ("extra inner JSR plus trampoline RTS", 12),
    ("counter update and publication", 7),
    ("load current write pointer", 2),
    ("seven absolute-load/indirect-store pairs", 21),
    ("publish updated pointer/index", 4),
    ("end/wrap comparisons and branches, worst path", 10),
    ("wrap/valid/freeze metadata, worst path", 20),
    ("pipeline/interlock reserve", 4),
]


def total(items: list[tuple[str, int]]) -> int:
    return sum(cycles for _, cycles in items)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    words = load_words()
    checks = {
        "caller_P_2A743": pwords(words, 0x2A743, 0x14),
        "callee_head_P_2AE86": pwords(words, 0x2AE86, 0x3C),
        "consumer_P_2B8EA": pwords(words, 0x2B8EA, 0x19),
        "consumer_P_2B941": pwords(words, 0x2B941, 0x14),
        "consumer_P_2B955_prologue": pwords(words, 0x2B955, 0x5),
        "consumer_P_2B955_epilogue": pwords(words, 0x2BA46, 0x4),
    }

    expected = {
        "caller_P_2A743": (0xE256, 0xA7D7, 0xE256, 0xAD8C, 0xE256, 0xA8C4,
                            0xE256, 0xAD62, 0xE256, 0xAE86, 0xE256, 0xB8EA,
                            0xE256, 0xBA5A, 0xE256, 0xB8A2, 0xE256, 0xB8CD,
                            0xE708, 0xE256),
        "consumer_P_2B8EA": (0xE256, 0xB941, 0xE256, 0xB955, 0xF07C, 0x2D53),
        "consumer_P_2B941": (0xFF7C, 0x2DDE),
        "consumer_P_2B955_prologue": (0x827B, 0xD22B, 0xD33F, 0x827B, 0x864B),
        "consumer_P_2B955_epilogue": (0x9F7E, 0xF33B, 0xF23B, 0xE708),
    }

    failures: list[str] = []
    for name, prefix in expected.items():
        if checks[name][:len(prefix)] != prefix:
            failures.append(
                f"{name}: expected prefix {hx(prefix)}, got {hx(checks[name][:len(prefix)])}"
            )

    # P:$2AE86 has no local stack allocation/save before its first branch/call.
    if checks["callee_head_P_2AE86"][:2] != (0xFF7C, 0x2D57):
        failures.append("P:$2AE86 entry no longer starts with TST.W X:$2D57")
    if pwords(words, 0x2AEC1, 1) != (0xE708,):
        failures.append("P:$2AE86 no longer ends at RTS P:$2AEC1")

    results = {
        "checks": "PASS" if not failures else "FAIL",
        "failures": failures,
        "oem_hook_words": hx(pwords(words, 0x2A74B, 2)),
        "post_hook_calls": ["P:$2B8EA", "P:$2BA5A", "P:$2B8A2", "P:$2B8CD"],
        "fixed_mailbox_5_added_cycles": total(FIXED_MAILBOX_5),
        "fixed_mailbox_7_added_cycles": total(FIXED_MAILBOX_7),
        "ring7_conservative_design_budget_cycles": total(RING7_BUDGET),
        "extra_peak_stack_words_inline_writer": 2,
        "templates": {
            "fixed_mailbox_5": FIXED_MAILBOX_5,
            "fixed_mailbox_7": FIXED_MAILBOX_7,
            "ring7_budget": RING7_BUDGET,
        },
    }

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"STATIC CHECKS: {results['checks']}")
        for failure in failures:
            print(f"FAIL: {failure}")
        print(f"OEM P:$2A74B: {results['oem_hook_words']}")
        print(f"5-word fixed-mailbox added cost: {total(FIXED_MAILBOX_5)} cycles")
        print(f"7-word fixed-mailbox added cost: {total(FIXED_MAILBOX_7)} cycles")
        print(f"7-word ring conservative design budget: {total(RING7_BUDGET)} cycles")
        print("Extra peak stack for inline writer: 2 words")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
