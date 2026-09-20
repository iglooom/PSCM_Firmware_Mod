#!/usr/bin/env python3
"""Look for CRC lookup tables in blk0 / blk1 (prior work only searched blk2).

A 256-entry 16-bit CRC table for polynomial P satisfies, for a table built
MSB-first:      t[i] = crc_of_byte(i)  and crucially
                t[i] ^ t[j] == t[i^j]  only for the linear part, so instead we
directly RECONSTRUCT the table for every candidate poly and memcmp.

Sweeps all 65536 polys x {msb,lsb} x {LE,BE} entry order against every 512-byte
window in every image. Fast: builds the table once per poly and uses a dict of
the first 8 bytes as an index into the haystack.
"""
import glob
from collections import defaultdict


def rev16(v):
    return int(f"{v:016b}"[::-1], 2)


def mk(poly, lsb):
    t = []
    rp = rev16(poly)
    for i in range(256):
        if lsb:
            c = i
            for _ in range(8):
                c = (c >> 1) ^ rp if c & 1 else c >> 1
        else:
            c = i << 8
            for _ in range(8):
                c = ((c << 1) ^ poly) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
        t.append(c)
    return t


BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
IMGS = {}
for p in sorted(glob.glob(f"{BASE}/*/*.bin")):
    tag = p.split("/")[-2] + "/" + p.split("/")[-1].split("_")[1]
    IMGS[tag] = open(p, "rb").read()

# index every image by 8-byte key at even offsets
idx = defaultdict(list)
for tag, d in IMGS.items():
    for o in range(0, len(d) - 8, 2):
        idx[d[o:o + 8]].append((tag, o))

print(f"indexed {len(idx)} keys")
hits = []
for poly in range(1, 65536):
    for lsb in (0, 1):
        t = mk(poly, lsb)
        for end in ("little", "big"):
            blob = b"".join(x.to_bytes(2, end) for x in t)
            key = blob[:8]
            for tag, o in idx.get(key, ()):
                d = IMGS[tag]
                if d[o:o + 512] == blob:
                    hits.append((tag, hex(o), hex(poly), "lsb" if lsb else "msb", end))
                    print("TABLE", hits[-1], flush=True)
print("total tables:", len(hits))
