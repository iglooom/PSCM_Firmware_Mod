#!/usr/bin/env python3
"""Which signals the PSCM consumes from each CAN message (14C386 config).

The answer is the descriptor's +5 mask (the table at X:$42B0, 4 words per
message).  Every bit it CLEARS is an `_UB` (Update Bit) signal -- 30 out of 30
across all 13 messages that carry a mask.  On this platform each subscribed
signal has a companion `<signal>_UB` update bit, so the set of tracked update
bits IS the set of consumed signals.

Why that reading is trusted:

 1. Bit convention is chosen by the data.  Scoring cleared bits against DBC
    signal boundaries: lsb-first gives 30 whole signals / 0 partially cut,
    msb-first gives 13 whole / 14 cut.  A wrong bit order smears across
    boundaries; the right one snaps to them.
 2. 30/30 of the cleared bits are `_UB` signals.  Chance alignment would not
    hit the update-bit suffix every single time.
 3. Every resulting signal set is exactly what a power-steering module needs
    (steering angle, vehicle speed, wheel speeds, yaw/lateral accel, ABS/TC
    state, propulsion torque, reverse gear, park-assist steering request) --
    and nothing it does not.

A SEPARATE table at X:$42E4 (6 words/record, keyed by payload buffer, only 7
messages) is NOT decoded here: its polarity is genuinely ambiguous -- neither
reading is semantically coherent across all 7 -- so it is left as open work
rather than guessed at.  See PSCM_can_id_handling.md.
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

SC = os.path.join(ROOT, "bins", "CV6T-14C386-AB",
                  "CV6T-14C386-AB_blk0_0x04008000.bin")


def load_words(path):
    d = open(path, "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def cleared_bits(words, xaddr):
    """Payload bit positions whose mask bit is 0 (lsb-first within a byte)."""
    w = words[xaddr - XBASE:xaddr - XBASE + 4]
    raw = b"".join(struct.pack("<H", x) for x in w)
    return {b * 8 + i for b in range(8) for i in range(8)
            if not (raw[b] >> i) & 1}


def main():
    words = load_words(SC)
    sigs = dbc_signals()

    rows = []
    for _p, ents in find_ptr_tables(words):
        for xa, code in ents:
            r = rec(words, xa)
            rows.append((r[0], {5: "TX", 7: "RX"}.get(code, "?"), r[5]))

    ub = tot = 0
    for cid, _d, mptr in rows:
        if not mptr:
            continue
        cl = cleared_bits(words, mptr)
        for nm, st, ln, bo in sigs.get(cid, []):
            if set(signal_bits(st, ln, bo)) & cl:
                tot += 1
                ub += nm.endswith("_UB")
    print(f"sanity: {ub}/{tot} masked signals are _UB update bits")
    print("NOTE: this mask only speaks about signals that HAVE a _UB companion.")
    print("Signals without an update bit are UNDETERMINED by it, not 'ignored'.\n")

    for cid, d, mptr in sorted(rows):
        names = sigs.get(cid, [])
        base = [nm for nm, _s, _l, _b in names if not nm.endswith("_UB")]
        has_ub = {nm[:-3] for nm, _s, _l, _b in names if nm.endswith("_UB")}
        if not mptr:
            print(f"===== 0x{cid:03X} {d} =====  no signal mask "
                  f"(whole-frame handling)")
            continue
        cl = cleared_bits(words, mptr)
        tracked = {nm[:-3] for nm, st, ln, bo in names
                   if nm.endswith("_UB") and set(signal_bits(st, ln, bo)) & cl}
        untracked = sorted(has_ub - tracked)
        unknown = sorted(set(base) - has_ub)
        print(f"===== 0x{cid:03X} {d}  mask X:${mptr:04X} =====")
        print(f"  SUBSCRIBED   ({len(tracked):2d}): "
              + (", ".join(sorted(tracked)) or "-"))
        print(f"  UB-NOT-SET   ({len(untracked):2d}): "
              + (", ".join(untracked) or "-"))
        print(f"  UNDETERMINED ({len(unknown):2d}, no update bit): "
              + (", ".join(unknown) or "-"))
        print()


if __name__ == "__main__":
    main()
