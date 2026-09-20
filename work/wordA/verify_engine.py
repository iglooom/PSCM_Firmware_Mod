#!/usr/bin/env python3
"""Operand direction for the two-register forms, and what it implies.

DIRECTION, settled from the manual's own field naming
-----------------------------------------------------
`LSRR.W EEE,FFF` is the Rosetta stone. Its assembler syntax is src,dst
(ERM: "S,D"), and its grid is

    LSRR.W EEE,FFF     0111 11FF Faaa 1001

The uppercase letter that also appears in the operand list (`FFF`) sits at
bits 9-7, and `FFF` is the SECOND operand = the DESTINATION. The lowercase
`aaa` field at bits 6-4 must therefore be the remaining operand = the SOURCE.
Same shape for `ZXT.B FFF,FFF` (`0111 11FF Fbbb 0010`) and
`EOR.W EEE,EEE` (`0111 10EE Eaaa 1010`).

  => bits 9-7 = DESTINATION, bits 6-4 = SOURCE.

Applying that to the loop body (Y1 = crc, Y0 = scratch):

  856A  MOVE.W P:(R2)+,Y0      ; Y0 = program word
  7AFA  EOR.W  Y1,Y0           ; dst=Y0(5) src=Y1(7)  -> Y0 = crc ^ word
  7ED2  ZXT.B  Y0,Y0           ; Y0 = (crc ^ word) & 0xFF   = table index
  8905/8921                     ; R1 = R0(table base) + index
  5FA8  LSRR.W #8,Y1           ; Y1 = crc >> 8
  F501  MOVE.W X:(R1)+,Y0      ; Y0 = tbl[index]
  7BDA  EOR.W  Y0,Y1           ; dst=Y1(7) src=Y0(5)  -> crc = (crc>>8) ^ tbl[i]

which is *exactly* `crc = (crc >> 8) ^ tbl[(crc ^ byte) & 0xFF]`, and the byte
consumed is the LOW byte of the program word. The second unrolled half repeats
it through accumulator A for the high byte.

EQUIVALENCE PROOF (retires a whole hypothesis family)
-----------------------------------------------------
The code XORs the FULL 16-bit word into the crc once, then does two table
steps. That looks like a distinct "word-wise" algorithm, but it is provably
identical to feeding the low byte then the high byte through the ordinary
byte-wise core. Proof below, checked numerically on real data.

Consequence: there is NO separate word-wise variant left to try - and since
`lo_then_hi` over the region already failed for all 65536 seeds, the ENGINE and
BYTE ORDER are not the problem. The remaining possibility is the REGION.
"""
import glob

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
b = {v: [open(x, "rb").read() for x in sorted(glob.glob(f"{BASE}/{v}/*.bin"))]
     for v in V}


def rev16(x):
    return int(f"{x:016b}"[::-1], 2)


RP = rev16(0x1021)
T = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ RP if c & 1 else c >> 1
    T.append(c)


def crc_bytes(data, init=0xFFFF):
    """Ordinary reflected byte-wise core."""
    c = init
    for x in data:
        c = (c >> 8) ^ T[(c ^ x) & 0xFF]
    return c


def crc_words_asm(words, init=0xFFFF):
    """Exactly what P:$16216 does: XOR the whole 16-bit word, then 2 steps."""
    c = init
    for wd in words:
        c ^= wd                                  # 7AFA on the full word
        for _ in range(2):
            idx = c & 0xFF                       # 7ED2 ZXT.B
            c = (c >> 8) ^ T[idx]                # 5FA8 + F501 + 7BDA
    return c


print("=" * 74)
print("1. ENGINE CONTROL - the X-space routine's output is a KNOWN value")
print("   (blk2 word A, solved in §5: reflected CCITT, init FFFF, bytes)")
for v in V:
    got = crc_bytes(b[v][2][:0x73FA], 0xFFFF)
    want = int.from_bytes(b[v][2][0x73FA:0x73FC], "little")
    print(f"   {v}: calc {got:04X} stored {want:04X} "
          f"{'PASS' if got == want else 'FAIL'}")
    assert got == want

print("=" * 74)
print("2. EQUIVALENCE: asm word-form == byte-wise lo-then-hi")
seg = b[V[0]][1][0x1000:0x1000 + 4096]
words = [int.from_bytes(seg[i:i + 2], "little") for i in range(0, len(seg), 2)]
for init in (0x0000, 0xFFFF, 0x1234):
    a = crc_words_asm(words, init)
    c = crc_bytes(seg, init)            # seg is already lo,hi,lo,hi...
    print(f"   init {init:04X}: asm-word {a:04X}  bytewise-lohi {c:04X}  "
          f"{'IDENTICAL' if a == c else 'DIFFER'}")
    assert a == c
print("   => the 'word-wise CRC' hypothesis is retired: it IS lo-then-hi.")

print("=" * 74)
print("3. The region from the call arguments, run through that exact engine")
A_OFF = 0x63FEA
for v in V:
    seg = b[v][1][0x1000:A_OFF]
    wl = [int.from_bytes(seg[i:i + 2], "little") for i in range(0, len(seg), 2)]
    got = crc_words_asm(wl, 0xFFFF)
    want = int.from_bytes(b[v][1][A_OFF:A_OFF + 2], "little")
    print(f"   {v}: calc {got:04X} stored {want:04X} "
          f"{'PASS' if got == want else 'MISMATCH'}")
print()
print("Engine verified, operand direction verified, byte order verified,")
print("all 65536 seeds already swept -> the REGION is the remaining unknown.")
