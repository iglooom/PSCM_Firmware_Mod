#!/usr/bin/env python3
"""Focused msgset: the range the blk1 self-check routine actually loads.

Disassembly of the routine at blk1+0x81B4 (all 3 versions, same structure):
    MOVE.L #$0003FFF5,R0     ; word addr of WORD A  (byte 0x7FFEA)
    MOVE.L #$0000E800,R2     ; count = 0xE800 words = 0x1D000 bytes
    MOVE.L #$000317F5,R1     ; word addr 0x317F5    (byte 0x62FEA)
and  0x317F5 + 0xE800 == 0x3FFF5  exactly.

=> the checked region is words [0x317F5 .. 0x3FFF5)
   = bytes [0x62FEA .. 0x7FFEA)  = blk1[0x46FEA .. 0x63FEA)
This is a PARTIAL range, which explains why every whole-image sweep failed.

Also emit neighbouring/alternate cuts so the exact boundary convention
(inclusive/exclusive, word vs byte count) is pinned down.
"""
import glob
import struct

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))] for v in V}
b1 = {v: b[v][1] for v in V}
b0 = {v: b[v][0] for v in V}
C218 = {"BV6T-14C217-AF": open(f"{BASE}/BV6T-14C218-AF/BV6T-14C218-AF_blk0_0x00009800.bin", "rb").read(),
        "CV6T-14C217-AR": open(f"{BASE}/CV6T-14C218-AX/CV6T-14C218-AX_blk0_0x00009800.bin", "rb").read()}
A_OFF = 0x63FEA

# blk1-relative window from the disassembly
S = 0x46FEA        # = byte 0x62FEA
E = 0x63FEA        # = byte 0x7FFEA (word A)

sets = []


def add(name, mapping):
    lens = {len(x) for x in mapping.values()}
    assert len(lens) == 1, (name, lens)
    sets.append((name, [(v, mapping[v]) for v in V if v in mapping]))


# the headline hypothesis and boundary variants
add("R_exact",      {v: b1[v][S:E] for v in V})
add("R_incl_A",     {v: b1[v][S:E + 2] for v in V})
add("R_to_B",       {v: b1[v][S:E + 4] for v in V})
add("R_minus2",     {v: b1[v][S - 2:E] for v in V})
add("R_plus2",      {v: b1[v][S + 2:E] for v in V})
add("R_to_end",     {v: b1[v][S:] for v in V})
# byteswapped-in-place (word-order) variants handled by sweep2 transforms

# maybe the count is BYTES not words -> 0xE800 bytes ending at word A
add("Rb_bytes",     {v: b1[v][E - 0xE800:E] for v in V})
add("Rb_bytes_inA", {v: b1[v][E - 0xE800:E + 2] for v in V})

# maybe start is byte 0x317F5*1 (treat imm as byte addr) -> blk1+0x157F5
add("Rc_bytestart", {v: b1[v][0x317F5 - 0x1C000:E] for v in V})

# and the same window taken from the true linear image (identical content,
# but keeps the option of crossing below blk1)
def linear(v):
    buf = bytearray(b"\xff" * 0x80000)
    buf[0:0x9800] = b0[v]
    if v in C218:
        buf[0x9800:0x1C000] = C218[v]
    buf[0x1C000:0x80000] = b1[v]
    return bytes(buf)


LIN = {v: linear(v) for v in V}
add("L_exact",  {v: LIN[v][0x62FEA:0x7FFEA] for v in V})
add("L_incl_A", {v: LIN[v][0x62FEA:0x7FFEC] for v in V})

TGT = {v: b1[v][A_OFF:A_OFF + 2] for v in V}
with open("msgsets_region.bin", "wb") as f:
    f.write(struct.pack("<I", len(sets)))
    for name, items in sets:
        nm = name.encode()
        f.write(struct.pack("<BII", len(nm), len(items), len(items[0][1])))
        f.write(nm)
        for v, msg in items:
            f.write(TGT[v])
            f.write(msg)
print(f"wrote msgsets_region.bin: {len(sets)} sets")
for name, items in sets:
    print(f"  {name:14s} nver={len(items)} len=0x{len(items[0][1]):X}")
