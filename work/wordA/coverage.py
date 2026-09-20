#!/usr/bin/env python3
"""Measure HONEST coverage of the disassembler across every PSCM image.

Reports, per image:
  * words that match at least one encoding vs `.word` (unknown)
  * words matching MORE than one encoding (ambiguous - a real limitation)
  * the most frequent unknown opcodes, so the next encoding to add is obvious

Coverage here is a *decode-rate*, not a correctness proof: a matched word can
still be a data word or an operand of a preceding instruction. Treat it as a
progress metric, not a guarantee.
"""
import glob
import json
import os
import struct
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "/home/gl/Projects/ford/PSCM/Research/bins"

tbl = json.load(open(os.path.join(HERE, "encodings.json")))
for e in tbl:
    e["nb"] = bin(e["mask"]).count("1")
tbl.sort(key=lambda e: -e["nb"])

# bucket encodings by the high byte to keep the scan fast
by_hi = {}
for e in tbl:
    for hi in range(256):
        if ((hi << 8) & (e["mask"] >> 8 << 8)) == (e["value"] & (e["mask"] >> 8 << 8)):
            by_hi.setdefault(hi, []).append(e)


def matches(word):
    return [e for e in by_hi.get(word >> 8, ()) if (word & e["mask"]) == e["value"]]


print(f"table: {len(tbl)} encodings, {len({e['mnem'] for e in tbl})} mnemonics\n")
print(f"{'image':<40} {'words':>8} {'decoded':>8} {'rate':>7} {'ambig':>7}")
print("-" * 76)

tot = dec = amb = 0
unknown = Counter()
for p in sorted(glob.glob(f"{BASE}/*/*.bin")):
    d = open(p, "rb").read()
    nw = len(d) // 2
    w = struct.unpack("<%dH" % nw, d[:nw * 2])
    n_dec = n_amb = 0
    for x in w:
        m = matches(x)
        if m:
            n_dec += 1
            if len(m) > 1:
                n_amb += 1
        else:
            unknown[x] += 1
    tag = p.split("/")[-2] + "/" + p.split("/")[-1].split("_")[1]
    print(f"{tag:<40} {nw:>8} {n_dec:>8} {100*n_dec/nw:>6.1f}% {n_amb:>7}")
    tot += nw
    dec += n_dec
    amb += n_amb

print("-" * 76)
print(f"{'TOTAL':<40} {tot:>8} {dec:>8} {100*dec/tot:>6.1f}% {amb:>7}"
      f"  ({100*amb/tot:.1f}% ambiguous)")
print(f"\ndistinct unknown opcode words: {len(unknown)}")
print("most frequent unknown words (next encodings to add):")
for word, n in unknown.most_common(15):
    print(f"   {word:04X}  x{n}")
