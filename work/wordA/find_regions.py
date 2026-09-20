#!/usr/bin/env python3
"""Recover region descriptors from DSP56800E 32-bit immediate loads in blk1.

Encoding observed (and self-consistent across all 3 versions):
    word 0xE41n , lo16 , hi16     ->  MOVE.L #(hi16<<16|lo16), Rn
e.g. E410 FFF5 0003 -> #$0003FFF5 ; E411 17F5 0003 -> #$000317F5
     E410 E800 0000 -> #$0000E800

DSP56800E is WORD addressed, so an address immediate W maps to byte 2*W.
  word 0x3FFF5 -> byte 0x7FFEA  (word A itself)
  word 0x317F5 -> byte 0x62FEA
  0x3FFF5 - 0x317F5 = 0xE800 words = 0x1D000 bytes  <- start/length/end triple

blk1 loads at byte 0x1C000 = word 0xE000.
"""
import glob
import sys

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
WORD_BASE = 0x0E000
BLK1_BYTE_BASE = 0x1C000

WIN = {"BV6T-14C217-AF": (0x8100, 0x8400),
       "CV6T-14C217-AH": (0x9430, 0x9730),
       "CV6T-14C217-AR": (0x9DA0, 0xA0A0)}


def imms(d, lo, hi):
    """Yield (byte_off, reg, value) for every E41n long-immediate load."""
    out = []
    o = lo
    while o < hi - 5:
        w = int.from_bytes(d[o:o + 2], "little")
        if (w & 0xFFF0) == 0xE410:
            val = (int.from_bytes(d[o + 4:o + 6], "little") << 16) | \
                  int.from_bytes(d[o + 2:o + 4], "little")
            out.append((o, w & 0xF, val))
            o += 6
            continue
        o += 2
    return out


def tag(val):
    """Interpret as a word address -> byte address, and name known landmarks."""
    ba = val * 2
    names = {0x7FFEA: "WORD_A", 0x7FFEC: "WORD_B", 0x7FFE4: "TIMESTAMP",
             0x80000: "FLASH_END", 0x9800: "CAL_START", 0x1C000: "APP_START",
             0x62FEA: "?", 0x1D000: "?"}
    t = f"byte 0x{ba:06X}"
    if ba in names and names[ba] != "?":
        t += f" = {names[ba]}"
    if BLK1_BYTE_BASE <= ba <= 0x80000:
        t += f"  blk1+0x{ba - BLK1_BYTE_BASE:05X}"
    return t


for v in V:
    d = open(sorted(glob.glob(f"{BASE}/{v}/*.bin"))[1], "rb").read()
    lo, hi = WIN[v]
    print("=" * 78)
    print(f"{v}   blk1[0x{lo:X}..0x{hi:X})")
    got = imms(d, lo, hi)
    for o, reg, val in got:
        print(f"  b0x{o:05X}  MOVE.L #$0{val:07X},R{reg}   {tag(val)}")
    vals = [val for _, _, val in got]
    print("  -- differences between address-like immediates --")
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            dv = vals[j] - vals[i]
            if 0 < abs(dv) < 0x40000 and vals[i] > 0x1000 and vals[j] > 0x1000:
                print(f"     0x{vals[j]:05X} - 0x{vals[i]:05X} = 0x{dv:05X} words"
                      f" = 0x{dv*2:06X} bytes")
