#!/usr/bin/env python3
"""Find the function entry that CONTAINS a given blk1 offset, then disassemble
from that entry so instruction boundaries are real.

Linear disassembly started at an arbitrary offset can begin mid-instruction and
produce convincing garbage (multi-word immediates get read as opcodes). Function
entries recovered from the validated `JSR <ABS19>` encoding are known-good
boundaries, so decoding from the nearest preceding entry is trustworthy.

blk1 loads at byte 0x1C000 = word 0xE000.
"""
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dis56800e as D

BLK1 = ("/home/gl/Projects/ford/PSCM/Research/bins/BV6T-14C217-AF/"
        "BV6T-14C217-AF_blk1_0x0001C000.bin")
WORD_BASE = 0xE000
TARGET_BYTE = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x81B4
NCOUNT = int(sys.argv[2], 0) if len(sys.argv) > 2 else 90

d = open(BLK1, "rb").read()
nw = len(d) // 2
w = list(struct.unpack("<%dH" % nw, d[:nw * 2]))

# --- collect JSR/JMP targets that land inside blk1 -> function entries -----
lo, hi = WORD_BASE, WORD_BASE + nw
entries = set()
callers = {}
for i in range(nw - 1):
    o = w[i]
    if (o & 0xFFF5) == 0xE254 or (o & 0xFFF5) == 0xE250:   # JSR / JMP ABS19
        t = ((o >> 3 & 1) << 18) | ((o >> 1 & 1) << 17) | ((o & 1) << 16) | w[i + 1]
        if lo <= t < hi:
            entries.add(t)
            callers.setdefault(t, []).append(WORD_BASE + i)

tw = WORD_BASE + TARGET_BYTE // 2
print(f"blk1: {nw} words, {len(entries)} distinct in-range call targets")
print(f"target byte 0x{TARGET_BYTE:X} = word P:${tw:05X}")

prev = sorted(e for e in entries if e <= tw)
if not prev:
    print("no preceding entry found; falling back to target itself")
    start = tw
else:
    start = prev[-1]
    print(f"nearest preceding function entry: P:${start:05X} "
          f"(byte 0x{(start - WORD_BASE) * 2:X}), "
          f"{(tw - start)} words before target, "
          f"called from {len(callers.get(start, []))} site(s): "
          + ", ".join(f"P:${c:05X}" for c in callers.get(start, [])[:6]))
print()

table = D.load_table()
i = start - WORD_BASE
end_i = min(i + NCOUNT * 3, nw)
n = 0
print(f"{'word':>9} {'byte':>9}  {'raw':<22} instruction")
while i < nw and n < NCOUNT:
    txt, ln, cands = D.disasm(w, i, table)
    aw = WORD_BASE + i
    bo = i * 2
    raw = " ".join(f"{w[i+k]:04X}" for k in range(min(ln, nw - i)))
    mark = "  <<<< TARGET" if aw == tw else ""
    amb = "" if len(cands) <= 1 else f"  ;{len(cands)}enc"
    lbl = ""
    if aw in entries:
        lbl = f"\nsub_{aw:05X}:"
    if lbl:
        print(lbl)
    print(f" P:${aw:05X} b0x{bo:05X}  {raw:<22} {txt}{amb}{mark}")
    if txt.startswith(("RTS", "RTI")):
        print(f"  --- end of function at P:${aw:05X} ---")
        break
    i += max(1, ln)
    n += 1
