#!/usr/bin/env python3
"""Corrected region hypothesis for 14C217 blk1 word A.

The routine at blk1+0x81B4 loads three longs. The self-consistent reading is:
    R2 = 0x0000E800   START  (word addr)      -> byte 0x0001D000
    R0 = 0x0003FFF5   END    (word addr)      -> byte 0x0007FFEA (word A)
    R1 = 0x000317F5   COUNT  (words)          -> 0x62FEA bytes
and indeed  0x3FFF5 - 0xE800 == 0x317F5  exactly.

=> checked region = bytes [0x0001D000 .. 0x0007FFEA)
                  = blk1[0x1000 .. 0x63FEA)
i.e. blk1 MINUS its first 0x1000 bytes (vector/header area), up to word A.
That 0x1000-byte skip is why every "from the start of the image" sweep failed.

Emits msgsets for the C sweeper (all 65536 polys) AND runs the additive
families directly here. All tests demand a match on all THREE versions.
"""
import glob
import struct

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))] for v in V}
b1 = {v: b[v][1] for v in V}
A_OFF = 0x63FEA
TGT_LE = {v: int.from_bytes(b1[v][A_OFF:A_OFF + 2], "little") for v in V}
TGT_BE = {v: int.from_bytes(b1[v][A_OFF:A_OFF + 2], "big") for v in V}

S = 0x1000       # blk1-relative start (byte 0x1D000)
E = 0x63FEA      # blk1-relative end   (byte 0x7FFEA = word A)

RANGES = {
    "H_exact":  (S, E),
    "H_inclA":  (S, E + 2),
    "H_toB":    (S, E + 4),
    "H_toend":  (S, 0x64000),
    "H_s0FFE":  (S - 2, E),
    "H_s1002":  (S + 2, E),
}

sets = []
for name, (s, e) in RANGES.items():
    msgs = {v: b1[v][s:e] for v in V}
    assert len({len(x) for x in msgs.values()}) == 1
    sets.append((name, [(v, msgs[v]) for v in V]))

with open("msgsets_hyp.bin", "wb") as f:
    f.write(struct.pack("<I", len(sets)))
    for name, items in sets:
        nm = name.encode()
        f.write(struct.pack("<BII", len(nm), len(items), len(items[0][1])))
        f.write(nm)
        for v, msg in items:
            f.write(b1[v][A_OFF:A_OFF + 2])
            f.write(msg)
print(f"wrote msgsets_hyp.bin: {len(sets)} sets")
for name, items in sets:
    print(f"  {name:10s} len=0x{len(items[0][1]):X}")

# ---- additive families, inline -------------------------------------------
M = 0xFFFF


def words(d, end):
    return [int.from_bytes(d[i:i + 2], end) for i in range(0, len(d) // 2 * 2, 2)]


def f_sum(ws, i=0):
    s = i
    for w in ws:
        s = (s + w) & M
    return s


def f_xor(ws, i=0):
    s = i
    for w in ws:
        s ^= w
    return s


def f_ones(ws, i=0):
    s = i
    for w in ws:
        s += w
        s = (s & M) + (s >> 16)
    return s & M


def f_sub(ws, i=0):
    s = i
    for w in ws:
        s = (s - w) & M
    return s


def f_rotxor(ws, i=0):
    s = i
    for w in ws:
        s = ((s << 1) | (s >> 15)) & M
        s ^= w
    return s


def f_fletch(ws, i=0):
    a, bb = i & 0xFF, (i >> 8) & 0xFF
    for w in ws:
        for byte in (w & 0xFF, w >> 8):
            a = (a + byte) & 0xFF
            bb = (bb + a) & 0xFF
    return (bb << 8) | a


ACCS = [("sum16", f_sum), ("xor16", f_xor), ("ones16", f_ones), ("sub16", f_sub),
        ("rotxor", f_rotxor), ("fletcher", f_fletch)]

print("\ntarget:", {v: hex(TGT_LE[v]) for v in V})
hits = []
for rname, (s, e) in RANGES.items():
    seg = {v: b1[v][s:e] for v in V}
    for wend in ("little", "big"):
        W = {v: words(seg[v], wend) for v in V}
        Wr = {v: list(reversed(W[v])) for v in V}
        for aname, fn in ACCS:
            for dirn, WW in (("fwd", W), ("rev", Wr)):
                for init in (0x0000, 0xFFFF, 0x0001):
                    raw = {v: fn(WW[v], init) for v in V}
                    for adj, g in (
                        ("raw", raw),
                        ("comp", {v: raw[v] ^ M for v in V}),
                        ("neg", {v: (-raw[v]) & M for v in V}),
                        ("compm1", {v: (M - raw[v]) & M for v in V}),
                        ("p1", {v: (raw[v] + 1) & M for v in V}),
                        ("m1", {v: (raw[v] - 1) & M for v in V}),
                    ):
                        if g == TGT_LE:
                            hits.append((rname, wend, aname, dirn, hex(init), adj, "LE"))
                            print("MATCH", hits[-1])
                        if g == TGT_BE:
                            hits.append((rname, wend, aname, dirn, hex(init), adj, "BE"))
                            print("MATCH", hits[-1])
print("additive matches:", len(hits))
