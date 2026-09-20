#!/usr/bin/env python3
"""Compare the 14C218 parameters that are pairable across builds.

The calibrations share no coordinate system (1.9% positionally alignable), so
most addresses cannot be compared.  But a handful of parameters are read from
the SAME P-space address in both builds -- these are anchored by the code that
reads them and are therefore the same parameter by construction.

For each, dump the surrounding words from both builds side by side so a real
tuning difference would be visible.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

from cal_usesites import cal_sites, BUILDS   # noqa: E402

CAL = {
    "BV6T": ("BV6T-14C218-AF/BV6T-14C218-AF_blk0_0x00009800.bin", 0x04C00),
    "CV6T": ("CV6T-14C218-AX/CV6T-14C218-AX_blk0_0x00009800.bin", 0x04C00),
}
N = 12


def load_cal(label):
    rel, base = CAL[label]
    d = open(os.path.join(ROOT, "bins", rel), "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d)), base


def main():
    anchored = {}
    for label, spec in BUILDS.items():
        anchored[label] = {c for _p, c, _r, n in cal_sites(spec) if n > 0}
    shared = sorted(anchored["BV6T"] & anchored["CV6T"])
    print(f"parameters read from the SAME address in both builds: "
          f"{len(shared)}\n")

    cals = {k: load_cal(k) for k in CAL}
    same = diff = 0
    for addr in shared:
        rows = {}
        for label in CAL:
            w, base = cals[label]
            i = addr - base
            rows[label] = w[i:i + N] if 0 <= i < len(w) else None
        a, b = rows["BV6T"], rows["CV6T"]
        if a is None or b is None:
            print(f"P:${addr:05X}: outside the calibration block in one build")
            continue
        eq = a == b
        same += eq
        diff += not eq
        print(f"P:${addr:05X}  {'IDENTICAL' if eq else '*** DIFFERS ***'}")
        print(f"   BV6T: " + " ".join(f"{x:04X}" for x in a))
        print(f"   CV6T: " + " ".join(f"{x:04X}" for x in b))
        if not eq:
            marks = "".join("  ^^ " if x != y else "     "
                            for x, y in zip(a, b))
            print(f"         {marks}")
        print()
    print(f"summary: {same} identical, {diff} differing "
          f"(first {N} words at each anchor)")


if __name__ == "__main__":
    main()
