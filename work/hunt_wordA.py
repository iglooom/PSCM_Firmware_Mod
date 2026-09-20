#!/usr/bin/env python3
"""Hunt the 14C217 blk1 word-A checksum, word-wise (MC56F8xxx is a 16-bit DSP)."""
import glob, sys

base = "/home/gl/Projects/ford/PSCM/Research/bins"
V217 = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {}
for v in V217:
    b[v] = [open(x, 'rb').read() for x in sorted(glob.glob(f"{base}/{v}/*.bin"))]
C218 = {'BV': open(f"{base}/BV6T-14C218-AF/BV6T-14C218-AF_blk0_0x00009800.bin", 'rb').read(),
        'CV': open(f"{base}/CV6T-14C218-AX/CV6T-14C218-AX_blk0_0x00009800.bin", 'rb').read()}
C386 = {'BV': open(f"{base}/BV6T-14C386-AA/BV6T-14C386-AA_blk0_0x04008000.bin", 'rb').read(),
        'CV': open(f"{base}/CV6T-14C386-AB/CV6T-14C386-AB_blk0_0x04008000.bin", 'rb').read()}

PAIR = {"BV6T-14C217-AF": 'BV', "CV6T-14C217-AR": 'CV'}
VV = list(PAIR.keys())


def linear(v):
    buf = bytearray(b'\xff' * 0x80000)
    b0, b1, _ = b[v]
    buf[0:0x9800] = b0
    buf[0x9800:0x1C000] = C218[PAIR[v]]
    buf[0x1C000:0x80000] = b1
    return bytes(buf)


IMG = {v: linear(v) for v in VV}
TA_le = {v: int.from_bytes(b[v][1][0x63FEA:0x63FEC], 'little') for v in VV}
TA_be = {v: int.from_bytes(b[v][1][0x63FEA:0x63FEC], 'big') for v in VV}


def words(d, end):
    return [int.from_bytes(d[i:i + 2], end) for i in range(0, len(d) // 2 * 2, 2)]


def crc16_wordwise(ws, poly, init, reflect_word=False):
    """Feed 16 bits at a time: crc ^= word; 16 shift steps."""
    c = init
    for w in ws:
        if reflect_word:
            w = int('{:016b}'.format(w)[::-1], 2)
        c ^= w
        for _ in range(16):
            c = ((c << 1) ^ poly) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def crc16_wordwise_lsb(ws, poly_rev, init):
    c = init
    for w in ws:
        c ^= w
        for _ in range(16):
            c = (c >> 1) ^ poly_rev if c & 1 else c >> 1
    return c


POLYS = [0x1021, 0x8005, 0x3D65, 0x8BB7, 0xC867, 0x0589, 0xA097, 0x1DCF, 0x5935]
INITS = [0x0000, 0xFFFF, 0x1D0F, 0xC6C6, 0xB2AA, 0x89EC, 0x800D]
RANGES = [(0, 0x7FFEA), (0, 0x7FFEC), (0, 0x80000), (0, 0x1C000), (0x1C000, 0x7FFEA),
          (0x9800, 0x7FFEA), (0, 0x9800), (0x9800, 0x1C000), (0x1C000, 0x80000)]

hits = []
for (s, e) in RANGES:
    for wend in ('little', 'big'):
        W = {v: words(IMG[v][s:e], wend) for v in VV}
        for p in POLYS:
            prev = int('{:016b}'.format(p)[::-1], 2)
            for init in INITS:
                for name, fn in (('msb', lambda ws, i=init, pp=p: crc16_wordwise(ws, pp, i)),
                                 ('msb_rw', lambda ws, i=init, pp=p: crc16_wordwise(ws, pp, i, True)),
                                 ('lsb', lambda ws, i=init, pr=prev: crc16_wordwise_lsb(ws, pr, i))):
                    vals = {v: fn(W[v]) for v in VV}
                    for xor in (0, 0xFFFF):
                        g = {v: vals[v] ^ xor for v in VV}
                        for lbl, T in (('le', TA_le), ('be', TA_be)):
                            if g == T:
                                hits.append((hex(s), hex(e), wend, hex(p), name, hex(init), hex(xor), lbl))
                                print("MATCH", hits[-1], flush=True)
    print("range done", hex(s), hex(e), flush=True)

print("TOTAL", hits)
