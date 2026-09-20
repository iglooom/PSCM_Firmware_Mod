#!/usr/bin/env python3
"""Verify the per-state authority-limiter finding in CV6T-14C217-AR.

Read-only. Re-derives from the binaries every structural claim made in
work/lca_resume/lca_authority_limiter_analysis.md.

  python3 work/lca_resume/verify_authority_limiter.py --selftest
"""
from __future__ import annotations
import argparse
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BLOCKS = [
    ("bins/CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin", 0x00000),
    ("bins/CV6T-14C218-AX/CV6T-14C218-AX_blk0_0x00009800.bin", 0x04C00),
    ("bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin", 0x0E000),
]

# Opcodes that take an absolute X address in the FOLLOWING word.
READ_OPS = {0xF07C, 0xF17C, 0xF27C, 0xF37C, 0xF57C, 0xF77C, 0xFF7C, 0x4C44, 0x4CC4}
WRITE_OPS = {0xD07C, 0xD17C, 0xD27C, 0xD37C, 0xD57C, 0xDF7C, 0x4EC4, 0x4E44}

DISPATCH1_TABLE = 0x2AFA4   # stride 2, 6 entries, rate limit
DISPATCH2_TABLE = 0x2B025   # stride 2, 6 entries, authority increment

# Expected arm targets (low 16 bits as stored in the table).
EXPECTED_D1 = (0xAFE7, 0xAFB0, 0xAFCF, 0xAFD4, 0xAFD8, 0xAFE2)
EXPECTED_D2 = (0xB04F, 0xB031, 0xB04E, 0xB04F, 0xB031, 0xB04E)

DYNAMIC_CODES = (1, 4)
FLAT_CODES = (2, 5)

# Dispatcher 1 arms are distinct addresses, so the partition is semantic, not
# by shared target: the dynamic arms invoke the 16-step divider P:$0011F,
# the flat arms just move a constant into X:$2D46.
DIVIDER_CALL = (0xE254, 0x011F)
D1_ARM_BOUNDS = {
    0x2AFE7: 0x2AFEA,   # code 0
    0x2AFB0: 0x2AFCF,   # code 1
    0x2AFCF: 0x2AFD4,   # code 2
    0x2AFD4: 0x2AFD8,   # code 3
    0x2AFD8: 0x2AFE2,   # code 4
    0x2AFE2: 0x2AFE7,   # code 5
}

PARK_REGION = (0x1E000, 0x1E600)
LANE_CELLS = range(0x2D00, 0x2E00)


