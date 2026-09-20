"""Is X:$0904 a calibration cell? Classify read/write for the low X region."""
import struct
from collections import Counter

ROOT = "/home/gl/Projects/ford/PSCM/Research/bins/"
BLK = [("CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin", 0),
       ("CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin", 0x1C000),
       ("CV6T-14C217-AR/CV6T-14C217-AR_blk2_0x04008C00.bin", 0x4008C00)]

READ = {0xF07C, 0xF17C, 0xF27C, 0xF37C, 0xF57C, 0xF77C, 0xFF7C,
        0x4C44, 0x4CC4, 0xF87C, 0xF97C}
WRITE = {0xD07C, 0xD17C, 0xD27C, 0xD37C, 0xD57C, 0xDF7C, 0x4EC4, 0x4E44}

w = []
for fn, ba in BLK:
    d = open(ROOT + fn, "rb").read()
    n = len(d) // 2
    w.append((ba // 2, list(struct.unpack("<%dH" % n, d[:n * 2]))))

r, wr = Counter(), Counter()
for base, ws in w:
    for i in range(len(ws) - 1):
        a = ws[i + 1]
        if ws[i] in READ:
            r[a] += 1
        elif ws[i] in WRITE or (ws[i] & 0xFF80) == 0xE680:
            wr[a] += 1

lo = [a for a in set(r) | set(wr) if a < 0x1000]
ro = sorted(a for a in lo if r[a] and not wr[a])
rw = sorted(a for a in lo if wr[a])
print(f"X-space below $1000: {len(lo)} cells referenced")
print(f"  READ-ONLY  : {len(ro)}")
print(f"  ever WRITTEN: {len(rw)}")
print(f"\nX:$0904 -> reads={r[0x0904]} writes={wr[0x0904]}  "
      f"{'READ-ONLY (constant/calibration)' if not wr[0x0904] else 'WRITTEN'}")
print("\nneighbours of $0904 (read-only cells $08E0..$0930):")
for a in range(0x08E0, 0x0931):
    if r[a] or wr[a]:
        print(f"   X:${a:04X}  r={r[a]} w={wr[a]}")
