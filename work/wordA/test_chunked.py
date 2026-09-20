#!/usr/bin/env python3
"""Chunked-accumulation hypothesis for 14C217 blk1 word A.

Why: the computing function (blk1+0x81AA) is a resumable state machine, not a
one-shot pass:
    MOVE.L X:$08F3,C.L      ; RELOAD a saved 32-bit word POINTER
    CMP.L  #$4C00,...       ; chunk size 0x4C00 words
    MOVE.W X:$11EF,Y1       ; RELOAD the running CRC
    JSR    P:$16216         ; CRC one chunk
    MOVE.W Y0,X:$11EF       ; save running CRC
    MOVE.L A10.L,X:$08F3    ; save advanced pointer
so the region is processed 0x4C00 words at a time across many invocations.

If chunks simply chain, the result equals a single linear pass - already
excluded for all 65536 seeds. So test the combinations that are NOT equivalent
to one pass: per-chunk restart with XOR / ADD / chained-mix accumulation.

Sibling evidence that the engine is right: the same function also drives an
X-space CRC (JSR P:$1627C) with its own accumulator X:$11EA over data flash
starting at X-byte 0x8C00 = blk2's load address - and blk2's word A is already
known to be reflected CCITT init 0xFFFF (§5). Engine confirmed; only the blk1
feed is open.
"""
import glob

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))]
     for v in V}
b1 = {v: b[v][1] for v in V}
A_OFF = 0x63FEA
TL = {v: int.from_bytes(b1[v][A_OFF:A_OFF + 2], "little") for v in V}
TB = {v: int.from_bytes(b1[v][A_OFF:A_OFF + 2], "big") for v in V}


def rev16(x):
    return int(f"{x:016b}"[::-1], 2)


RP = rev16(0x1021)
T = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ RP if c & 1 else c >> 1
    T.append(c)


def crc(data, init):
    c = init
    for x in data:
        c = (c >> 8) ^ T[(c ^ x) & 0xFF]
    return c


# ---- sanity: the engine must reproduce blk2's KNOWN word A ---------------
for v in V:
    got = crc(b[v][2][:0x73FA], 0xFFFF)
    want = int.from_bytes(b[v][2][0x73FA:0x73FC], "little")
    assert got == want, (v, hex(got), hex(want))
print("ENGINE CONTROL: reflected CCITT init FFFF reproduces blk2 word A "
      "on all 3 versions  PASS")

S, E = 0x1000, A_OFF
CHUNK_WORDS = 0x4C00
print(f"region blk1[0x{S:X}..0x{E:X}) = {(E-S)//2} words, "
      f"chunk {CHUNK_WORDS} words -> "
      f"{-(-((E-S)//2) // CHUNK_WORDS)} chunks")

ORDERS = {
    "lohi": lambda d: list(d),
    "hilo": lambda d: [x for i in range(0, len(d) - 1, 2) for x in (d[i + 1], d[i])],
}

found = []
for oname, gen in ORDERS.items():
    for cw in (CHUNK_WORDS, 0x4C00 // 2, 0x9800):
        cb = cw * 2                       # chunk size in bytes
        for init in (0xFFFF, 0x0000):
            for rule in ("xor", "add", "sub"):
                acc = {}
                for v in V:
                    seq = gen(b1[v][S:E])
                    a = 0xFFFF if rule == "sub" else 0
                    if rule == "xor":
                        a = 0
                    for off in range(0, len(seq), cb):
                        c = crc(seq[off:off + cb], init)
                        if rule == "xor":
                            a ^= c
                        elif rule == "add":
                            a = (a + c) & 0xFFFF
                        else:
                            a = (a - c) & 0xFFFF
                    acc[v] = a
                for lbl, TT in (("LE", TL), ("BE", TB)):
                    if acc == TT:
                        found.append((oname, hex(cw), hex(init), rule, lbl))
                        print("*** MATCH", found[-1], flush=True)
                    ax = {v: acc[v] ^ 0xFFFF for v in V}
                    if ax == TT:
                        found.append((oname, hex(cw), hex(init), rule, lbl + "+xor"))
                        print("*** MATCH", found[-1], flush=True)
                print(f"  {oname} chunk=0x{cw:X} init=0x{init:04X} {rule}: "
                      + " ".join(f"{acc[v]:04X}" for v in V))

print("\ntarget:", {v: f"{TL[v]:04X}" for v in V})
print("matches:", found)
