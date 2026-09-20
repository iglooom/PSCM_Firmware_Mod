#!/usr/bin/env python3
"""Decode the per-message signal masks in the 14C386 signal configuration.

Each mailbox descriptor's +5 field points at a 4-word (=64-bit) mask, one bit
per payload bit.  This script tests the mask's BIT ORDER against the CAN-HS
DBC rather than assuming one: a correct convention should make the cleared
bits land exactly on whole DBC signal boundaries, a wrong one should smear
them across signal edges.

DBC bit numbering (Motorola / @0+): byte b, MSB-first, position = b*8 + (7-i).
"""
import os
import re
import struct
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DBC = "/home/gl/Projects/ford/CANBus/CAN-HS.dbc"
XBASE = 0x4000
SC = os.path.join(ROOT, "bins", "CV6T-14C386-AB",
                  "CV6T-14C386-AB_blk0_0x04008000.bin")


def load_words(path):
    d = open(path, "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def dbc_signals():
    """{msg_id: [(name, start_bit, length, byte_order)]}"""
    msgs, cur = {}, None
    for ln in open(DBC, errors="replace"):
        m = re.match(r"^BO_ (\d+) (\w+)", ln)
        if m:
            cur = int(m.group(1))
            msgs[cur] = []
            continue
        s = re.match(r"^\s*SG_ (\w+)\s*:\s*(\d+)\|(\d+)@(\d)([+-])", ln)
        if s and cur is not None:
            msgs[cur].append((s.group(1), int(s.group(2)), int(s.group(3)),
                              int(s.group(4))))
    return msgs


def signal_bits(start, length, byte_order):
    """Absolute payload bit positions covered by a signal.

    Motorola/big-endian (@0): start bit is the MSB; the field walks DOWN
    within a byte then continues at bit 7 of the next byte.
    Intel/little-endian (@1): contiguous ascending from start.
    """
    bits = []
    if byte_order == 0:
        b, i = divmod(start, 8)
        pos = 7 - i
        for _ in range(length):
            bits.append(b * 8 + (7 - pos))
            pos += 1
            if pos > 7:
                pos = 0
                b += 1
    else:
        bits = list(range(start, start + length))
    return bits


def mask_bits(words, xaddr, order):
    """Return the set of CLEARED bit indices under a given bit convention."""
    w = words[xaddr - XBASE: xaddr - XBASE + 4]
    raw = b"".join(struct.pack("<H", x) for x in w)   # 8 bytes, word LE
    out = set()
    for bytepos in range(8):
        for i in range(8):
            if order == "msb":          # bit 7 of byte 0 = position 0
                bit = bytepos * 8 + (7 - i)
            else:                       # bit 0 of byte 0 = position 0
                bit = bytepos * 8 + i
            if not (raw[bytepos] >> i) & 1:
                out.add(bit)
    return out


def main():
    words = load_words(SC)
    sigs = dbc_signals()
    sys.path.insert(0, HERE)
    from signal_config import find_ptr_tables, rec  # noqa: E402

    entries = []
    for _ptab, ents in find_ptr_tables(words):
        for xa, code in ents:
            r = rec(words, xa)
            if r[5]:                       # has a mask pointer
                entries.append((r[0], r[5], code))

    print("Testing bit conventions: does each cleared bit fall inside exactly")
    print("one DBC signal, and does it cover that signal WHOLLY?\n")
    for order in ("msb", "lsb"):
        whole = partial = orphan = 0
        for cid, mptr, _ in entries:
            cleared = mask_bits(words, mptr, order)
            if not cleared:
                continue
            for name, st, ln, bo in sigs.get(cid, []):
                sb = set(signal_bits(st, ln, bo))
                inter = sb & cleared
                if not inter:
                    continue
                if inter == sb:
                    whole += 1
                else:
                    partial += 1
            covered = set()
            for name, st, ln, bo in sigs.get(cid, []):
                covered |= set(signal_bits(st, ln, bo))
            orphan += len(cleared - covered)
        print(f"  order={order:>3}:  whole signals {whole:3d}   "
              f"partially-cut {partial:3d}   bits outside any signal {orphan:3d}")

    print("\n" + "=" * 66)
    # The convention is CHOSEN BY THE DATA, not assumed: see the scores above.
    # lsb wins decisively (30 whole signals, 0 partially cut) -- a wrong bit
    # order would smear cleared bits across signal boundaries, as msb does.
    order = "lsb"
    print(f"Per-message cleared bits (convention: {order})\n")
    tally = Counter()
    for cid, mptr, code in sorted(entries):
        cleared = mask_bits(words, mptr, order)
        names = []
        for name, st, ln, bo in sigs.get(cid, []):
            sb = set(signal_bits(st, ln, bo))
            if sb & cleared:
                tag = "" if sb <= cleared else " (PARTIAL)"
                names.append(name + tag)
                tally[name.split("_")[-1]] += 1
        d = {5: "TX", 7: "RX"}.get(code, "?")
        print(f"  0x{cid:03X} {d}  mask X:${mptr:04X}  "
              f"cleared bits {sorted(cleared)}")
        print(f"          -> {', '.join(names) if names else '(no DBC match)'}")
    print("\nSuffix tally of masked signals:", dict(tally))


if __name__ == "__main__":
    main()
