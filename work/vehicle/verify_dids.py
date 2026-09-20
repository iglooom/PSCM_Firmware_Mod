#!/usr/bin/env python3
"""Verify dump_dids.py's DID list against the firmware table itself.

Independent second code path: re-extracts the table at P:$0EDC4 straight from
the binary and asserts it matches TABLE_DIDS exactly. Catches transcription
errors -- the tool must probe what the ECU actually declares, not what I typed.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "work", "vehicle"))

TABLE_ADDR = 0x0EDC4
STRIDE = 6

ok = True


def chk(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", name,
                          ("  " + detail) if detail else ""))


def load():
    p0 = os.path.join(ROOT, "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin")
    p1 = os.path.join(ROOT, "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin")
    spans = []
    for path, base in ((p0, 0x00000), (p1, 0x0E000)):
        d = open(path, "rb").read()
        spans.append((base, list(struct.unpack("<%dH" % (len(d) // 2), d))))
    return spans


def W(spans, a):
    for base, arr in spans:
        if base <= a < base + len(arr):
            return arr[a - base]
    return None


def main():
    spans = load()
    import dump_dids

    print("verify_dids.py -- re-extract the DID table from the binary\n")

    # walk the table exactly as the firmware lays it out
    found = []
    a = TABLE_ADDR
    while True:
        did = W(spans, a)
        if did is None or not (0xD000 <= did <= 0xFFFF):
            break
        rest = [W(spans, a + k) for k in range(1, STRIDE)]
        found.append((did, rest))
        a += STRIDE

    print("   extracted %d entries from P:$%05X\n" % (len(found), TABLE_ADDR))
    chk("firmware table has 40 entries", len(found) == 40, str(len(found)))

    # The tool probes a SUPERSET: the firmware table plus 19 DIDs seen only in
    # the UCDS capture (candump-2026-09-14_222146.log). So the correct
    # assertion is containment, not equality -- every DID the firmware
    # declares must still be probed.
    fw = [d for d, _ in found]
    missing = [d for d in fw if d not in dump_dids.TABLE_DIDS]
    chk("every firmware-declared DID is probed by the tool",
        not missing, " ".join("%04X" % d for d in missing))
    extra = [d for d in dump_dids.TABLE_DIDS if d not in fw]
    chk("the tool adds the UCDS-only DIDs", len(extra) == 19,
        "%d extra: %s" % (len(extra), " ".join("%04X" % d for d in extra)))
    chk("probe set is sorted and unique",
        dump_dids.TABLE_DIDS == sorted(set(dump_dids.TABLE_DIDS)))

    # structure assertions
    chk("every entry has word+1 == 0000",
        all(r[0] == 0 for _, r in found))
    nonempty = [(d, r) for d, r in found if r[1] != 0]
    chk("read descriptor present for all but F112",
        len(nonempty) == len(found) - 1,
        "%d of %d" % (len(nonempty), len(found)))
    f112 = [r for d, r in found if d == 0xF112]
    chk("F112 is the empty entry", f112 and all(x == 0 for x in f112[0]),
        " ".join("%04X" % x for x in f112[0]) if f112 else "missing")

    # descriptor indices step by 3
    idx = [(r[1] & 0x0FFF) // 3 for d, r in nonempty]
    chk("descriptor indices are unique", len(set(idx)) == len(idx))

    # the known part-number DID must be in there
    chk("F188 present (part number)", 0xF188 in dump_dids.TABLE_DIDS)
    chk("F190 present (VIN)", 0xF190 in dump_dids.TABLE_DIDS)

    # the ones we actually want to probe
    unknown = [d for d in dump_dids.TABLE_DIDS if d not in dump_dids.KNOWN]
    chk("every probed DID now has a name", not unknown,
        " ".join("%04X" % d for d in unknown))

    # the measurement set, cross-checked against the map document
    chk("FD0E is named Torque Loop Demand",
        "Torque Loop Demand" in dump_dids.KNOWN.get(0xFD0E, ""))
    chk("FD0E is in the torque watch set", 0xFD0E in dump_dids.TORQUE_DIDS)
    chk("FD13 Function enable is in the config watch set",
        0xFD13 in dump_dids.CONFIG_DIDS)

    doc = os.path.join(ROOT, "PSCM_DID_map.md")
    if os.path.exists(doc):
        md = open(doc).read()
        for did, name in ((0xFD0E, "Torque Loop Demand"),
                          (0xFD0C, "Q-Axis Current"),
                          (0xFD15, "Lane Assist enable")):
            chk("PSCM_DID_map.md documents %04X" % did,
                ("%04X" % did) in md.upper() and name in md)

    print("\n   probe set: %d DIDs (%d firmware + %d UCDS-only)"
          % (len(dump_dids.TABLE_DIDS), len(fw), len(extra)))
    print("   torque set: " + " ".join("%04X" % d for d in dump_dids.TORQUE_DIDS))
    print("   config set: " + " ".join("%04X" % d for d in dump_dids.CONFIG_DIDS))

    print("\n" + "=" * 56)
    print("VERIFY:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
