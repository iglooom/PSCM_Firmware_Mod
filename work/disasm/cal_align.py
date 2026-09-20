#!/usr/bin/env python3
"""Align the two 14C218 calibrations by anchor curves, then diff the content.

14C218 has NO global alignment (best uniform shift scores 5.4% = noise), but
it is not randomly rearranged either: identical data tables recur at
piecewise-constant offsets (+0xA0, +0x90, ...).  So:

  1. find ANCHORS -- monotonic curves whose exact value sequence occurs in
     both builds and is UNIQUE within each (so the pairing is unambiguous);
  2. derive the local shift at each anchor;
  3. group anchors into aligned REGIONS of constant shift;
  4. inside each aligned region, compare word-for-word and report the real
     parameter differences.

Words outside any aligned region are reported as UNALIGNED and explicitly
NOT interpreted -- their differences cannot be distinguished from relocation.
"""
import os
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
BASE = 0x4C00

from cal_structure import curves, load, BUILDS  # noqa: E402


def anchors(a, b):
    """Unique-in-both exact curve matches -> [(addr_a, addr_b, shift, len)]."""
    ca, cb = curves(a), curves(b)
    va, vb = defaultdict(list), defaultdict(list)
    for x in ca:
        va[x["vals"]].append(x)
    for x in cb:
        vb[x["vals"]].append(x)
    out = []
    for v in set(va) & set(vb):
        if len(va[v]) == 1 and len(vb[v]) == 1:      # unambiguous pairing
            xa, xb = va[v][0], vb[v][0]
            out.append((xa["at"], xb["at"], xb["at"] - xa["at"], xa["len"]))
    return sorted(out)


def regions(anc, a, b):
    """Grow each anchor's constant-shift agreement outward word by word."""
    regs = []
    for pa, pb, sh, ln in anc:
        ia, ib = pa - BASE, pb - BASE
        lo = 0
        while (ia - lo - 1 >= 0 and ib - lo - 1 >= 0
               and a[ia - lo - 1] == b[ib - lo - 1]):
            lo += 1
        hi = ln
        while (ia + hi < len(a) and ib + hi < len(b)
               and a[ia + hi] == b[ib + hi]):
            hi += 1
        regs.append((pa - lo, pb - lo, sh, lo + hi))
    # merge overlapping regions with the same shift
    regs.sort()
    merged = []
    for r in regs:
        if merged and merged[-1][2] == r[2] and \
                r[0] <= merged[-1][0] + merged[-1][3]:
            s = merged[-1]
            end = max(s[0] + s[3], r[0] + r[3])
            merged[-1] = (s[0], s[1], s[2], end - s[0])
        else:
            merged.append(list(r) if isinstance(r, tuple) else r)
            merged[-1] = tuple(merged[-1])
    return merged


def main():
    (la, ra), (lb, rb) = list(BUILDS.items())
    a, b = load(ra), load(rb)
    anc = anchors(a, b)
    print(f"anchors (curves unique in both builds): {len(anc)}")
    sh = Counter(x[2] for x in anc)
    print("local shifts:", ", ".join(f"{s:+d}x{n}" for s, n in sh.most_common(8)))

    regs = regions(anc, a, b)
    cov = sum(r[3] for r in regs)
    print(f"\naligned regions: {len(regs)}, covering {cov} words "
          f"({100*cov/len(a):.1f}% of the calibration)")
    for pa, pb, s, ln in sorted(regs, key=lambda r: -r[3])[:12]:
        print(f"   BV6T P:${pa:05X} <-> CV6T P:${pb:05X}  shift {s:+6d}  "
              f"{ln:>5} words identical")

    print(f"\n=> {100-100*cov/len(a):.1f}% of the block is UNALIGNED: those "
          f"words cannot be\n   compared, because relocation and retuning are "
          f"indistinguishable there.")
    print("   No parameter claim is made about the unaligned remainder.")


if __name__ == "__main__":
    main()
