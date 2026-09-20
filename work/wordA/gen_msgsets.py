#!/usr/bin/env python3
"""Build the message-set blob consumed by sweep.c for the 14C217 blk1 word-A hunt.

Rationale
---------
Any table-driven CRC is AFFINE over GF(2):
    crc_{init,xorout}(m) = crc_{0,0}(m) ^ K      (K depends only on init/xorout/len)
Therefore for two firmware versions of the same part:
    A1 ^ A2 == crc_{0,0}(m1 ^ m2)
The init and xorout dimensions CANCEL. That removes two whole axes from the
search and lets us sweep all 65536 polynomials instead of a hand-picked catalogue.
Each message set below must have identical length across versions.
"""
import glob
import os
import struct

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "msgsets.bin")

V217 = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))] for v in V217}
C218 = {
    "BV": open(f"{BASE}/BV6T-14C218-AF/BV6T-14C218-AF_blk0_0x00009800.bin", "rb").read(),
    "CV": open(f"{BASE}/CV6T-14C218-AX/CV6T-14C218-AX_blk0_0x00009800.bin", "rb").read(),
}
C386 = {
    "BV": open(f"{BASE}/BV6T-14C386-AA/BV6T-14C386-AA_blk0_0x04008000.bin", "rb").read(),
    "CV": open(f"{BASE}/CV6T-14C386-AB/CV6T-14C386-AB_blk0_0x04008000.bin", "rb").read(),
}
PAIR = {"BV6T-14C217-AF": "BV", "CV6T-14C217-AR": "CV"}

A_OFF = 0x63FEA          # word A inside blk1
B_OFF = 0x63FEC          # word B inside blk1
BLK1_LEN = 0x64000

# stored word A, raw bytes, per version
TGT = {v: b[v][1][A_OFF:A_OFF + 2] for v in V217}


def linear(v, fill=b"\xff", gap=None):
    """blk0 ++ 14C218 ++ blk1 as the ECU sees it (0x00000..0x80000)."""
    buf = bytearray(fill * 0x80000)
    b0, b1, _ = b[v]
    buf[0:0x9800] = b0
    if gap is not None:
        buf[0x9800:0x1C000] = gap
    elif v in PAIR:
        buf[0x9800:0x1C000] = C218[PAIR[v]]
    buf[0x1C000:0x80000] = b1
    return bytes(buf)


def zeroA(d, off, val=b"\x00\x00"):
    m = bytearray(d)
    m[off:off + 2] = val
    return bytes(m)


sets = []   # (name, [(version, message_bytes)])


def add(name, mapping):
    lens = {len(x) for x in mapping.values()}
    assert len(lens) == 1, (name, lens)
    sets.append((name, [(v, mapping[v]) for v in V217 if v in mapping]))


# ---------------------------------------------------------------- blk1 only: 3 files
b1 = {v: b[v][1] for v in V217}
add("b1_to_A",      {v: b1[v][:A_OFF] for v in V217})
add("b1_to_B",      {v: b1[v][:B_OFF] for v in V217})
add("b1_to_FFEE",   {v: b1[v][:0x63FEE] for v in V217})
add("b1_full",      {v: b1[v] for v in V217})
add("b1_full_A0",   {v: zeroA(b1[v], A_OFF) for v in V217})
add("b1_full_AFF",  {v: zeroA(b1[v], A_OFF, b"\xff\xff") for v in V217})
add("b1_toB_A0",    {v: zeroA(b1[v][:B_OFF], A_OFF) for v in V217})
add("b1_toFFEE_A0", {v: zeroA(b1[v][:0x63FEE], A_OFF) for v in V217})
# code-only prefixes (guess: checksum covers only the application text)
add("b1_first_32k", {v: b1[v][:0x8000] for v in V217})
add("b1_after_hdr", {v: b1[v][0x1000:A_OFF] for v in V217})

# ---------------------------------------------------------------- blk0 (+blk1): 3 files
b0 = {v: b[v][0] for v in V217}
add("b0",           {v: b0[v] for v in V217})
add("b0_b1_to_A",   {v: b0[v] + b1[v][:A_OFF] for v in V217})
add("b0_b1_full",   {v: b0[v] + b1[v] for v in V217})
add("b0_b1_A0",     {v: b0[v] + zeroA(b1[v], A_OFF) for v in V217})
b2 = {v: b[v][2] for v in V217}
add("b0_b1A_b2",    {v: b0[v] + b1[v][:A_OFF] + b2[v] for v in V217})
add("b2_b0_b1A",    {v: b2[v] + b0[v] + b1[v][:A_OFF] for v in V217})
add("b0_b1_full_b2", {v: b0[v] + b1[v] + b2[v] for v in V217})

# ---------------------------------------------------------------- linear image: 2 files
LIN = {v: linear(v) for v in PAIR}
add("lin_to_A",   {v: LIN[v][:0x7FFEA] for v in PAIR})
add("lin_to_B",   {v: LIN[v][:0x7FFEC] for v in PAIR})
add("lin_full",   {v: LIN[v] for v in PAIR})
add("lin_full_A0", {v: zeroA(LIN[v], 0x7FFEA) for v in PAIR})
add("lin_from_218", {v: LIN[v][0x9800:0x7FFEA] for v in PAIR})
add("lin_from_b1", {v: LIN[v][0x1C000:0x7FFEA] for v in PAIR})
add("lin_to_1C000", {v: LIN[v][:0x1C000] for v in PAIR})
add("lin_cal_only", {v: LIN[v][0x9800:0x1C000] for v in PAIR})
add("lin_plus_386", {v: LIN[v][:0x7FFEA] + C386[PAIR[v]][:0x788] for v in PAIR})
add("lin_plus_b2", {v: LIN[v][:0x7FFEA] + b2[v] for v in PAIR})
# gap filled with 00 instead of the real calibration
LIN0 = {v: linear(v, gap=b"\x00" * 0x12800) for v in V217}
add("lin0_to_A",  {v: LIN0[v][:0x7FFEA] for v in V217})
add("lin0_full",  {v: LIN0[v] for v in V217})
LINF = {v: linear(v, gap=b"\xff" * 0x12800) for v in V217}
add("linF_to_A",  {v: LINF[v][:0x7FFEA] for v in V217})

# ---------------------------------------------------------------- serialise
with open(OUT, "wb") as f:
    f.write(struct.pack("<I", len(sets)))
    for name, items in sets:
        nm = name.encode()
        f.write(struct.pack("<BII", len(nm), len(items), len(items[0][1])))
        f.write(nm)
        for v, msg in items:
            f.write(TGT[v])
            f.write(msg)

tot = sum(len(m) * len(items) for _, items in sets for _, m in items) if sets else 0
print(f"wrote {OUT}: {len(sets)} sets")
for name, items in sets:
    print(f"  {name:16s} nver={len(items)} len=0x{len(items[0][1]):X}")
print("stored word A:", {v: TGT[v].hex() for v in V217})
