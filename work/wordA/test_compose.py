#!/usr/bin/env python3
"""Untested region COMPOSITIONS, with the now-verified engine and all seeds.

Gap this closes: the only sweep that ever included blk0+blk1 concatenations
(`msgsets.bin`) was killed at poly 64 of 65536 -- so poly 0x1021, the polynomial
we have now PROVEN is the right one, was never tested against those regions.
`rangesweep` covered every (start,end) of blk1 alone, and `test_linear.py`
covered the contiguous linear image, but never a composition that SKIPS the
calibration gap.

That composition is worth testing because the checksum is a *software* integrity
monitor: it plausibly covers code only (blk0 + blk1, the two 14C217 blocks) and
deliberately excludes the separately-flashable 14C218 calibration -- which is
exactly the design already documented in §6 for word B, where the calibration is
made transparent (`sum16le(14C218) == 0xFFFF`).

Everything here is verified-engine + all 65536 seeds via the affine trick, and
demands a simultaneous match on all THREE versions.
"""
import glob

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))]
     for v in V}


def rev16(x):
    return int(f"{x:016b}"[::-1], 2)


RP = rev16(0x1021)
T = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ RP if c & 1 else c >> 1
    T.append(c)


def crc(d, init=0xFFFF):
    c = init
    for x in d:
        c = (c >> 8) ^ T[(c ^ x) & 0xFF]
    return c


for v in V:
    assert crc(b[v][2][:0x73FA], 0xFFFF) == \
        int.from_bytes(b[v][2][0x73FA:0x73FC], "little"), v
print("ENGINE CONTROL: blk2 word A reproduced on all 3 versions  PASS\n")

A = 0x63FEA
TL = {v: int.from_bytes(b[v][1][A:A + 2], "little") for v in V}
TB = {v: int.from_bytes(b[v][1][A:A + 2], "big") for v in V}

b0 = {v: b[v][0] for v in V}
b1 = {v: b[v][1] for v in V}
b2 = {v: b[v][2] for v in V}

CAND = {
    # code-only: the two 14C217 program blocks, calibration skipped
    "b0+b1[:A]":        {v: b0[v] + b1[v][:A] for v in V},
    "b0+b1[:A+2]":      {v: b0[v] + b1[v][:A + 2] for v in V},
    "b0+b1_full":       {v: b0[v] + b1[v] for v in V},
    # skip blk1's first 0x1000 (the §8.1 start) but keep blk0
    "b0+b1[0x1000:A]":  {v: b0[v] + b1[v][0x1000:A] for v in V},
    # blk0 after its own vector table
    "b0[0x1000:]+b1[:A]": {v: b0[v][0x1000:] + b1[v][:A] for v in V},
    # include the second image too
    "b0+b1[:A]+b2":     {v: b0[v] + b1[v][:A] + b2[v] for v in V},
    "b2+b0+b1[:A]":     {v: b2[v] + b0[v] + b1[v][:A] for v in V},
    # blk0 alone / blk0 then wrap
    "b0_only":          {v: b0[v] for v in V},
}

found = []
for name, msgs in CAND.items():
    if len({len(x) for x in msgs.values()}) != 1:
        print(f"  skip {name} (length differs)")
        continue
    base = {v: crc(msgs[v], 0) for v in V}
    cols = [crc(msgs[V[0]], 1 << k) ^ base[V[0]] for k in range(16)]
    if not all([crc(msgs[v], 1 << k) ^ base[v] for k in range(16)] == cols
               for v in V):
        print(f"  skip {name} (affine basis differs)")
        continue

    def ap(x):
        r = 0
        for k in range(16):
            if x >> k & 1:
                r ^= cols[k]
        return r

    for seed in range(65536):
        g = {v: base[v] ^ ap(seed) for v in V}
        for lbl, TT in (("LE", TL), ("BE", TB)):
            if g == TT:
                found.append((name, hex(seed), lbl))
                print(f"*** MATCH {found[-1]}", flush=True)
            gx = {v: g[v] ^ 0xFFFF for v in V}
            if gx == TT:
                found.append((name, hex(seed), lbl + "+xorFFFF"))
                print(f"*** MATCH {found[-1]}", flush=True)
    print(f"  {name:22s} len=0x{len(msgs[V[0]]):06X} init=FFFF -> "
          + " ".join(f"{base[v] ^ ap(0xFFFF):04X}" for v in V), flush=True)

print("\ntarget:", {v: f"{TL[v]:04X}" for v in V})
print("matches:", found)
