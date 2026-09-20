#!/usr/bin/env python3
"""Validate DSP56800E opcode encodings against the real SBL image.

Goal: prove we can read this instruction set correctly BEFORE relying on any
conclusion drawn from it.

Encodings taken verbatim from the DSP56800E/EX Reference Manual (docs/erm.txt,
fetched from community.nxp.com), Appendix A:

  JSR <ABS19>:   1110 0010 0101 A1AA  +  AAAAAAAAAAAAAAAA
                 = 0xE25x with bit3 pattern, second word = low 16 addr bits
  JSR <ABS21>:   1110 0AAA 0A11 AAAA  +  AAAAAAAAAAAAAAAA

The single most repeated word in the image is 0xE25C (55 times) and 0xE030
(156 times). If 0xE25C is a JSR <ABS19>, then every occurrence must be followed
by a word that, combined, yields a target INSIDE the SBL's own address range
P:0x4F800..0x4FFC1. That is a strong, falsifiable test: random data would not
land in a 1985-word window 55 times out of 55.
"""
import struct

P = ("/home/gl/Projects/ford/PSCM/Research/bins/BV6T-14C220-AA/"
     "BV6T-14C220-AA_blk0_0x0009F000.bin")
d = open(P, 'rb').read()
NW = len(d) // 2
w = list(struct.unpack('<%dH' % NW, d[:NW * 2]))
BASE = 0x4F800
LO, HI = BASE, BASE + NW

print(f"SBL window P:0x{LO:05X}..0x{HI:05X}  ({NW} words)")
print()

# ---- JSR <ABS19> : 1110 0010 0101 A1AA  (opcode word) -------------------
# bits: 15..4 = 1110 0010 0101 ; bit3=A18? per manual row: A 1 A A
# The manual row reads:  1 1 1 0 0 0 1 0 0 1 0 1 A 1 A A
# so opcode & 0xFFF5 == 0xE254, and the three A bits are b3,b1,b0 = A18,A17,A16
def decode_jsr_abs19(op, nxt):
    if (op & 0xFFF5) != 0xE254:
        return None
    a18 = (op >> 3) & 1
    a17 = (op >> 1) & 1
    a16 = op & 1
    return (a18 << 18) | (a17 << 17) | (a16 << 16) | nxt


print("=" * 74)
print("TEST 1: is 0xE25C a JSR <ABS19>?   (0xE25C & 0xFFF5 = 0x%04X, want 0xE254)"
      % (0xE25C & 0xFFF5))
print("=" * 74)
hits, inside = 0, 0
targets = []
for i in range(NW - 1):
    t = decode_jsr_abs19(w[i], w[i + 1])
    if t is None:
        continue
    hits += 1
    ok = LO <= t < HI
    inside += ok
    targets.append((i, w[i], t, ok))

print(f"  candidate JSR <ABS19> sites : {hits}")
print(f"  targets inside the SBL      : {inside}")
if hits:
    print(f"  hit rate                    : {inside/hits*100:.1f}%")
print()
for i, op, t, ok in targets[:20]:
    print(f"    P:{BASE+i:05X}  {op:04X} {w[i+1]:04X}  -> JSR P:0x{t:05X}  "
          f"{'IN' if ok else 'out-of-range'}")
if len(targets) > 20:
    print(f"    ... and {len(targets)-20} more")

print()
print("=" * 74)
print("TEST 2: call-target histogram (are they real function entries?)")
print("=" * 74)
from collections import Counter
c = Counter(t for _, _, t, ok in targets if ok)
print(f"  {len(c)} distinct in-range targets from {inside} calls")
for t, n in c.most_common(15):
    print(f"    P:0x{t:05X}  called {n:2d}x   (file offset 0x{(t-BASE)*2:04X})")

print()
print("=" * 74)
print("VERDICT")
print("=" * 74)
if hits and inside / hits > 0.9 and len(c) < inside:
    print("  PASS - 0xE25C decodes as JSR <ABS19>. Nearly every target lands inside")
    print("  the SBL and targets repeat (real functions called from many sites).")
    print("  => The DSP56800E encoding is confirmed readable on this image.")
else:
    print("  INCONCLUSIVE - do not build on this decoding.")
