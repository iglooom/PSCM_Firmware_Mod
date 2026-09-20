#!/usr/bin/env python3
"""Run the VERIFIED engine over the TRUE LINEAR IMAGE.

Why this test was missing
------------------------
Everything else is now pinned:
  * engine   - reflected CCITT poly 0x1021 w/ the blk2 table; reproduces blk2's
               known word A exactly (control below)
  * operands - bits 9-7 = dst, bits 6-4 = src (from `LSRR.W EEE,FFF`)
  * feed     - the asm word-form is PROVABLY identical to byte-wise lo-then-hi
  * seed     - all 65536 swept
so only the REGION can be wrong. And there was a real blind spot:

`rangesweep` swept every (start,end) of **blk1 only**, and the earlier
"from offset 0" variants used `blk1[0:...]` - but **blk1[0] is flash byte
0x1C000, not byte 0**. The CRC pointer walks P-space *word* addresses, and
words [0 .. 0x3FFF5) = bytes [0 .. 0x7FFEA) span the WHOLE lower flash:

    0x00000000  blk0        (14C217 blk0,  0x09800)
    0x00009800  calibration (14C218 blk0,  0x12800)   <- never in any blk1 sweep
    0x0001C000  blk1        (14C217 blk1,  0x64000)

So any region starting below 0x1C000 has never been tested with this engine.
Build the real linear image and sweep the plausible starts, each over all
65536 seeds via the affine trick.
"""
import glob

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V217 = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
PAIR = {"BV6T-14C217-AF": "BV6T-14C218-AF", "CV6T-14C217-AR": "CV6T-14C218-AX"}

b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))]
     for v in V217}
C218 = {k: open(f"{BASE}/{p}/{p}_blk0_0x00009800.bin", "rb").read()
        for k, p in PAIR.items()}


def rev16(x):
    return int(f"{x:016b}"[::-1], 2)


RP = rev16(0x1021)
T = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ RP if c & 1 else c >> 1
    T.append(c)


def crc(data, init=0xFFFF):
    c = init
    for x in data:
        c = (c >> 8) ^ T[(c ^ x) & 0xFF]
    return c


# ---- control: the engine must reproduce blk2's known word A ---------------
for v in V217:
    assert crc(b[v][2][:0x73FA], 0xFFFF) == \
        int.from_bytes(b[v][2][0x73FA:0x73FC], "little"), v
print("ENGINE CONTROL: blk2 word A reproduced on all 3 versions  PASS\n")

A_BYTE = 0x7FFEA                      # word A in linear flash
TL, TB = {}, {}
for v in V217:
    w = b[v][1][0x63FEA:0x63FEC]
    TL[v] = int.from_bytes(w, "little")
    TB[v] = int.from_bytes(w, "big")


def linear(v, fill=b"\xff"):
    buf = bytearray(fill * 0x80000)
    buf[0x00000:0x09800] = b[v][0]
    if v in C218:
        buf[0x09800:0x1C000] = C218[v]
    buf[0x1C000:0x80000] = b[v][1]
    return bytes(buf)


# only the two versions with a matching 14C218 are fully real
VV = [v for v in V217 if v in C218]
print(f"versions with matching 14C218: {VV}")
LIN = {v: linear(v) for v in VV}
LINF = {v: linear(v) for v in V217}          # FF-filled gap for all three

STARTS = [0x00000, 0x01000, 0x09800, 0x1C000, 0x1D000]
ENDS = [A_BYTE, A_BYTE + 2, 0x80000]

found = []
for tag, IMG, vers in (("real-218", LIN, VV), ("FF-gap", LINF, V217)):
    for s in STARTS:
        for e in ENDS:
            segs = {v: IMG[v][s:e] for v in vers}
            if len({len(x) for x in segs.values()}) != 1:
                continue
            base = {v: crc(segs[v], 0) for v in vers}
            cols = [crc(segs[vers[0]], 1 << k) ^ base[vers[0]] for k in range(16)]
            ok = all([crc(segs[v], 1 << k) ^ base[v] for k in range(16)] == cols
                     for v in vers)
            if not ok:
                continue

            def ap(x):
                r = 0
                for k in range(16):
                    if x >> k & 1:
                        r ^= cols[k]
                return r

            hit = None
            for seed in range(65536):
                g = {v: base[v] ^ ap(seed) for v in vers}
                for lbl, TT in (("LE", TL), ("BE", TB)):
                    want = {v: TT[v] for v in vers}
                    if g == want:
                        hit = (tag, hex(s), hex(e), hex(seed), lbl)
                        found.append(hit)
                        print(f"*** MATCH {hit}", flush=True)
                    gx = {v: g[v] ^ 0xFFFF for v in vers}
                    if gx == want:
                        hit = (tag, hex(s), hex(e), hex(seed), lbl + "+xorFFFF")
                        found.append(hit)
                        print(f"*** MATCH {hit}", flush=True)
            print(f"  {tag} [0x{s:05X}..0x{e:05X}) init=FFFF -> "
                  + " ".join(f"{base[v] ^ ap(0xFFFF):04X}" for v in vers))

print("\ntarget:", {v: f"{TL[v]:04X}" for v in V217})
print("matches:", found)
