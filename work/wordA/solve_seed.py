#!/usr/bin/env python3
"""Per-build-seed hypothesis for 14C217 blk1 word A.

Everything so far excludes a CONSTANT init:
  * all 65536 polys x 4 transforms x {msb,lsb} x {LE,BE} over the
    disassembly-derived region blk1[0x1000..0x63FEA)  -> 0 hits
  * exhaustive (start,end) range sweep with poly 0x1021 over blk1, and NO hit
    ends at 0x63FEA (word A's own offset)            -> all coincidences
  * no CRC table exists in blk0/blk1 at all; the only table in the corpus is
    the REFLECTED CCITT (0x1021) table in blk2.

So test hypothesis 2: the init/seed is PER BUILD.  For a CRC the map
init -> crc is affine and invertible, so for a fixed (range, poly, core) we can
RECOVER the init each version would need.  If those recovered inits are
meaningful (timestamp, part number, a neighbouring stored word, a constant
offset from each other) that identifies the scheme.

Recovering init for a reflected (LSB-first) CRC:
    crc(m, init) = crc(m, 0) ^ L(init)
  where L is linear.  Build L's 16x16 matrix by running the zero-message-length
  basis, then solve L(init) = target ^ crc(m,0) by Gaussian elimination.
"""
import glob

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))] for v in V}
b1 = {v: b[v][1] for v in V}
A_OFF = 0x63FEA


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


def crc(d, init, t, lsb):
    c = init
    if lsb:
        for x in d:
            c = (c >> 8) ^ t[(c ^ x) & 0xFF]
    else:
        for x in d:
            c = ((c << 8) & 0xFFFF) ^ t[((c >> 8) ^ x) & 0xFF]
    return c


def solve_init(msg, target, t, lsb):
    """Return init such that crc(msg, init) == target, or None."""
    base = crc(msg, 0, t, lsb)
    # columns of L
    cols = [crc(msg, 1 << i, t, lsb) ^ base for i in range(16)]
    want = target ^ base
    # gaussian elimination over GF(2): find x with sum x_i * cols[i] == want
    rows = []           # (mask_of_inits, value)
    piv = {}
    for i, c in enumerate(cols):
        vec, sel = c, 1 << i
        for bit in sorted(piv, reverse=True):
            if vec >> bit & 1:
                vec ^= piv[bit][0]
                sel ^= piv[bit][1]
        if vec:
            hb = vec.bit_length() - 1
            piv[hb] = (vec, sel)
    vec, sel = want, 0
    for bit in sorted(piv, reverse=True):
        if vec >> bit & 1:
            vec ^= piv[bit][0]
            sel ^= piv[bit][1]
    if vec:
        return None
    assert crc(msg, sel, t, lsb) == target
    return sel


RANGES = {
    "blk1[0x1000:A]": (0x1000, A_OFF),
    "blk1[0:A]": (0x0, A_OFF),
    "blk1[0x1000:A+2]": (0x1000, A_OFF + 2),
}

print("stored word A:", {v: b1[v][A_OFF:A_OFF + 2].hex() for v in V})
print("timestamp    :", {v: b1[v][0x63FE4:0x63FEA].hex() for v in V})
print("word B       :", {v: b1[v][A_OFF + 2:A_OFF + 4].hex() for v in V})
print("blk2 word A  :", {v: b[v][2][0x73FA:0x73FC].hex() for v in V})
print()

for poly, pname in ((0x1021, "CCITT"),):
    for lsb in (1, 0):
        t = mk(poly, lsb)
        core = "lsb/reflected" if lsb else "msb/forward"
        for rname, (s, e) in RANGES.items():
            for store in ("little", "big"):
                got = {}
                for v in V:
                    tgt = int.from_bytes(b1[v][A_OFF:A_OFF + 2], store)
                    got[v] = solve_init(b1[v][s:e], tgt, t, lsb)
                print(f"poly={pname} core={core:14s} range={rname:18s} store={store}")
                for v in V:
                    iv = got[v]
                    print(f"    {v}: init=0x{iv:04X}" if iv is not None else f"    {v}: none")
                vals = [got[v] for v in V]
                if len(set(vals)) == 1:
                    print("    *** CONSTANT INIT ACROSS ALL VERSIONS ***")
                print()
