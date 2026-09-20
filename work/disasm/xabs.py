"""Scan raw P-space for ANY word-pair that could write/read X:$<addr>.

Reports the preceding opcode word so a bare value match can be told apart
from a real absolute reference (the brief's false-positive rule).
"""
import struct
import sys

ROOT = "/home/gl/Projects/ford/PSCM/Research/bins/"
B = {
    "CV6T-AR": [("CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin", 0),
                ("CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin", 0x1C000),
                ("CV6T-14C217-AR/CV6T-14C217-AR_blk2_0x04008C00.bin", 0x4008C00)],
    "CV6T-AH": [("CV6T-14C217-AH/CV6T-14C217-AH_blk0_0x00000000.bin", 0),
                ("CV6T-14C217-AH/CV6T-14C217-AH_blk1_0x0001C000.bin", 0x1C000),
                ("CV6T-14C217-AH/CV6T-14C217-AH_blk2_0x04008C00.bin", 0x4008C00)],
    "BV6T-AF": [("BV6T-14C217-AF/BV6T-14C217-AF_blk0_0x00000000.bin", 0),
                ("BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin", 0x1C000),
                ("BV6T-14C217-AF/BV6T-14C217-AF_blk2_0x04008C00.bin", 0x4008C00)],
}

# opcode word -> meaning, for the 2-word absolute forms (verified via encodings.json)
OPS = {
    0xF07C: "MOVE.W  X:$%04X,A     (READ)",
    0xF17C: "MOVE.W  X:$%04X,B     (READ)",
    0xF27C: "MOVE.W  X:$%04X,C     (READ)",
    0xF37C: "MOVE.W  X:$%04X,D     (READ)",
    0xF57C: "MOVE.W  X:$%04X,Y0    (READ)",
    0xF77C: "MOVE.W  X:$%04X,Y1    (READ)",
    0xFF7C: "TST.W   X:$%04X       (READ)",
    0xD07C: "MOVE.W  A1,X:$%04X    (WRITE)",
    0xD17C: "MOVE.W  B1,X:$%04X    (WRITE)",
    0xD27C: "MOVE.W  C1,X:$%04X    (WRITE)",
    0xD37C: "MOVE.W  D1,X:$%04X    (WRITE)",
    0xD57C: "MOVE.W  Y0,X:$%04X    (WRITE)",
    0x4C44: "CMP.W   X:$%04X,A     (READ)",
    0x4CC4: "CMP.W   X:$%04X,B     (READ)",
    0x4EC4: "INC.W   X:$%04X       (RMW)",
    0x4E44: "DEC.W   X:$%04X       (RMW)",
    0xDF7C: "CLR.W   X:$%04X       (WRITE)",
}
# MOVE.W #<-64,63>,X:xxxx  ->  1110 0110 1BBBBBBB, imm = low 7 bits
IMM_MASK = 0xFF80
IMM_VAL = 0xE680


def words(build):
    out = []
    for fn, ba in B[build]:
        d = open(ROOT + fn, "rb").read()
        n = len(d) // 2
        out.append((ba // 2, list(struct.unpack("<%dH" % n, d[:n * 2]))))
    return out


def scan(build, addr):
    print(f"--- {build}  X:${addr:04X} ---")
    hits = 0
    bare = 0
    for base, w in words(build):
        for i in range(len(w) - 1):
            if w[i + 1] != addr:
                continue
            bare += 1
            op = w[i]
            if op in OPS:
                print(f"   P:${base+i:05X}  {op:04X} {addr:04X}   "
                      + OPS[op] % addr)
                hits += 1
            elif (op & IMM_MASK) == IMM_VAL:
                imm = op & 0x7F
                if imm & 0x40:
                    imm -= 0x80
                print(f"   P:${base+i:05X}  {op:04X} {addr:04X}   "
                      f"MOVE.W  #{imm},X:${addr:04X}  (WRITE const {imm})")
                hits += 1
    print(f"   => {hits} real reference(s); {bare} bare value matches")
    return hits


if __name__ == "__main__":
    a = int(sys.argv[1], 0)
    for b in sys.argv[2:] or list(B):
        scan(b, a)
