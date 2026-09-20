#!/usr/bin/env python3
"""Dump the region-descriptor context around the word-address-of-word-A hits.

Hit offsets (u32 LE = word addr 0x3FFF5) inside blk1:
  AF: 0x81B6 0x81E0 0x82A2   AH: 0x94E6 0x9510 0x95D2   AR: 0x9E5C 0x9E86 0x9F48
Identical relative spacing +0x2A / +0xC2 across all three versions.
DSP56800E word addr -> byte addr = 2 * word.
"""
import glob
import sys

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
HITS = {"BV6T-14C217-AF": [0x81B6, 0x81E0, 0x82A2],
        "CV6T-14C217-AH": [0x94E6, 0x9510, 0x95D2],
        "CV6T-14C217-AR": [0x9E5C, 0x9E86, 0x9F48]}
b1 = {v: open(sorted(glob.glob(f"{BASE}/{v}/*.bin"))[1], "rb").read() for v in V}


def u32l(d, o):
    return int.from_bytes(d[o:o + 4], "little")


def u16l(d, o):
    return int.from_bytes(d[o:o + 2], "little")


def annot(w):
    """Interpret a 32-bit value as a possible word address -> byte address."""
    ba = w * 2
    tags = []
    if 0 <= ba <= 0x80000:
        tags.append(f"byte 0x{ba:X}")
        for nm, v in (("=A", 0x7FFEA), ("=B", 0x7FFEC), ("=END", 0x80000),
                      ("=CAL", 0x9800), ("=APP", 0x1C000), ("=TS", 0x7FFE4)):
            if ba == v:
                tags.append(nm)
    if 0x2000000 <= w <= 0x2010000:
        tags.append(f"upper byte 0x{w*2:X}")
    return " ".join(tags)


for v in V:
    d = b1[v]
    print("=" * 78)
    print(v)
    lo = min(HITS[v]) - 0x40
    hi = max(HITS[v]) + 0x40
    print(f"-- raw window blk1[0x{lo:X}..0x{hi:X}) as u32 LE --")
    for o in range(lo, hi, 4):
        w = u32l(d, o)
        mark = " <<< WORD-A-ADDR" if o in HITS[v] else ""
        a = annot(w)
        print(f"  0x{o:05X}: {w:08X}  {a:28s}{mark}")
