#!/usr/bin/env python3
"""CONTROL set: the already-solved 14C217 blk2 word A (CRC-16/MCRF4XX over
blk2[0..0x73FA), stored LE at 0x73FA). The sweeper MUST recover
poly=0x1021 var=byte_lsb store=LE. If it does not, the harness is broken and
no negative result from it means anything."""
import glob
import struct

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b2 = {}
for v in V:
    fs = sorted(glob.glob(f"{BASE}/{v}/*.bin"))
    b2[v] = open(fs[2], "rb").read()

A = 0x73FA
sets = [("ctl_blk2_toA", [(v, b2[v][:A]) for v in V]),
        ("ctl_blk2_full", [(v, b2[v]) for v in V])]

with open("msgsets_ctl.bin", "wb") as f:
    f.write(struct.pack("<I", len(sets)))
    for name, items in sets:
        nm = name.encode()
        f.write(struct.pack("<BII", len(nm), len(items), len(items[0][1])))
        f.write(nm)
        for v, msg in items:
            f.write(b2[v][A:A + 2])   # stored word A of blk2
            f.write(msg)
print("control written; expect poly=0x1021 var=byte_lsb store=LE on ctl_blk2_toA")
print({v: b2[v][A:A + 2].hex() for v in V})
