#!/usr/bin/env python3
"""Non-CRC (additive / xor / Fletcher) families over the region the blk1
self-check routine actually addresses, plus boundary variants.

Region from disassembly (see find_regions.py / gen_region.py):
    words [0x317F5 .. 0x3FFF5)  =  bytes [0x62FEA .. 0x7FFEA)
    blk1-relative [0x46FEA .. 0x63FEA)   length 0x1D000 bytes
Word A is stored at blk1+0x63FEA, i.e. immediately after this region.

Tests each accumulator over the region, forwards and backwards, LE/BE word
order, with optional complement / +-1 / negate adjustments, and demands a
simultaneous match on ALL THREE versions.
"""
import glob

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))] for v in V}
b1 = {v: b[v][1] for v in V}
b0 = {v: b[v][0] for v in V}
C218 = {"BV6T-14C217-AF": open(f"{BASE}/BV6T-14C218-AF/BV6T-14C218-AF_blk0_0x00009800.bin", "rb").read(),
        "CV6T-14C217-AR": open(f"{BASE}/CV6T-14C218-AX/CV6T-14C218-AX_blk0_0x00009800.bin", "rb").read()}
A_OFF = 0x63FEA
TGT = {v: int.from_bytes(b1[v][A_OFF:A_OFF + 2], "little") for v in V}
TGT_BE = {v: int.from_bytes(b1[v][A_OFF:A_OFF + 2], "big") for v in V}

M = 0xFFFF


def words(d, end):
    return [int.from_bytes(d[i:i + 2], end) for i in range(0, len(d) // 2 * 2, 2)]


def acc_sum16(ws, init=0):
    s = init
    for w in ws:
        s = (s + w) & M
    return s


def acc_xor16(ws, init=0):
    s = init
    for w in ws:
        s ^= w
    return s


def acc_ones(ws, init=0):
    s = init
    for w in ws:
        s += w
        s = (s & M) + (s >> 16)
    return s & M


def acc_sub16(ws, init=0):
    s = init
    for w in ws:
        s = (s - w) & M
    return s


def acc_fletch(ws, init=0):
    a = init & 0xFF
    bb = (init >> 8) & 0xFF
    for w in ws:
        for byte in (w & 0xFF, w >> 8):
            a = (a + byte) & 0xFF
            bb = (bb + a) & 0xFF
    return (bb << 8) | a


def acc_rot_xor(ws, init=0):
    """xor with 1-bit left rotate each step - common cheap 'CRC-like' hash."""
    s = init
    for w in ws:
        s = ((s << 1) | (s >> 15)) & M
        s ^= w
    return s


def acc_sum8(d, init=0):
    s = init
    for x in d:
        s = (s + x) & M
    return s


ACCS_W = [("sum16", acc_sum16), ("xor16", acc_xor16), ("ones16", acc_ones),
          ("sub16", acc_sub16), ("fletcher", acc_fletch), ("rotxor", acc_rot_xor)]

RANGES = {
    "R_exact":   (0x46FEA, 0x63FEA),
    "R_inclA":   (0x46FEA, 0x63FEC),
    "R_toB":     (0x46FEA, 0x63FEE),
    "R_toend":   (0x46FEA, 0x64000),
    "Rbytecnt":  (0x63FEA - 0xE800, 0x63FEA),
    "b1_toA":    (0x00000, 0x63FEA),
    "b1_full":   (0x00000, 0x64000),
}
INITS = [0x0000, 0xFFFF, 0x0001]

print("target word A (LE):", {v: hex(TGT[v]) for v in V})
print()
found = []
for rname, (s, e) in RANGES.items():
    seg = {v: b1[v][s:e] for v in V}
    for wend in ("little", "big"):
        W = {v: words(seg[v], wend) for v in V}
        Wr = {v: list(reversed(W[v])) for v in V}
        for aname, fn in ACCS_W:
            for dirn, WW in (("fwd", W), ("rev", Wr)):
                for init in INITS:
                    raw = {v: fn(WW[v], init) for v in V}
                    for adj, g in (
                        ("raw", raw),
                        ("comp", {v: raw[v] ^ M for v in V}),
                        ("neg", {v: (-raw[v]) & M for v in V}),
                        ("p1", {v: (raw[v] + 1) & M for v in V}),
                        ("m1", {v: (raw[v] - 1) & M for v in V}),
                        ("compm1", {v: (M - raw[v]) & M for v in V}),
                    ):
                        if g == TGT:
                            found.append((rname, wend, aname, dirn, hex(init), adj, "LE"))
                            print("MATCH", found[-1])
                        if g == TGT_BE:
                            found.append((rname, wend, aname, dirn, hex(init), adj, "BE"))
                            print("MATCH", found[-1])
    # byte-wise sum too
    for init in INITS:
        raw = {v: acc_sum8(seg[v], init) for v in V}
        for adj, g in (("raw", raw), ("comp", {v: raw[v] ^ M for v in V}),
                       ("neg", {v: (-raw[v]) & M for v in V})):
            if g == TGT:
                found.append((rname, "-", "sum8", "fwd", hex(init), adj, "LE"))
                print("MATCH", found[-1])
            if g == TGT_BE:
                found.append((rname, "-", "sum8", "fwd", hex(init), adj, "BE"))
                print("MATCH", found[-1])
    print(f"  range {rname} done")

print()
print("TOTAL matches:", len(found))
for f in found:
    print("  ", f)
