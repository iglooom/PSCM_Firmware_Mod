#!/usr/bin/env python3
"""Structural comparison of BV6T vs CV6T 14C217 via pointer-constant profiles.

Raw word diffing is useless here (two builds of the SAME family differ by
74.5%), so this compares the SHAPE of the code instead: how many times each
data-structure base address is loaded into an AGU pointer register.

That profile is compile-independent -- relocation changes the addresses but
not the number of places each structure is touched.  If the two builds are
the same program with data moved, the sorted use-count profiles will match
rank-for-rank; a genuinely different feature set would show extra or missing
structures.

Reported: the paired profile, and any count that exists in one build with no
partner in the other (those are where a real behavioural difference must live).
"""
import collections
import os
import struct
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
AGU_LOADS = {0x8748, 0x8749, 0x874A, 0x874B, 0x874C, 0x874D}

BUILDS = {
    "BV6T": [("BV6T-14C217-AF/BV6T-14C217-AF_blk0_0x00000000.bin", 0x00000),
             ("BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin", 0x0E000)],
    "CV6T": [("CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin", 0x00000),
             ("CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin", 0x0E000)],
}


def load(rel):
    d = open(os.path.join(ROOT, "bins", rel), "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def profile(spec, lo=0x3E00, hi=0x8800):
    c = collections.Counter()
    for rel, _b in spec:
        w = load(rel)
        for i in range(len(w) - 1):
            if w[i] in AGU_LOADS and lo <= w[i + 1] < hi:
                c[w[i + 1]] += 1
    return c


def main():
    prof = {k: profile(v) for k, v in BUILDS.items()}
    for k, c in prof.items():
        print(f"{k}: {len(c)} distinct base addresses, "
              f"{sum(c.values())} pointer loads")

    a = sorted(prof["BV6T"].values(), reverse=True)
    b = sorted(prof["CV6T"].values(), reverse=True)
    n = min(len(a), len(b))
    same = sum(1 for i in range(n) if a[i] == b[i])
    print(f"\nsorted use-count profiles agree on {same}/{n} ranks "
          f"({100*same/n:.0f}%)")

    print("\nrank  BV6T addr/count      CV6T addr/count      match")
    ra = prof["BV6T"].most_common()
    rb = prof["CV6T"].most_common()
    for i in range(20):
        if i >= len(ra) or i >= len(rb):
            break
        (aa, ac), (ba, bc) = ra[i], rb[i]
        print(f" {i:>3}  X:${aa:04X} x{ac:<4}        X:${ba:04X} x{bc:<4}"
              f"        {'=' if ac == bc else '<-- DIFFERS'}")

    # multiset difference of counts: what has no partner at all?
    ca, cb = collections.Counter(a), collections.Counter(b)
    only_a, only_b = ca - cb, cb - ca
    print(f"\ncounts present only in BV6T: "
          f"{sorted(only_a.elements(), reverse=True)[:15]}")
    print(f"counts present only in CV6T: "
          f"{sorted(only_b.elements(), reverse=True)[:15]}")


if __name__ == "__main__":
    main()
