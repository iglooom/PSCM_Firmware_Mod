#!/usr/bin/env python3
"""Locate the word-A checksum LOOP in blk1 and disassemble it from a real
instruction boundary.

Two robust boundary sources (an arbitrary start can begin mid-instruction and
yield convincing garbage):
  1. the nearest preceding RTS/RTI  -> the next word starts a function
  2. JSR <ABS19> targets            -> known-good entries

A flash checksum must READ PROGRAM MEMORY, so the loop body has to contain a
P-space read. Those encodings are (verified, erm 26953/27443):
    MOVE.W  P:(Rn)+ ,GGG   1000 0GGG 0110 1mRR   -> 0x8068 mask 0xF8F8
    MOVEU.W P:(Rn)+ ,SSS   1000 1SSS 0110 1mRR   -> 0x8868 mask 0xF8F8
We locate every P-space read in blk1 and report those nearest the routine that
loads the region triple (blk1+0x81B4), which is the checksum loop.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dis56800e as D

BASE_DIR = "/home/gl/Projects/ford/PSCM/Research/bins"
VER = sys.argv[1] if len(sys.argv) > 1 else "BV6T-14C217-AF"
BLK1 = f"{BASE_DIR}/{VER}/{VER}_blk1_0x0001C000.bin"
WORD_BASE = 0xE000

d = open(BLK1, "rb").read()
nw = len(d) // 2
w = list(struct.unpack("<%dH" % nw, d[:nw * 2]))
table = D.load_table()

# ---- 1. every P-space read in blk1 ---------------------------------------
pread = [i for i in range(nw)
         if (w[i] & 0xF8F8) == 0x8068 or (w[i] & 0xF8F8) == 0x8868]
print(f"{VER}: {nw} words; P-space reads found: {len(pread)}")
TRIPLE = 0x81B4 // 2          # word index of the region-triple load
near = sorted(pread, key=lambda i: abs(i - TRIPLE))[:12]
print(f"P-space reads nearest the region triple (blk1 word 0x{TRIPLE:X}"
      f" = byte 0x81B4):")
for i in near:
    print(f"   blk1 word 0x{i:05X}  byte 0x{i*2:05X}  P:${WORD_BASE+i:05X}"
          f"  delta {i - TRIPLE:+d} words   raw {w[i]:04X}")

# ---- 2. RTS/RTI positions, for function boundaries -----------------------
rets = [i for i in range(nw) if w[i] in (0xE708, 0xE710)]


def func_start(idx):
    """First word after the nearest preceding RTS/RTI."""
    prev = [r for r in rets if r < idx]
    return (prev[-1] + 1) if prev else 0


# ---- 3. disassemble the function containing each interesting site --------
def show(idx, count=46, tag=""):
    s = func_start(idx)
    print(f"\n=== function containing blk1 word 0x{idx:05X} (byte 0x{idx*2:05X})"
          f" {tag}")
    print(f"    starts after RTS at word 0x{s:05X} (byte 0x{s*2:05X}),"
          f" {idx - s} words before the site")
    i = s
    n = 0
    while i < nw and n < count:
        txt, ln, cands = D.disasm(w, i, table)
        raw = " ".join(f"{w[i+k]:04X}" for k in range(min(ln, nw - i)))
        mark = "   <<<<" if i == idx else ""
        amb = "" if len(cands) <= 1 else f"  ;{len(cands)}enc"
        print(f"  P:${WORD_BASE+i:05X} b0x{i*2:05X}  {raw:<22} {txt}{amb}{mark}")
        if txt.startswith(("RTS", "RTI")) and i > s:
            print("    --- RTS ---")
            break
        i += max(1, ln)
        n += 1


if near:
    show(near[0], 46, "= nearest P-space read == candidate checksum loop")
show(TRIPLE, 40, "= the region-triple load")
