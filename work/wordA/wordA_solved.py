#!/usr/bin/env python3
"""SOLVED: 14C217 blk1 word A. Reference implementation + verification.

ALGORITHM (derived from the code, verified on all three OEM versions)
---------------------------------------------------------------------
    word_A = CRC-16/MCRF4XX( blk0 ++ blk1[START .. 0x63FEA) )
           = reflected CCITT, poly 0x1021, init 0xFFFF, xorout 0x0000
    stored little-endian at blk1+0x63FEA (flash 0x0007FFEA)

where the table is the one at blk2+0x20EC (X:$5676) and

    START = 2 * start_word - 0x1C000        <- READ FROM THE FIRMWARE

`start_word` is the immediate loaded by the self-check state machine; it is
**per build**, not a constant:

    BV6T-14C217-AF : 0x0000E800 -> byte 0x01D000 -> blk1+0x1000
    CV6T-14C217-AH : 0x0000E800 -> byte 0x01D000 -> blk1+0x1000
    CV6T-14C217-AR : 0x0000EC00 -> byte 0x01D800 -> blk1+0x1800   <-- differs!

Getting AR right *required* reading its own constant; assuming 0x1000 fails.
This script extracts the constant automatically, so it works on any version.

WHY EVERY EARLIER SWEEP MISSED IT
---------------------------------
The region is **blk0 ++ blk1-tail** -- a CONCATENATION of two non-adjacent
blocks that SKIPS the 14C218 calibration in between (flash 0x9800..0x1C000).
  * `rangesweep` swept every (start,end) of blk1 alone -> can't express it.
  * the linear-image tests used one contiguous span -> includes the gap.
  * the only sweep with blk0+blk1 compositions was killed at poly 64/65536,
    so poly 0x1021 was never reached there.
Design-wise this mirrors word B (§6): the calibration is deliberately excluded
so a recalibration does not invalidate the code checksum.
"""
import glob
import struct
import sys

BASE = "/home/gl/Projects/ford/PSCM/Research/bins"
A_OFF = 0x63FEA            # word A inside blk1
BLK1_BASE = 0x1C000        # blk1 load address (bytes)


def _mk_table():
    rp = int(f"{0x1021:016b}"[::-1], 2)
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ rp if c & 1 else c >> 1
        t.append(c)
    return t


_T = _mk_table()


def crc16_mcrf4xx(data, init=0xFFFF):
    c = init
    for x in data:
        c = (c >> 8) ^ _T[(c ^ x) & 0xFF]
    return c


def find_start_offset(blk1):
    """Recover the CRC region start from the firmware's own code.

    The self-check state machine loads, within a few words of each other:
        MOVE.L #$0003FFF5,reg     ; END   = word address of word A
        MOVE.L #$0000E800,reg     ; START = word address (per build)
    Encoding: E41n <lo16> <hi16>. We find the END constant, then take the
    nearest following long-immediate that is a plausible P-space start, and
    return it as a blk1-relative BYTE offset.
    """
    nw = len(blk1) // 2
    w = list(struct.unpack("<%dH" % nw, blk1[:nw * 2]))
    END_WORD = (BLK1_BASE + A_OFF) // 2          # 0x3FFF5

    def longimm(i):
        if (w[i] & 0xFFF0) != 0xE410:
            return None
        return (w[i + 2] << 16) | w[i + 1]

    ends = [i for i in range(nw - 2) if longimm(i) == END_WORD]
    for e in ends:
        for j in range(e, min(e + 40, nw - 2)):
            v = longimm(j)
            if v is None or v == END_WORD:
                continue
            byte = v * 2
            if BLK1_BASE < byte < BLK1_BASE + A_OFF:
                return byte - BLK1_BASE, v
    return None, None


def word_a(blk0, blk1, start_off=None):
    """Compute word A. start_off defaults to the firmware's own constant."""
    if start_off is None:
        start_off, _ = find_start_offset(blk1)
        if start_off is None:
            raise RuntimeError("could not recover START constant from blk1")
    return crc16_mcrf4xx(blk0 + blk1[start_off:A_OFF], 0xFFFF)


if __name__ == "__main__":
    V = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
    allok = True
    print("14C217 blk1 word A  =  CRC-16/MCRF4XX( blk0 ++ blk1[START:0x63FEA) )")
    print("                       init 0xFFFF, stored LE at blk1+0x63FEA\n")
    for v in V:
        f = sorted(glob.glob(f"{BASE}/{v}/*.bin"))
        if len(f) < 2:
            continue
        b0 = open(f[0], "rb").read()
        b1 = open(f[1], "rb").read()
        off, wordaddr = find_start_offset(b1)
        got = word_a(b0, b1, off)
        want = int.from_bytes(b1[A_OFF:A_OFF + 2], "little")
        ok = got == want
        allok = allok and ok
        print(f"  {v}")
        print(f"    START from code : #${wordaddr:08X} -> flash byte "
              f"0x{wordaddr*2:06X} = blk1+0x{off:X}")
        print(f"    computed        : {got:04X}")
        print(f"    stored          : {want:04X}   {'PASS' if ok else 'FAIL'}")
    print()
    print("ALL THREE VERSIONS PASS" if allok else "FAILURE")
    sys.exit(0 if allok else 1)
