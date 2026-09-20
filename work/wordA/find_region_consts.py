#!/usr/bin/env python3
"""Locate region-descriptor / address constants for the self-check routine.

DSP56800E is WORD addressed: VBF byte addr = 2 * word addr.
  byte 0x00000000 -> word 0x00000      byte 0x0007FFEA -> word 0x3FFF5  (word A)
  byte 0x00009800 -> word 0x04C00      byte 0x0007FFEC -> word 0x3FFF6  (word B)
  byte 0x0001C000 -> word 0x0E000      byte 0x00080000 -> word 0x40000  (end)
  byte 0x04008C00 -> word 0x2004600
Scan every image for these values as 16-bit halves and 32-bit words, LE and BE.
"""
import glob
import struct
from collections import defaultdict

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
IMGS = {}
for p in sorted(glob.glob(f"{BASE}/*/*.bin")):
    tag = p.split("/")[-2] + "/" + p.split("/")[-1].split("_")[1]
    IMGS[tag] = open(p, "rb").read()

BYTE_ADDRS = {
    "A@0x7FFEA": 0x0007FFEA, "B@0x7FFEC": 0x0007FFEC, "end0x80000": 0x00080000,
    "cal0x9800": 0x00009800, "app0x1C000": 0x0001C000, "blk2@0x4008C00": 0x04008C00,
    "0x7FFE4_ts": 0x0007FFE4, "0x7FFEE": 0x0007FFEE,
}
CANDS = defaultdict(list)
for name, ba in BYTE_ADDRS.items():
    CANDS[name].append(("byte", ba))
    if ba % 2 == 0:
        CANDS[name].append(("word", ba // 2))

# also the lengths
for name, ln in {"len0x64000": 0x64000, "len0x63FEA": 0x63FEA, "len0x12800": 0x12800,
                 "len0x9800": 0x9800, "cnt0x31FF5": 0x31FF5}.items():
    CANDS[name].append(("byte", ln))
    CANDS[name].append(("word", ln // 2))


def find_u32(d, val):
    out = []
    for end in ("little", "big"):
        pat = val.to_bytes(4, end)
        i = d.find(pat)
        while i != -1:
            out.append((i, "u32" + end[0]))
            i = d.find(pat, i + 1)
    return out


def find_u16(d, val):
    if val > 0xFFFF:
        return []
    out = []
    for end in ("little", "big"):
        pat = val.to_bytes(2, end)
        i = d.find(pat)
        while i != -1:
            out.append((i, "u16" + end[0]))
            i = d.find(pat, i + 1)
    return out


print("=" * 78)
for name, variants in CANDS.items():
    for kind, val in variants:
        for tag, d in IMGS.items():
            hits = find_u32(d, val)
            # u16 only for values that fit and are distinctive (skip tiny/common)
            if val <= 0xFFFF and val not in (0x0000, 0xFFFF):
                hits += find_u16(d, val)
            if not hits:
                continue
            if len(hits) > 12:
                print(f"{name:14s} {kind:4s} 0x{val:X}  {tag:34s} {len(hits)} hits (too many)")
                continue
            locs = " ".join(f"0x{o:X}/{k}" for o, k in hits)
            print(f"{name:14s} {kind:4s} 0x{val:X}  {tag:34s} {locs}")
