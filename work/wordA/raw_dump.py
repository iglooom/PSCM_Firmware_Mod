#!/usr/bin/env python3
"""Raw word dump of a P-space range, with per-word annotation.

The table-driven disassembler can drift on instruction LENGTH (multi-word
immediates), and a drifted stream invents plausible mnemonics. For close
reading of a single routine we want the RAW words plus, for each word, every
encoding that matches it - then resolve boundaries by hand.

Usage: python3 raw_dump.py <word_addr> <nwords> [version]
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
WORD_BASE = 0xE000          # blk1 loads at byte 0x1C000

addr = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x16216
n = int(sys.argv[2], 0) if len(sys.argv) > 2 else 60
VER = sys.argv[3] if len(sys.argv) > 3 else "BV6T-14C217-AF"

d = open(f"{BASE}/{VER}/{VER}_blk1_0x0001C000.bin", "rb").read()
nw = len(d) // 2
w = list(struct.unpack("<%dH" % nw, d[:nw * 2]))

tbl = json.load(open(os.path.join(HERE, "encodings.json")))
for e in tbl:
    e["nb"] = bin(e["mask"]).count("1")
tbl.sort(key=lambda e: -e["nb"])

start = addr - WORD_BASE
print(f"# {VER}  P:${addr:05X}  blk1 byte 0x{start*2:05X}")
for k in range(n):
    i = start + k
    if i >= nw:
        break
    word = w[i]
    cands = [e for e in tbl if (word & e["mask"]) == e["value"]]
    top = cands[:3]
    desc = " | ".join(f"{e['mnem']} {e['operands']}" for e in top)
    print(f"P:${WORD_BASE+i:05X} b0x{i*2:05X} {word:04X}  {desc[:96]}")
