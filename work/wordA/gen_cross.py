#!/usr/bin/env python3
"""Cross-block hypothesis: blk1 word A may checksum a DIFFERENT region than
blk1 itself - e.g. blk0 (0x9800) or blk2 (0x7400) or the 14C218 calibration.

Emits one msgset per candidate carrier, all with TARGET = blk1 word A
(@blk1+0x63FEA), for the exhaustive (start,end) rangesweep.
"""
import glob
import struct

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))] for v in V}
A_OFF = 0x63FEA
TGT = {v: b[v][1][A_OFF:A_OFF + 2] for v in V}

CANDS = {
    "blk0": {v: b[v][0] for v in V},
    "blk2": {v: b[v][2] for v in V},
    "blk0_blk2": {v: b[v][0] + b[v][2] for v in V},
    "blk2_blk0": {v: b[v][2] + b[v][0] for v in V},
}

for name, msgs in CANDS.items():
    assert len({len(x) for x in msgs.values()}) == 1, name
    path = f"msgsets_x_{name}.bin"
    with open(path, "wb") as f:
        f.write(struct.pack("<I", 1))
        nm = name.encode()
        f.write(struct.pack("<BII", len(nm), len(V), len(msgs[V[0]])))
        f.write(nm)
        for v in V:
            f.write(TGT[v])
            f.write(msgs[v])
    print(f"{path}  len=0x{len(msgs[V[0]]):X}")
print("target (blk1 word A):", {v: TGT[v].hex() for v in V})
