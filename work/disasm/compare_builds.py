#!/usr/bin/env python3
"""Compare the CAN signal configuration of two PSCM builds.

Usage: python3 compare_builds.py            (defaults BV6T-14C386-AA vs CV6T-14C386-AB)

Reports, per build and as a diff:
  * the mailbox descriptor table (IDs, direction)
  * the per-message used/ignored signal sets (descriptor +5 mask, _UB bits)

Everything here is offline analysis of the *signal configuration* VBF only.
For the main-firmware side of a feature question this is necessary but NOT
sufficient: absence of a signal subscription proves the module cannot act on
that signal, but presence does not prove the control logic exists in 14C217.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
XBASE = 0x4000

from signal_masks import dbc_signals, signal_bits   # noqa: E402
from signal_config import find_ptr_tables, rec      # noqa: E402

BUILDS = [
    ("BV6T-14C386-AA", "BV6T-14C386-AA/BV6T-14C386-AA_blk0_0x04008000.bin"),
    ("CV6T-14C386-AB", "CV6T-14C386-AB/CV6T-14C386-AB_blk0_0x04008000.bin"),
]


def load_words(path):
    d = open(path, "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def cleared_bits(words, xaddr):
    w = words[xaddr - XBASE:xaddr - XBASE + 4]
    raw = b"".join(struct.pack("<H", x) for x in w)
    return {b * 8 + i for b in range(8) for i in range(8)
            if not (raw[b] >> i) & 1}


def analyse(path):
    """-> {msg_id: (direction, used_signal_set_or_None)}"""
    words = load_words(path)
    sigs = dbc_signals()
    out = {}
    for _p, ents in find_ptr_tables(words):
        for xa, code in ents:
            r = rec(words, xa)
            cid, mptr = r[0], r[5]
            d = {5: "TX", 7: "RX"}.get(code, "?")
            if not mptr:
                out[cid] = (d, None)
                continue
            cl = cleared_bits(words, mptr)
            used = {nm[:-3] for nm, st, ln, bo in sigs.get(cid, [])
                    if nm.endswith("_UB") and set(signal_bits(st, ln, bo)) & cl}
            out[cid] = (d, used)
    return out


def main():
    res = {}
    for label, rel in BUILDS:
        res[label] = analyse(os.path.join(ROOT, "bins", rel))
        n = len(res[label])
        print(f"{label}: {n} messages configured")
    a_lbl, b_lbl = BUILDS[0][0], BUILDS[1][0]
    A, B = res[a_lbl], res[b_lbl]

    print(f"\n===== message-level diff ({a_lbl} -> {b_lbl}) =====")
    for cid in sorted(set(A) | set(B)):
        if cid not in B:
            print(f"  REMOVED  0x{cid:03X} ({A[cid][0]})")
        elif cid not in A:
            print(f"  ADDED    0x{cid:03X} ({B[cid][0]})")

    print(f"\n===== signal-level diff on shared messages =====")
    any_diff = False
    for cid in sorted(set(A) & set(B)):
        ua, ub = A[cid][1], B[cid][1]
        if ua is None and ub is None:
            continue
        ua, ub = ua or set(), ub or set()
        if ua == ub:
            continue
        any_diff = True
        print(f"  0x{cid:03X} {B[cid][0]}:")
        for s in sorted(ua - ub):
            print(f"      -{s}   (dropped in {b_lbl})")
        for s in sorted(ub - ua):
            print(f"      +{s}   (added in {b_lbl})")
    if not any_diff:
        print("  (none — identical signal selection on every shared message)")

    print("\n===== lane-assist signals, side by side =====")
    for cid in (0x0A5, 0x140, 0x0C8):
        for lbl, R in ((a_lbl, A), (b_lbl, B)):
            if cid in R and R[cid][1] is not None:
                la = sorted(s for s in R[cid][1]
                            if s.startswith(("La", "Lka", "Lca", "Ldw")))
                print(f"  0x{cid:03X} {lbl}: {la if la else '-'}")
        print()


if __name__ == "__main__":
    main()
