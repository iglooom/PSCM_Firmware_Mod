#!/usr/bin/env python3
"""Decode the per-signal EXTRACTION table in the 14C386 signal configuration.

This is the table that actually answers "which signals does the PSCM unpack",
and it is far stronger evidence than the update-bit mask: it names the exact
bit field of every signal the module extracts, plus where it stores it.

Record layout (5 words), phase-locked by shape:

    +0  extra/second-stage source RAM word (0 for simple single-byte fields)
    +1  packed field spec:  high byte = bit MASK within the payload byte
                            low  byte = right SHIFT count
    +2  high byte = payload byte selector, low byte = message SLOT bit
    +3  destination RAM word address
    +4  destination RAM area/base (0x7F75, 0x7F7D, 0x7F8A, ...)

DBC Motorola/@0+ bit numbering: a signal's start bit is its MSB, at
byte = start//8, bit position = start%8 (7 = MSB of that byte).  So

    shift = (start % 8) - length + 1      mask = ((1<<length)-1) << shift

e.g. `LkaActvStats_D_Req : 30|3@0+` -> byte 3, msb pos 6, shift 4, mask 0x70,
which is exactly the record spec word 0x7004.

IMPORTANT: slot bits are REUSED between the two descriptor groups (in CV6T
slot 0x20 belongs to BOTH 0x0A5 and 0x1C0), so a slot alone does not identify
a message.  Records are therefore matched against the union of the candidate
messages' signals, and a match is only reported when exactly ONE candidate
message owns a signal with that (mask, shift) -- ambiguous ones are flagged.
"""
import os
import struct
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
XBASE = 0x4000

from signal_masks import dbc_signals             # noqa: E402
from signal_config import find_ptr_tables, rec   # noqa: E402

BUILDS = {
    "BV6T": ("BV6T-14C386-AA/BV6T-14C386-AA_blk0_0x04008000.bin",
             "BV6T-14C217-AF"),
    "CV6T": ("CV6T-14C386-AB/CV6T-14C386-AB_blk0_0x04008000.bin",
             "CV6T-14C217-AR"),
}


def load_words(path):
    d = open(path, "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def sig_field(start, length):
    """(byte, mask, shift) for a Motorola/@0+ signal, or None if it spans.

    The start bit is the field's MSB; bit position within the byte is
    start % 8 (7 = MSB).  Verified against LkaActvStats_D_Req (30|3 -> 0x70>>4,
    matching the firmware's own spec word 0x7004).
    """
    b, msb = start // 8, start % 8
    shift = msb - length + 1
    if shift < 0:
        return None                  # crosses a byte boundary
    return b, ((1 << length) - 1) << shift, shift


def slots(words):
    """slot bit -> [(msg_id, direction), ...]  (slots are reused!)"""
    out = {}
    for _p, ents in find_ptr_tables(words):
        for xa, code in ents:
            r = rec(words, xa)
            out.setdefault(r[8], []).append(
                (r[0], {5: "TX", 7: "RX"}.get(code, "?")))
    return out


def extraction_records(words, limit=0x1E0):
    """Find stride-5 records by locking phase on plausible field specs."""
    best, bestn = 0, -1
    for phase in range(5):
        n = 0
        for i in range(phase, limit - 5, 5):
            mask, shift = words[i + 1] >> 8, words[i + 1] & 0xFF
            slot = words[i + 2] & 0xFF
            if mask and shift <= 7 and slot and not (slot & (slot - 1)):
                n += 1
        if n > bestn:
            best, bestn = phase, n
    recs = []
    for i in range(best, limit - 5, 5):
        mask, shift = words[i + 1] >> 8, words[i + 1] & 0xFF
        sel, slot = words[i + 2] >> 8, words[i + 2] & 0xFF
        if not mask or shift > 7 or not slot or (slot & (slot - 1)):
            continue
        recs.append({"at": XBASE + i, "mask": mask, "shift": shift,
                     "sel": sel, "slot": slot,
                     "dst": words[i + 3], "area": words[i + 4],
                     "extra": words[i]})
    return best, recs


def analyse(label):
    rel, _fw = BUILDS[label]
    words = load_words(os.path.join(ROOT, "bins", rel))
    sigs = dbc_signals()
    slotmap = slots(words)
    phase, recs = extraction_records(words)

    matched = defaultdict(list)      # msg_id -> [(signal, record)]
    ambiguous = []
    for r in recs:
        owners = []
        for cid, _d in slotmap.get(r["slot"], []):
            for nm, st, ln, bo in sigs.get(cid, []):
                f = sig_field(st, ln)
                if f and f[1] == r["mask"] and f[2] == r["shift"]:
                    owners.append((cid, nm))
        uniq = {c for c, _n in owners}
        if len(uniq) == 1:
            cid = owners[0][0]
            for c, nm in owners:
                matched[cid].append((nm, r))
        elif len(uniq) > 1:
            ambiguous.append((r, owners))
    return matched, ambiguous, recs, phase


def main():
    res, allrecs = {}, {}
    for label in BUILDS:
        matched, ambiguous, recs, phase = analyse(label)
        res[label] = matched
        allrecs[label] = (recs, ambiguous)
        n = sum(len(v) for v in matched.values())
        print(f"===== {label}: {len(recs)} extraction records (phase {phase}), "
              f"{n} uniquely matched, {len(ambiguous)} ambiguous")
    print()

    # The slot-uniqueness filter is NOT a usable discriminator between builds:
    # the two descriptor groups reuse slot bits, and the co-tenant message
    # differs per build (BV6T slot 0x40 = 0x0A5 + 0x1E0; CV6T slot 0x20 =
    # 0x0A5 + 0x1C0).  So a record can be "unique" in one build and "ambiguous"
    # in the other purely because of its co-tenant -- an artefact, not a
    # feature change.  Compare the RECORD SPECS instead, which are build-
    # independent.
    print("=== 0x0A5 records compared by SPEC (slot-independent) ===")
    specs = {}
    for label in BUILDS:
        rel, _fw = BUILDS[label]
        words = load_words(os.path.join(ROOT, "bins", rel))
        sigs = dbc_signals()
        slotmap = slots(words)
        _ph, recs = extraction_records(words)
        s = set()
        for r in recs:
            owners = {cid for cid, _d in slotmap.get(r["slot"], [])
                      for nm, st, ln, bo in sigs.get(cid, [])
                      if (f := sig_field(st, ln))
                      and f[1] == r["mask"] and f[2] == r["shift"]}
            if 0x0A5 in owners:
                s.add((r["mask"], r["shift"], r["sel"], r["extra"] != 0))
        specs[label] = s
        print(f"  {label}: {len(s)} distinct 0x0A5-compatible field specs")
        for mask, sh, sel, ex in sorted(s):
            names = sorted({nm for nm, st, ln, bo in sigs.get(0x0A5, [])
                            if (f := sig_field(st, ln))
                            and f[1] == mask and f[2] == sh})
            print(f"     mask {mask:#04x} >>{sh} sel {sel:#04x}"
                  f"{' +2nd-stage' if ex else '':<12} -> {'/'.join(names)}")
    only_b = specs["BV6T"] - specs["CV6T"]
    only_c = specs["CV6T"] - specs["BV6T"]
    print(f"\n  specs only in BV6T: {sorted(only_b) or '-'}")
    print(f"  specs only in CV6T: {sorted(only_c) or '-'}")
    if not only_b and not only_c:
        print("  -> 0x0A5 extraction is IDENTICAL in both builds.")


if __name__ == "__main__":
    main()
