#!/usr/bin/env python3
"""Test the CRC exactly as the routine at P:$16216 computes it.

From find_calls.py, the single meaningful call site (blk1+0x820A) sets up:
    MOVE.L #$0000E800,A      ; START word address  -> byte 0x01D000
    MOVE.L #$000317F5,B      ; COUNT in WORDS      -> 0x62FEA bytes
    (0xE800 + 0x317F5 = 0x3FFF5 = word A, so the region ends exactly at word A)
and from the loop body at P:$16216:
    MOVE.W P:(R2)+,Y0        ; read ONE PROGRAM WORD
    ... EOR / ZXT.B / LSRR.W #8 / MOVE.W X:(R1)+  TWICE per word
i.e. each 16-bit program word contributes TWO byte-steps of the reflected CRC.

The open question is byte ORDER within the word and the initial value. The
running CRC is chained through X:$11EF (read into Y1 before the call, stored
from Y0 after), so the seed may be whatever a previous stage left - test a
full sweep of seeds as well as the standard ones.

Region: words [0xE800 .. 0x3FFF5) = bytes [0x1D000 .. 0x7FFEA)
      = blk1[0x1000 .. 0x63FEA)          (blk1 loads at byte 0x1C000)
"""
import glob

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))]
     for v in V}
b1 = {v: b[v][1] for v in V}
A_OFF = 0x63FEA
TGT_LE = {v: int.from_bytes(b1[v][A_OFF:A_OFF + 2], "little") for v in V}
TGT_BE = {v: int.from_bytes(b1[v][A_OFF:A_OFF + 2], "big") for v in V}


def rev16(x):
    return int(f"{x:016b}"[::-1], 2)


RP = rev16(0x1021)
T = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ RP if c & 1 else c >> 1
    T.append(c)

# confirm the table is the one the code points at (blk2+0x20EC etc.)
OFFS = {"BV6T-14C217-AF": 0x20EC, "CV6T-14C217-AH": 0x2112, "CV6T-14C217-AR": 0x21F2}
blob = b"".join(x.to_bytes(2, "little") for x in T)
for v in V:
    assert b[v][2][OFFS[v]:OFFS[v] + 512] == blob, v
print("table at blk2+0x20EC/0x2112/0x21F2 == reflected CCITT: confirmed all 3")


def crc(seq, init):
    c = init
    for x in seq:
        c = (c >> 8) ^ T[(c ^ x) & 0xFF]
    return c


S, E = 0x1000, A_OFF          # blk1-relative, from the call arguments
seg = {v: b1[v][S:E] for v in V}
print(f"region blk1[0x{S:X}..0x{E:X}) = {E-S} bytes = {(E-S)//2} words"
      f"  (count arg 0x317F5 = {0x317F5})")

ORDERS = {
    "lo_then_hi": lambda d: list(d),
    "hi_then_lo": lambda d: [x for i in range(0, len(d) - 1, 2)
                             for x in (d[i + 1], d[i])],
}

print("\n-- sweep ALL 65536 seeds, both byte orders, both stored endiannesses --")
found = []
for oname, gen in ORDERS.items():
    seqs = {v: gen(seg[v]) for v in V}
    # crc is affine in the seed: crc(m,s) = crc(m,0) ^ L(s).
    # Build L once per order by running the 16 basis seeds on a SHORT proxy?
    # No - L depends only on length, and all three versions share it, so just
    # compute crc(m,0) and crc(m,1<<k) for each version: 17 passes per version.
    base = {v: crc(seqs[v], 0) for v in V}
    cols = {v: [crc(seqs[v], 1 << k) ^ base[v] for k in range(16)] for v in V}
    # L is identical across versions (same length) - verify, then solve.
    assert cols[V[0]] == cols[V[1]] == cols[V[2]], "L differs: lengths differ"
    L = cols[V[0]]

    def apply_L(s):
        r = 0
        for k in range(16):
            if s >> k & 1:
                r ^= L[k]
        return r

    for seed in range(65536):
        d = apply_L(seed)
        got = {v: base[v] ^ d for v in V}
        for lbl, TT in (("LE", TGT_LE), ("BE", TGT_BE)):
            if got == TT:
                found.append((oname, hex(seed), lbl))
                print(f"*** MATCH order={oname} seed=0x{seed:04X} stored={lbl}")
        for lbl, TT in (("LE", TGT_LE), ("BE", TGT_BE)):
            g2 = {v: got[v] ^ 0xFFFF for v in V}
            if g2 == TT:
                found.append((oname, hex(seed), lbl + "+xorFFFF"))
                print(f"*** MATCH order={oname} seed=0x{seed:04X} "
                      f"stored={lbl} xorout=FFFF")
    print(f"  {oname}: crc(seed=0) = "
          + " ".join(f"{v[:2]}:{base[v]:04X}" for v in V)
          + f"   crc(seed=FFFF) = "
          + " ".join(f"{v[:2]}:{base[v]^apply_L(0xFFFF):04X}" for v in V))

print("\ntarget:", {v: f"{TGT_LE[v]:04X}" for v in V})
print("matches:", found)
