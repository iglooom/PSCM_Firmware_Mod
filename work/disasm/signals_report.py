#!/usr/bin/env python3
"""Signals the PSCM extracts from each CAN message -- CONSERVATIVE report.

Combines the two independent pieces of evidence in the 14C386 signal config
and states, per signal, how strong the evidence is.  Nothing is asserted that
the data does not support.

EVIDENCE A -- update-bit mask (descriptor word +5, table at X:$42B0)
    4 words = 64 bits, one per payload bit; a CLEARED bit is tracked.
    30/30 of the bits it clears are `_UB` signals, so it enumerates exactly
    the subscribed signals that HAVE an update bit.  Signals with no `_UB`
    companion are simply outside what this mask can express.

EVIDENCE B -- per-signal extraction table (stride-5 records below X:$41E0)
    +1 packed field spec: high byte = bit MASK, low byte = right SHIFT
    +2 high byte = selector, low byte = message SLOT bit
    +3/+4 destination RAM word / area
    Confirmed by geometry: LkaActvStats_D_Req is 30|3@0+ -> shift 4, mask
    0x70, and the firmware's spec word is literally 0x7004.

KNOWN LIMITS, stated rather than papered over:

 1. Slot bits are REUSED by the two descriptor groups, so a record's slot
    names two candidate messages.  A record is attributed only when exactly
    one candidate owns a signal of that geometry; otherwise it is AMBIGUOUS.
 2. The stride-5 framing does not explain the whole region: several runs of
    records match no candidate signal at all, so the table very likely holds
    more than one record shape.  Those records are counted as UNPARSED and
    deliberately excluded, which means the extraction lists here are a LOWER
    BOUND on what the module unpacks.
 3. Word +2's high byte is NOT a payload byte index (it disagrees with the
    DBC byte in 20 of 21 unambiguous cases), so it cannot currently be used
    to disambiguate.  Its meaning is unresolved.

Consequently: CONFIRMED entries are solid, AMBIGUOUS entries are real
extractions whose owning message is uncertain, and absence from both lists
means "no evidence found", NOT "ignored".
"""
import os
import struct
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
XBASE = 0x4000

from signal_masks import dbc_signals, signal_bits   # noqa: E402
from signal_config import find_ptr_tables, rec      # noqa: E402

BUILDS = {
    "BV6T-14C386-AA": "BV6T-14C386-AA/BV6T-14C386-AA_blk0_0x04008000.bin",
    "CV6T-14C386-AB": "CV6T-14C386-AB/CV6T-14C386-AB_blk0_0x04008000.bin",
}


def load_words(path):
    d = open(path, "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def field(start, length):
    """(byte, mask, shift) for a Motorola/@0+ signal; None if it spans bytes."""
    b, msb = start // 8, start % 8
    sh = msb - length + 1
    return None if sh < 0 else (b, ((1 << length) - 1) << sh, sh)


def cleared_bits(words, xaddr):
    w = words[xaddr - XBASE:xaddr - XBASE + 4]
    raw = b"".join(struct.pack("<H", x) for x in w)
    return {b * 8 + i for b in range(8) for i in range(8)
            if not (raw[b] >> i) & 1}


def descriptors(words):
    """[(msg_id, direction, mask_ptr, slot_bit)] in table order."""
    out = []
    for _p, ents in find_ptr_tables(words):
        for xa, code in ents:
            r = rec(words, xa)
            out.append((r[0], {5: "TX", 7: "RX"}.get(code, "?"), r[5], r[8]))
    return out


def extraction(words, limit=0x1E0):
    recs = []
    for i in range(0, limit - 5, 5):
        spec, bs = words[i + 1], words[i + 2]
        mask, sh, slot = spec >> 8, spec & 0xFF, bs & 0xFF
        if not mask or sh > 7 or not slot or (slot & (slot - 1)):
            continue
        recs.append({"at": XBASE + i, "mask": mask, "sh": sh, "slot": slot})
    return recs


def analyse(path):
    words = load_words(path)
    sigs = dbc_signals()
    desc = descriptors(words)
    byslot = defaultdict(list)
    for cid, _d, _m, slot in desc:
        byslot[slot].append(cid)

    confirmed = defaultdict(set)     # msg -> signals (uniquely attributed)
    ambiguous = defaultdict(set)     # msg -> signals (shared slot geometry)
    unparsed = 0
    for r in extraction(words):
        owners = []
        for cid in byslot.get(r["slot"], []):
            for nm, st, ln, bo in sigs.get(cid, []):
                f = field(st, ln)
                if f and f[1] == r["mask"] and f[2] == r["sh"]:
                    owners.append((cid, nm))
        if not owners:
            unparsed += 1
            continue
        msgs = {c for c, _n in owners}
        for cid, nm in owners:
            (confirmed if len(msgs) == 1 else ambiguous)[cid].add(nm)

    subscribed = {}
    for cid, d, mptr, _slot in desc:
        if not mptr:
            subscribed[cid] = None
            continue
        cl = cleared_bits(words, mptr)
        subscribed[cid] = {nm[:-3] for nm, st, ln, bo in sigs.get(cid, [])
                           if nm.endswith("_UB")
                           and set(signal_bits(st, ln, bo)) & cl}
    return desc, subscribed, confirmed, ambiguous, unparsed, sigs


def report(label, rel):
    path = os.path.join(ROOT, "bins", rel)
    desc, sub, conf, amb, unparsed, sigs = analyse(path)
    print("=" * 72)
    print(f"{label}   ({len(desc)} messages, "
          f"{unparsed} extraction records unparsed -> lists are a LOWER BOUND)")
    print("=" * 72)
    for cid, d, mptr, _slot in sorted(desc):
        allsig = {nm for nm, _s, _l, _b in sigs.get(cid, [])
                  if not nm.endswith("_UB")}
        s = sub.get(cid)
        c = {n for n in conf.get(cid, set()) if not n.endswith("_UB")}
        a = {n for n in amb.get(cid, set()) if not n.endswith("_UB")}
        used = (s or set()) | c
        rest = sorted(allsig - used - a)
        print(f"\n--- 0x{cid:03X} {d} "
              f"{'(no mask, whole-frame)' if not mptr else ''}")
        if s:
            print(f"  subscribed (update bit) : {', '.join(sorted(s))}")
        if c:
            print(f"  extracted  (confirmed)  : {', '.join(sorted(c))}")
        if a:
            print(f"  extracted  (ambiguous)  : {', '.join(sorted(a))}")
        if rest:
            print(f"  no evidence found       : {', '.join(rest)}")


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for label, rel in BUILDS.items():
        if only and only not in label:
            continue
        report(label, rel)
        print()


if __name__ == "__main__":
    main()
