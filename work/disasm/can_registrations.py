#!/usr/bin/env python3
"""Enumerate CAN message registrations in the PSCM image.

Pattern observed at P:$1B5C4.. (the TX registration block):
    moveu.w #$0E60,R0      ; 0x0E60 = 0x398 << 2  -- FlexCAN ID_HIGH layout
    move.w  #$000B,...     ; slot / index
    jsr     P:$1C14E       ; registration helper

So a registration site is `moveu.w #<ID<<2>,<AGU reg>` followed by a JSR
within a short window.  This finds every such site and names the ID from the
HS DBC.
"""
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from flow56800e import Image  # noqa: E402

DBC = "/home/gl/Projects/ford/CANBus/CAN-HS.dbc"


def dbc_ids():
    ids = {}
    if not os.path.exists(DBC):
        return ids
    for ln in open(DBC, errors="replace"):
        m = re.match(r"^BO_ (\d+) (\w+)", ln)
        if m and int(m.group(1)) < 0x800:
            ids[int(m.group(1))] = m.group(2)
    return ids


def find_registrations(img, window=10):
    found = collections.defaultdict(list)
    for wa, w in img.iter_words():
        if (w & 0xFFF0) != 0x8740:        # moveu.w #xxxx,<AGU reg>
            continue
        v = img.word(wa + 1)
        if (v & 3) or not (0 < (v >> 2) < 0x800):
            continue
        tgt = None
        for k in range(2, window):
            x = img.word(wa + k)
            if (x & 0xFFF4) == 0xE254:    # JSR <ABS19>
                a = (((x >> 3) & 1) << 18) | (((x >> 1) & 1) << 17) \
                    | ((x & 1) << 16)
                tgt = a | img.word(wa + k + 1)
                break
        if tgt is not None:
            found[v >> 2].append((wa, tgt))
    return found


def main():
    img = Image()
    names = dbc_ids()
    found = find_registrations(img)
    print(f'{"ID":>6}  {"DBC name":<28}{"sites":>6}  callees')
    for cid in sorted(found):
        sites = found[cid]
        callees = sorted({t for _, t in sites})
        print(f" 0x{cid:03X}  {names.get(cid, '-'):<28}{len(sites):>6}  "
              + ", ".join(f"P:${t:05X}" for t in callees[:5]))
    print()
    for probe in (0xA5,):
        print(f"0x{probe:02X} registered: {probe in found}")


if __name__ == "__main__":
    main()
