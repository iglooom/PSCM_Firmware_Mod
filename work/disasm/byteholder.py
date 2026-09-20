"""Which payload-byte holder does each 0x0A5 extraction record read?

Helper P:$31122 does:  MOVE.W X:(R2),R0 ; MOVEU.BP X:(R0) ; AND mask ; ASRR shift
so the record's +3 word is a POINTER to the payload-byte cell, and mask/shift
select the field within that byte.  Grouping records by their +3 pointer
therefore recovers which payload BYTE each holder corresponds to -- which is
what disambiguates LdwActvStats (byte 1) from LkaActvStats (byte 3), since
both have the identical spec 0x7004.
"""
import os
import struct
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
from signal_masks import dbc_signals   # noqa: E402

CFG = {"CV6T": "CV6T-14C386-AB/CV6T-14C386-AB_blk0_0x04008000.bin",
       "BV6T": "BV6T-14C386-AA/BV6T-14C386-AA_blk0_0x04008000.bin"}


def field(st, ln):
    b, msb = st // 8, st % 8
    sh = msb - ln + 1
    return None if sh < 0 else (b, ((1 << ln) - 1) << sh, sh)


for lbl, rel in CFG.items():
    d = open(os.path.join(ROOT, "bins", rel), "rb").read()
    w = struct.unpack("<%dH" % (len(d) // 2), d)
    sigs = dbc_signals()
    a5 = [(nm, *field(st, ln)) for nm, st, ln, bo in sigs.get(0x0A5, [])
          if field(st, ln)]

    byptr = defaultdict(list)
    for i in range(0, 0x1E0 - 5, 5):
        mask, sh = w[i + 1] >> 8, w[i + 1] & 0xFF
        slot = w[i + 2] & 0xFF
        if not mask or sh > 7 or not slot or (slot & (slot - 1)):
            continue
        byptr[w[i + 3]].append((0x4000 + i, mask, sh))

    print(f"===== {lbl}: records grouped by payload-byte holder pointer")
    for ptr in sorted(byptr):
        recs = byptr[ptr]
        # which 0x0A5 payload byte is consistent with ALL specs on this holder?
        cands = set(range(8))
        for _at, mask, sh in recs:
            ok = {b for nm, b, m, s in a5 if m == mask and s == sh}
            cands &= ok
        if not cands:
            continue
        for _at, mask, sh in recs:
            names = [nm for nm, b, m, s in a5
                     if m == mask and s == sh and b in cands]
            if names:
                print(f"   X:${ptr:04X} (byte {sorted(cands)})  rec@{_at:04X} "
                      f"spec {mask:02X}>>{sh}  -> {'/'.join(names)}")
    print()