class Image:
    def __init__(self, blocks=BLOCKS):
        self.spans = []
        for name, base in blocks:
            data = (ROOT / name).read_bytes()
            words = list(struct.unpack("<%dH" % (len(data) // 2), data))
            self.spans.append((base, words))

    def word(self, addr):
        for base, words in self.spans:
            if base <= addr < base + len(words):
                return words[addr - base]
        return None

    def refs(self):
        """Return (reads, writes) Counters keyed by absolute X address."""
        reads, writes = Counter(), Counter()
        for _base, words in self.spans:
            for i in range(len(words) - 1):
                op, operand = words[i], words[i + 1]
                if op in READ_OPS:
                    reads[operand] += 1
                elif op in WRITE_OPS or (op & 0xFF80) == 0xE680:
                    writes[operand] += 1
        return reads, writes

    def sites(self, lo, hi):
        """Yield (addr, op, operand) for absolute-addressed ops in [lo, hi]."""
        for base, words in self.spans:
            for i in range(len(words) - 1):
                addr = base + i
                if lo <= addr <= hi and (words[i] in READ_OPS or words[i] in WRITE_OPS):
                    yield addr, words[i], words[i + 1]


def table(img, base, n=6):
    return tuple(img.word(base + 2 * k) for k in range(n))


def check(results, name, ok, detail=""):
    results.append((name, bool(ok), detail))


def _reads_runtime(img, start, end):
    """True if the arm reads a lane runtime cell (X:$2Dxx) as an operand."""
    return any(
        img.word(p) == 0xF07C and 0x2D00 <= (img.word(p + 1) or 0) < 0x2E00
        for p in range(start, end)
    )


# --- cross-build: BV6T-AF (working LCA) and CV6T-AH (same-family control) ----

BV_BLOCKS = [
    ("bins/BV6T-14C217-AF/BV6T-14C217-AF_blk0_0x00000000.bin", 0x00000),
    ("bins/BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin", 0x0E000),
]
AH_BLOCKS = [
    ("bins/CV6T-14C217-AH/CV6T-14C217-AH_blk0_0x00000000.bin", 0x00000),
    ("bins/CV6T-14C217-AH/CV6T-14C217-AH_blk1_0x0001C000.bin", 0x0E000),
]

# Dispatcher-1 code4/5 shared rate-limit source cell, per build.
SOURCE_CELL = {"BV6T-AF": 0x23A7, "CV6T-AR": 0x2D50, "CV6T-AH": 0x2891}

DIRECT_WRITE = {0xD07C, 0xD17C, 0xD27C, 0xD37C, 0xD57C, 0xDF7C}


def source_writers(img, cell):
    """Return (runtime_writers, literal_writers) for a cell.

    Runtime: computed store (Dx7C abs) or register-to-cell copy (F67C src,dst).
    Literal: 8654 dst,imm16 or E680|imm7 dst.
    """
    runtime, literal = [], []
    for base, words in img.spans:
        for i in range(len(words) - 2):
            addr, op = base + i, words[i]
            if op in DIRECT_WRITE and words[i + 1] == cell:
                runtime.append(addr)
            elif op == 0xF67C and words[i + 2] == cell:
                runtime.append(addr)
            elif op == 0x8654 and words[i + 1] == cell:
                literal.append((addr, words[i + 2]))
            elif (op & 0xFF80) == 0xE680 and words[i + 1] == cell:
                literal.append((addr, op & 0x7F))
    return runtime, literal


def run_cross_build_checks():
    """The A/B claim only counts if a same-family control agrees with one side."""
    r = []
    imgs = {
        "CV6T-AR": Image(),
        "BV6T-AF": Image(BV_BLOCKS),
        "CV6T-AH": Image(AH_BLOCKS),
    }
    verdict = {}
    for name, img in imgs.items():
        cell = SOURCE_CELL[name]
        runtime, literal = source_writers(img, cell)
        # A cell whose only writers are literals is constant at runtime.
        verdict[name] = "DYNAMIC" if runtime else ("CONSTANT" if literal else "NONE")
        check(r, f"{name}: X:${cell:04X} classified",
              verdict[name] in ("DYNAMIC", "CONSTANT"),
              f"{verdict[name]} runtime={len(runtime)} literal={len(literal)}")

    check(r, "BV6T (working LCA) rate source is DYNAMIC",
          verdict["BV6T-AF"] == "DYNAMIC")
    check(r, "CV6T-AR (our build) rate source is CONSTANT",
          verdict["CV6T-AR"] == "CONSTANT")
    check(r, "CONTROL: CV6T-AH agrees with CV6T-AR, not BV6T",
          verdict["CV6T-AH"] == verdict["CV6T-AR"] != verdict["BV6T-AF"],
          "difference is cross-family, not recompilation noise")

    # BV6T calls a producer immediately before dispatcher 1; CV6T does not.
    bv = imgs["BV6T-AF"]
    check(r, "BV6T wrapper calls producer P:$27346 then dispatcher P:$273BE",
          (bv.word(0x27460), bv.word(0x27461)) == (0xE256, 0x7346)
          and (bv.word(0x27462), bv.word(0x27463)) == (0xE256, 0x73BE))
    cv = imgs["CV6T-AR"]
    check(r, "CV6T wrapper calls dispatcher P:$2AF90 with no producer",
          (cv.word(0x2B058), cv.word(0x2B059)) == (0xE256, 0xAF90)
          and cv.word(0x2B056) == 0xF27C)

    # Dispatcher 2 is reversed between the families.
    check(r, "BV6T dispatcher-2 code1/4 arm is a constant load",
          bv.word(0x2744A) == 0xF17C and bv.word(0x2744B) == 0x05FA)
    check(r, "CV6T dispatcher-2 code1/4 arm is scheduled",
          cv.word(0x2B031) == 0xF07C and cv.word(0x2B032) == 0x08F4
          and cv.word(0x2B034) == 0xF07C and cv.word(0x2B035) == 0x2DA0)
    return r


def run_checks(img):
    r = []
    reads, writes = img.refs()

    d1, d2 = table(img, DISPATCH1_TABLE), table(img, DISPATCH2_TABLE)
    check(r, "dispatcher-1 table matches", d1 == EXPECTED_D1,
          " ".join("%04X" % x for x in d1))
    check(r, "dispatcher-2 table matches", d2 == EXPECTED_D2,
          " ".join("%04X" % x for x in d2))

    # The structural core. Dispatcher 2 partitions by SHARED ARM; dispatcher 1
    # has distinct arms per code, so it is tested by the semantic property
    # instead. Asserting shared arms for d1 would be false.
    dyn2 = {d2[c] for c in DYNAMIC_CODES}
    flat2 = {d2[c] for c in FLAT_CODES}
    check(r, "d2: codes (1,4) share one arm", len(dyn2) == 1)
    check(r, "d2: codes (2,5) share one arm", len(flat2) == 1)
    check(r, "d2: dynamic and flat arms differ", dyn2 != flat2)

    # d1: dynamic arms call the divider, flat arms do not.
    def arm_calls_divider(start):
        end = D1_ARM_BOUNDS[start]
        w = [img.word(p) for p in range(start, end)]
        return any((w[i], w[i + 1]) == DIVIDER_CALL for i in range(len(w) - 1))

    d1_div = {c: arm_calls_divider(d1[c] | 0x20000) for c in range(6)}
    check(r, "d1: codes (1,4) are the scheduled/divider arms",
          all(d1_div[c] for c in DYNAMIC_CODES),
          f"divider per code: {d1_div}")
    check(r, "d1: codes (2,5) are flat (no divider)",
          not any(d1_div[c] for c in FLAT_CODES))
    check(r, "d1: code 1 reads a runtime cell, code 5 does not",
          _reads_runtime(img, 0x2AFB0, 0x2AFCF) and not _reads_runtime(img, 0x2AFE2, 0x2AFE7))

    # Live sustained LKA is code 1, live sustained LCA is code 5 (joint3..joint8).
    for tbl, label in ((d1, "d1"), (d2, "d2")):
        check(r, f"{label}: live LKA code 1 != live LCA code 5 arm",
              tbl[1] != tbl[5], "%04X vs %04X" % (tbl[1], tbl[5]))

    # Calibration cells must be read-only, or they are state and the story changes.
    for cell in (0x08F4, 0x08F5, 0x08F6, 0x03AF, 0x03B0):
        check(r, f"X:${cell:04X} is read-only calibration",
              reads[cell] > 0 and writes[cell] == 0,
              f"reads={reads[cell]} writes={writes[cell]}")

    # Single convergent store of the authority increment.
    check(r, "P:$2B051 stores B1 -> X:$2D4A",
          img.word(0x2B051) == 0xD17C and img.word(0x2B052) == 0x2D4A)
    check(r, "X:$2D4A feeds integrator add at P:$2B08C",
          img.word(0x2B08A) == 0xF07C and img.word(0x2B08B) == 0x2D4A
          and img.word(0x2B08C) == 0x4444 and img.word(0x2B08D) == 0x2D4B)
    check(r, "accumulator store P:$2B0B2 -> X:$2D53",
          img.word(0x2B0B2) == 0xD07C and img.word(0x2B0B3) == 0x2D53)

    # Preamble selecting between the two flat constants.
    check(r, "preamble tests X:$2DDC",
          img.word(0x2B00B) == 0xFF7C and img.word(0x2B00C) == 0x2DDC)
    check(r, "preamble constants are X:$08F6 / X:$08F5",
          img.word(0x2B00F) == 0x08F6 and img.word(0x2B012) == 0x08F5)

    # Park is an independent channel: no overlap with lane cells.
    overlap = [(a, o) for a, _op, o in img.sites(*PARK_REGION) if o in LANE_CELLS]
    check(r, "park region touches no lane torque cell", not overlap,
          f"{len(overlap)} overlapping sites")

    park_cal = sorted({o for _a, _op, o in img.sites(*PARK_REGION) if o < 0x1000})
    check(r, "park region has its own calibration bank", len(park_cal) >= 8,
          " ".join("$%04X" % c for c in park_cal))

    # Guard against the retracted labelling: in dispatcher 2 code 2 and code 5
    # share an arm, and in dispatcher 1 both are flat. So a code-2-vs-code-5
    # comparison cannot reveal this asymmetry -- which is exactly why the
    # earlier static analysis concluded "identical".
    check(r, "code 2 vs code 5 comparison is structurally blind",
          d2[2] == d2[5] and not d1_div[2] and not d1_div[5],
          "both flat in d1, same arm in d2")
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.parse_args()

    img = Image()
    results = run_checks(img)
    print("-- CV6T-AR structure --")
    failed = 0
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
        failed += not ok

    cross = run_cross_build_checks()
    print("\n-- cross-build vs BV6T-AF, control CV6T-AH --")
    for name, ok, detail in cross:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
        failed += not ok

    total = len(results) + len(cross)
    print(f"\n{total - failed}/{total} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
