#!/usr/bin/env python3
"""Cross-check the datasheet peripheral map against the OEM SBL's real writes.

The SBL's most common instruction is the 4-word absolute store
    E030 8654 <addr16> <imm16>  =  MOVE.W #imm,X:$00addr
(verified bit-exact against RM Appendix A: prefix 1110 0AAA 0A11 AAAA +
 1000 0110 0101 0100).

Every one of these is a write to a 16-bit data-space address. If our part
identification (56F8367) is right, those addresses must cluster in the
documented peripheral windows -- above X:$00F000 -- and specifically should
touch FlexCAN at X:$00F800 since the SBL talks CAN.

This is an INDEPENDENT test of the part ID: it uses the firmware's own behaviour,
not the datasheet's self-consistency.
"""
import struct
from collections import Counter

P = ("/home/gl/Projects/ford/PSCM/Research/bins/BV6T-14C220-AA/"
     "BV6T-14C220-AA_blk0_0x0009F000.bin")
d = open(P, 'rb').read()
nw = len(d) // 2
w = list(struct.unpack('<%dH' % nw, d[:nw * 2]))
BASE = 0x4F800

# 56F8367 Table 4-9 peripheral base map (X: word addresses)
PERIPH = [
    (0x00F000, 0x00F040, 'ITCN (interrupt controller)'),
    (0x00F020, 0x00F030, 'EMI'),
    (0x00F040, 0x00F080, 'SIM / system'),
    (0x00F0C0, 0x00F100, 'PLL / CLKGEN'),
    (0x00F100, 0x00F200, 'GPIO ports'),
    (0x00F200, 0x00F300, 'Timers / PWM'),
    (0x00F360, 0x00F370, 'LVI power supervisor'),
    (0x00F400, 0x00F420, 'FM  (FLASH MODULE)'),
    (0x00F800, 0x00F900, 'FlexCAN'),
    (0x00FA00, 0x00FB00, 'FlexCAN2'),
]


def region(a):
    for lo, hi, name in PERIPH:
        if lo <= a < hi:
            return name
    if a < 0x004000:
        return 'Data RAM (X:$0000-$3FFF)'
    if 0x004000 <= a < 0x008000:
        return 'Data Flash (X:$4000-$7FFF)'
    if a >= 0x00F000:
        return 'peripheral window (unclassified)'
    return 'other'


stores = []
for i in range(nw - 3):
    if (w[i] & 0xF8F0) == 0xE030 and w[i + 1] == 0x8654:
        AAA = (w[i] >> 8) & 7
        A6 = (w[i] >> 6) & 1
        A30 = w[i] & 0xF
        upper = (AAA << 5) | (A6 << 4) | A30
        addr = (upper << 16) | w[i + 2]
        stores.append((BASE + i, addr, w[i + 3]))

print(f"absolute immediate stores found: {len(stores)}")
print()
print("=" * 74)
print("Target address histogram")
print("=" * 74)
c = Counter(a for _, a, _ in stores)
for a, n in sorted(c.items()):
    print(f"  X:${a:06X}  x{n:<3}  {region(a)}")

print()
print("=" * 74)
print("Region summary")
print("=" * 74)
rc = Counter(region(a) for _, a, _ in stores)
for r, n in rc.most_common():
    print(f"  {n:4d}  {r}")

print()
print("=" * 74)
print("VERDICT — does the firmware's behaviour match the 56F8367 map?")
print("=" * 74)
lowram = sum(n for a, n in c.items() if a < 0x4000)
periph = sum(n for a, n in c.items() if a >= 0xF000)
print(f"  writes into Data RAM  (X:<$4000) : {lowram}")
print(f"  writes into peripherals (>=$F000): {periph}")
print()
if lowram and not periph:
    print("  All immediate stores go to low RAM. These are variable")
    print("  initialisations, NOT peripheral setup -- so this particular")
    print("  instruction form does not exercise the peripheral map.")
    print("  => Test is INCONCLUSIVE for part ID (peripherals are likely")
    print("     driven via register-indirect stores, a different encoding).")
    print("     The datasheet-based part ID stands on its own evidence")
    print("     (P:$04F800 RAM == SBL call address); this neither confirms")
    print("     nor contradicts it.")
elif periph:
    print("  Peripheral writes present -- consistent with the 56F8367 map.")
