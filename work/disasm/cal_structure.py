#!/usr/bin/env python3
"""Structural comparison of the 14C218 PSCM calibration between builds.

14C218 loads at VBF byte 0x00009800 -> P-space word 0x4C00, filling the gap
between blk0 code (0x0000-0x4BFF) and blk1 code (0xE000+).  It is 37888 words
of dense parameter data with NO global alignment between builds (best shift
scores 5.4%, i.e. noise), so byte/word diffing is meaningless -- 96.3% of
words differ purely from relocation.

Layout-independent comparison instead: extract every monotonic run (the
signature of an axis / breakpoint table / transfer curve) and compare the
resulting CURVE MULTISET.  A curve is described by its length, direction and
value range, none of which depend on where it sits.

This can show:
  * curves present in one build and absent in the other  -> possible feature
  * curves present in both but with different ranges     -> retuned limits
"""
import os
import struct
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE = 0x4C00

BUILDS = {
    "BV6T-14C218-AF": "BV6T-14C218-AF/BV6T-14C218-AF_blk0_0x00009800.bin",
    "CV6T-14C218-AX": "CV6T-14C218-AX/CV6T-14C218-AX_blk0_0x00009800.bin",
}

MIN_RUN = 6          # shorter runs occur by chance far too often


def load(rel):
    d = open(os.path.join(ROOT, "bins", rel), "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def curves(w, minrun=MIN_RUN):
    """Maximal strictly-monotonic runs of >= minrun words."""
    out = []
    i = 0
    n = len(w)
    while i < n - 1:
        if w[i + 1] > w[i]:
            d = 1
        elif w[i + 1] < w[i]:
            d = -1
        else:
            i += 1
            continue
        j = i + 1
        while j < n - 1 and ((w[j + 1] > w[j]) if d > 0 else (w[j + 1] < w[j])):
            j += 1
        ln = j - i + 1
        if ln >= minrun:
            out.append({"at": BASE + i, "len": ln, "dir": d,
                        "lo": min(w[i], w[j]), "hi": max(w[i], w[j]),
                        "vals": tuple(w[i:j + 1])})
        i = j
    return out


def main():
    cur = {}
    for label, rel in BUILDS.items():
        w = load(rel)
        c = curves(w)
        cur[label] = c
        span = sum(x["len"] for x in c)
        print(f"{label}: {len(c)} monotonic curves (>= {MIN_RUN} words), "
              f"{span} words = {100*span/len(w):.1f}% of the block")

    a, b = list(BUILDS)
    ca, cb = cur[a], cur[b]

    # 1. identical curves (same exact value sequence) regardless of address
    va = Counter(x["vals"] for x in ca)
    vb = Counter(x["vals"] for x in cb)
    shared = va & vb
    print(f"\nexact-sequence curves shared: {sum(shared.values())}")
    print(f"  only in {a}: {sum((va - vb).values())}")
    print(f"  only in {b}: {sum((vb - va).values())}")

    # 2. profile by (length, direction) -- robust to rescaling
    pa = Counter((x["len"], x["dir"]) for x in ca)
    pb = Counter((x["len"], x["dir"]) for x in cb)
    keys = sorted(set(pa) | set(pb))
    print(f"\n{'len':>5} {'dir':>4} {a:>16} {b:>16}   delta")
    for k in keys:
        if abs(pa[k] - pb[k]) >= 2 or k[0] >= 16:
            print(f"{k[0]:>5} {k[1]:>4} {pa[k]:>16} {pb[k]:>16}"
                  f"   {pb[k]-pa[k]:+d}")

    # 3. the longest curves, which are the real transfer maps
    print(f"\nlongest curves in each build:")
    for label, c in ((a, ca), (b, cb)):
        top = sorted(c, key=lambda x: -x["len"])[:8]
        print(f"  {label}:")
        for x in top:
            print(f"    P:${x['at']:05X} len {x['len']:>4} "
                  f"{'up ' if x['dir']>0 else 'down'} "
                  f"[{x['lo']:#06x}..{x['hi']:#06x}]")


if __name__ == "__main__":
    main()
