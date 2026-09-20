"""Find the startup ROM->X:RAM initialisation that supplies X:$0904.

X:$0904 is read-only in code, so its value must come from a startup copy.
Look for pointer-setup immediates that bracket $0904 and for copy loops.
"""
import struct

ROOT = "/home/gl/Projects/ford/PSCM/Research/bins/"
BLK = [("CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin", 0),
       ("CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin", 0x1C000),
       ("CV6T-14C217-AR/CV6T-14C217-AR_blk2_0x04008C00.bin", 0x4008C00)]

sp = []
for fn, ba in BLK:
    d = open(ROOT + fn, "rb").read()
    n = len(d) // 2
    sp.append((ba // 2, list(struct.unpack("<%dH" % n, d[:n * 2]))))

# MOVE.L #xxxx,Rn  = E40A..E40F family ; MOVE.W #xxxx,Rn = 8740|... ;
# MOVEU.W #xxxx,Rn = 87 4x
PTR = {0xE408, 0xE409, 0xE40A, 0xE40B, 0xE40C, 0xE40D,
       0x8750, 0x8752, 0x8754, 0x8756, 0x8758, 0x875A,
       0x8748, 0x874A, 0x874C, 0x874E}

print("immediates in $08C0..$0960 loaded into a pointer/GP register:")
for base, w in sp:
    for i in range(len(w) - 1):
        v = w[i + 1]
        if 0x08C0 <= v <= 0x0960 and w[i] in PTR:
            print(f"   P:${base+i:05X}  {w[i]:04X} {v:04X}   -> X:${v:04X}")

print("\nAny 32-bit long immediate whose low half is in that range:")
for base, w in sp:
    for i in range(len(w) - 2):
        if w[i] == 0xE410 or w[i] == 0xE411:
            lo, hi = w[i + 1], w[i + 2]
            for v in (lo, hi):
                if 0x08C0 <= v <= 0x0960:
                    print(f"   P:${base+i:05X}  {w[i]:04X} {lo:04X} {hi:04X}")
                    break
