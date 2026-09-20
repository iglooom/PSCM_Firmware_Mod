#!/usr/bin/env python3
"""Find the 14C217 code that CONSUMES the lane-assist signals.

The 14C386 extraction table tells us the exact RAM word each 0x0A5 signal is
written to.  That address is the handle into the main firmware: whatever code
reads it is the lane-assist consumer.

Per build, 0x0A5 LkaActvStats_D_Req (spec 0x7004 = mask 0x70 >> 4) lands in:
    BV6T : X:$7EDF  and  X:$8628
    CV6T : X:$7ED7  and  X:$863C

This scans P-space for every instruction word whose FOLLOWING word is one of
those addresses (the absolute-addressing form on this core), so we can
disassemble the readers.
"""
import os
import struct
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

from signal_config import find_ptr_tables, rec       # noqa: E402
from signal_masks import dbc_signals                 # noqa: E402

XBASE = 0x4000

BUILDS = {
    "BV6T": {
        "cfg": "BV6T-14C386-AA/BV6T-14C386-AA_blk0_0x04008000.bin",
        "p": [("BV6T-14C217-AF/BV6T-14C217-AF_blk0_0x00000000.bin", 0x00000),
              ("BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin", 0x0E000)],
    },
    "CV6T": {
        "cfg": "CV6T-14C386-AB/CV6T-14C386-AB_blk0_0x04008000.bin",
        "p": [("CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin", 0x00000),
              ("CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin", 0x0E000)],
    },
}


def load(rel):
    d = open(os.path.join(ROOT, "bins", rel), "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def field(start, length):
    b, msb = start // 8, start % 8
    sh = msb - length + 1
    return None if sh < 0 else (b, ((1 << length) - 1) << sh, sh)


def lane_destinations(cfg_rel, msg=0x0A5):
    """-> {dest_addr: [signal names]} for every extraction record of `msg`."""
    w = load(cfg_rel)
    sigs = dbc_signals()
    byslot = defaultdict(list)
    for _p, ents in find_ptr_tables(w):
        for xa, code in ents:
            r = rec(w, xa)
            byslot[r[8]].append(r[0])
    out = defaultdict(set)
    for i in range(0, 0x1E0 - 5, 5):
        spec, bs = w[i + 1], w[i + 2]
        mask, sh, slot = spec >> 8, spec & 0xFF, bs & 0xFF
        if not mask or sh > 7 or not slot or (slot & (slot - 1)):
            continue
        for cid in byslot.get(slot, []):
            if cid != msg:
                continue
            for nm, st, ln, bo in sigs.get(cid, []):
                f = field(st, ln)
                if f and f[1] == mask and f[2] == sh:
                    out[w[i + 3]].add(nm)
                    out[w[i + 4]].add(nm + " (area)")
    return out


def scan_refs(pspec, addrs):
    """-> {addr: [(p_word_addr, opcode_word)]}

    A word equal to an address is NOT a reference: in 512 KB any 16-bit value
    occurs by chance ~8 times.  A real absolute reference requires the
    PRECEDING word to be an absolute-addressing opcode, so filter on the
    verified prefix set from flow56800e.ABS_PREFIX.
    """
    from flow56800e import ABS_PREFIX
    hits = defaultdict(list)
    raw = defaultdict(int)
    for rel, base in pspec:
        w = load(rel)
        for i in range(len(w) - 1):
            if w[i + 1] in addrs:
                raw[w[i + 1]] += 1
                if w[i] in ABS_PREFIX:
                    hits[w[i + 1]].append((base + i, w[i]))
    return hits, raw


def main():
    for label, b in BUILDS.items():
        dests = lane_destinations(b["cfg"])
        # only the concrete destination words, not the area bases
        addrs = {a for a, names in dests.items()
                 if any(not n.endswith("(area)") for n in names)}
        print(f"===== {label}: 0x0A5 destination RAM words")
        for a in sorted(addrs):
            names = sorted(n for n in dests[a] if not n.endswith("(area)"))
            print(f"   X:${a:04X}  <- {', '.join(names)}")
        hits, raw = scan_refs(b["p"], addrs)
        print(f"  -- P-space references (opcode-filtered) --")
        tot = 0
        for a in sorted(addrs):
            hs = hits.get(a, [])
            tot += len(hs)
            note = f"  [{raw.get(a, 0)} bare value matches, not references]"
            if hs:
                print(f"   X:${a:04X}: {len(hs)} real ref(s) "
                      + ", ".join(f"P:${p:05X}(op {o:04X})" for p, o in hs[:8]))
            elif raw.get(a):
                print(f"   X:${a:04X}: 0 real refs{note}")
        if not tot:
            print("   => NO absolute references: the lane-assist RAM cells are")
            print("      reached only through pointers/indexed addressing.")
        print()


if __name__ == "__main__":
    main()
