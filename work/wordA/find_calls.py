#!/usr/bin/env python3
"""Find every call to the CRC routines and recover each call's (start,count).

Decisive observation from find_xref.py: at blk1+0x8202 the code does
    MOVE.W X:$11EF,Y1     ; load the RUNNING crc
    ...
    JSR    P:$16216       ; crc routine
    MOVE.W Y0,X:$11EF     ; store the updated crc
so X:$11EF is a CHAINED accumulator: each call folds one more region into it.
That is why no single contiguous range ever matched (§7) - word A is a CRC over
a CONCATENATION of regions.

This script finds:
  * every JSR to the CRC routines (0x16216 P-space, 0x1627C X-space)
  * the MOVE.L #imm32 register loads in the preceding window (the arguments)
  * every read/write of X:$11EF, to see where the chain starts and ends

MOVE.L #imm32,reg encoding observed: E41n <lo16> <hi16>
  n = 0 -> A.L   1 -> B.L   2 -> C.L   8 -> R0   9 -> R1
(E418 -> R0 is confirmed by usage: blk1+0x82A0 loads R0 then does MOVE.W P:(R0)+,A)
"""
import os
import struct
import sys

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
WORD_BASE = 0xE000
CRC_P = 0x16216          # reads P: space
CRC_X = 0x1627C          # reads X: space
ACC = 0x11EF             # the chained accumulator cell

REGN = {0: "A", 1: "B", 2: "C", 3: "D", 8: "R0", 9: "R1", 0xA: "R2", 0xB: "R3"}


def load(ver):
    d = open(f"{BASE}/{ver}/{ver}_blk1_0x0001C000.bin", "rb").read()
    nw = len(d) // 2
    return list(struct.unpack("<%dH" % nw, d[:nw * 2]))


def jsr_target(w, i):
    """JSR/JMP <ABS19>:  1110 0010 0101 A1AA (JSR) / A0AA (JMP) + low16.

    Bits 3,1,0 of the opcode are address bits 18,17,16; bit 2 is the literal
    that distinguishes JSR (1) from JMP (0). So the fixed-bit mask is 0xFFF4.

    Two masks were wrong before this:
      * 0xFFF5 forced bit0 = 0, silently missing 0xE255 -- the encoding this
        firmware actually uses. It passed the SBL self-test only because every
        target there is 0x4xxxx (A = 100, bit0 = 0).
      * 0xFFFC forced bit3 = 0, dropping address bit 18.
    Cross-checks: 0xE255 + 0x6216 -> P:$16216 (matches the table-driven
    decode); 0xE25C -> A18=1 -> P:$4xxxx (the SBL form).
    """
    o = w[i]
    if i + 1 >= len(w):
        return None
    if (o & 0xFFF4) not in (0xE254, 0xE250):
        return None
    hi = ((o >> 3 & 1) << 18) | ((o >> 1 & 1) << 17) | ((o & 1) << 16)
    return hi | w[i + 1]


def imms_in(w, lo, hi):
    """MOVE.L #imm32,reg loads in [lo,hi)."""
    out = []
    i = lo
    while i < hi - 2:
        if (w[i] & 0xFFF0) == 0xE410:
            val = (w[i + 2] << 16) | w[i + 1]
            out.append((i, REGN.get(w[i] & 0xF, f"r{w[i]&0xF}"), val))
            i += 3
            continue
        i += 1
    return out


def annot(v):
    """A word-address immediate -> byte address + landmark name."""
    ba = v * 2
    names = {0x7FFEA: "WORD_A", 0x7FFEC: "WORD_B", 0x80000: "FLASH_END",
             0x9800: "CAL_START", 0x1C000: "APP_START", 0x0: "FLASH_START"}
    t = f"byte 0x{ba:06X}"
    if ba in names:
        t += f" ={names[ba]}"
    return t


for VER in ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]:
    if not os.path.exists(f"{BASE}/{VER}/{VER}_blk1_0x0001C000.bin"):
        continue
    w = load(VER)
    nw = len(w)
    print("=" * 78)
    print(VER)

    # ---- all calls to the two CRC routines ------------------------------
    for name, tgt in (("P-space CRC", CRC_P), ("X-space CRC", CRC_X)):
        sites = [i for i in range(nw - 1) if jsr_target(w, i) == tgt]
        print(f"\n-- JSR P:${tgt:05X} ({name}): {len(sites)} call site(s) --")
        for s in sites:
            print(f"   call at blk1 word 0x{s:05X} byte 0x{s*2:05X} "
                  f"(P:${WORD_BASE+s:05X})")
            for i, reg, v in imms_in(w, max(0, s - 40), s):
                extra = f"   {annot(v)}" if v < 0x40000 else ""
                print(f"       b0x{i*2:05X}  MOVE.L #${v:08X},{reg}{extra}")

    # ---- the accumulator cell: where does the chain start/end? ----------
    print(f"\n-- X:${ACC:04X} accumulator traffic --")
    for i in range(nw - 1):
        if w[i + 1] != ACC:
            continue
        op = w[i]
        kind = {0xF77C: "READ  -> Y1", 0xF07C: "READ  -> A",
                0xF57C: "READ  -> Y0", 0xD57C: "WRITE <- Y0",
                0xD07C: "WRITE <- A1", 0x4C44: "CMP   with A",
                0xFF7C: "TST", 0x4E44: "DEC", 0xF67C: "MOVE X:->X:"}.get(op)
        if kind:
            print(f"   b0x{i*2:05X} {op:04X} {kind}")
    break
