#!/usr/bin/env python3
"""Single-set inputs for rangesweep.

 - msgsets_b1.bin  : full blk1, 3 versions, target = stored word A @0x63FEA.
 - msgsets_rsctl.bin : CONTROL = full blk2, 3 versions, target = blk2 word A
                       @0x73FA.  rangesweep MUST report st=0x0 end=0x73FA
                       core=lsb store=LE tr=plain, else the tool is broken.
"""
import glob
import struct
import sys

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))] for v in V}


def emit(path, name, blkidx, tgt_off):
    with open(path, "wb") as f:
        f.write(struct.pack("<I", 1))
        nm = name.encode()
        msgs = [(v, b[v][blkidx]) for v in V]
        f.write(struct.pack("<BII", len(nm), len(msgs), len(msgs[0][1])))
        f.write(nm)
        for v, m in msgs:
            f.write(b[v][blkidx][tgt_off:tgt_off + 2])
            f.write(m)
    print(f"{path}: {name} len=0x{len(msgs[0][1]):X} "
          f"tgt@0x{tgt_off:X} = " +
          " ".join(b[v][blkidx][tgt_off:tgt_off + 2].hex() for v in V))


emit("msgsets_b1.bin", "blk1_full", 1, 0x63FEA)
emit("msgsets_rsctl.bin", "blk2_full", 2, 0x73FA)
