#!/usr/bin/env python3
"""Disassemble the blk1 routine that loads the word-address of word A.

blk1 loads at byte 0x0001C000 -> word base 0x0E000.
blk1 byte offset o  ->  word addr 0xE000 + o/2.
The immediate 0x0003FFF5 (LE u32) = word addr of byte 0x0007FFEA = word A.
"""
import glob
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dsp56800e_dis as D

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = sys.argv[1] if len(sys.argv) > 1 else "BV6T-14C217-AF"
START = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x8140
NW = int(sys.argv[3], 0) if len(sys.argv) > 3 else 160

d = open(sorted(glob.glob(f"{BASE}/{V}/*.bin"))[1], "rb").read()
WORD_BASE = 0x0E000          # blk1 @ byte 0x1C000

words = [int.from_bytes(d[i:i + 2], "little") for i in range(0, len(d) // 2 * 2, 2)]
start_w = START // 2
print(f"# {V} blk1  byte 0x{START:X} = word 0x{WORD_BASE + start_w:05X}")
i = start_w
end = min(start_w + NW, len(words))
while i < end:
    try:
        txt, ln = D.disasm(words, i, WORD_BASE + i)
    except Exception as e:
        txt, ln = f"<err {e}>", 1
    raw = " ".join(f"{words[i+k]:04X}" for k in range(ln))
    bo = i * 2
    print(f"  {WORD_BASE+i:05X}  b0x{bo:05X}  {raw:<20s} {txt}")
    i += max(1, ln)
