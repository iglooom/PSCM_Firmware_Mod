#!/usr/bin/env python3
"""Decode which SIGNALS the PSCM actually consumes from each CAN message.

There are TWO mask tables in the 14C386 signal configuration, and they mean
different things -- conflating them is the easy mistake:

  A) descriptor field +5 -> 4-word mask at X:$42B0..
     Empirically 30/30 of the bits it CLEARS are `_UB` (Update Bit) signals.
     This is an update-bit map, NOT a used/ignored filter.

  B) a separate 6-word-per-record table at X:$42E4, keyed by the message's
     payload-buffer pointer:
         +0  payload buffer pointer (matches descriptor +2)
         +1  0x0000
         +2..+5  4 words = 64-bit mask
     Only 7 of the 20 messages have such a record.

MASK POLARITY (chosen by the data, not assumed): a bit SET means the payload
bit is MASKED OUT / not consumed; a bit CLEAR means the module extracts it.
Scored by whether the implied signals align to whole DBC boundaries:

    set=used   -> 7 whole signals, 8 partially covered   (incoherent)
    set=masked -> 92 whole signals, 8 partially covered  <-- chosen

The semantics confirm it: under this polarity 0x010 yields SteeringAngle and
0x1E0 yields Veh_V_ActlBrk -- the two inputs a power-steering module cannot
work without.  The opposite polarity claimed it used only checksum/quality
bits and ignored the steering angle, which is nonsense for a PSCM.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
DBC = "/home/gl/Projects/ford/CANBus/CAN-HS.dbc"
XBASE = 0x4000
USE_TABLE = 0x42E4
USE_REC = 6

from signal_masks import dbc_signals, signal_bits  # noqa: E402
from signal_config import find_ptr_tables, rec     # noqa: E402


def load_words(path):
    d = open(path, "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def bits_used(words, xaddr):
    """64-bit mask -> payload bit positions the module CONSUMES.

    Polarity: a SET bit is masked out, so the used bits are the CLEARED ones
    (LSB-first within each byte).  See the module docstring for the scoring
    that establishes this.
    """
    w = words[xaddr - XBASE:xaddr - XBASE + 4]
    raw = b"".join(struct.pack("<H", x) for x in w)
    return {b * 8 + i for b in range(8) for i in range(8)
            if not (raw[b] >> i) & 1}


def use_records(words, bufs):
    """Walk the use-mask table while +0 is a known payload-buffer pointer."""
    out = {}
    a = USE_TABLE
    while True:
        i = a - XBASE
        if i + USE_REC > len(words):
            break
        buf, zero = words[i], words[i + 1]
        if buf not in bufs or zero != 0:
            break
        out[buf] = a + 2
        a += USE_REC
    return out


def main():
    sc = os.path.join(ROOT, "bins", "CV6T-14C386-AB",
                      "CV6T-14C386-AB_blk0_0x04008000.bin")
    words = load_words(sc)
    sigs = dbc_signals()

    # message id -> (payload buffer, direction)
    msgs = {}
    for _p, ents in find_ptr_tables(words):
        for xa, code in ents:
            r = rec(words, xa)
            msgs[r[0]] = (r[2], {5: "TX", 7: "RX"}.get(code, "?"))
    bufs = {b for b, _ in msgs.values()}
    used = use_records(words, bufs)

    print(f"Use-mask table at X:${USE_TABLE:04X}: {len(used)} records "
          f"of {USE_REC} words\n")

    # validate the reading: do set bits land on whole signals?
    whole = partial = 0
    for cid, (buf, _d) in msgs.items():
        if buf not in used:
            continue
        on = bits_used(words, used[buf])
        for nm, st, ln, bo in sigs.get(cid, []):
            sb = set(signal_bits(st, ln, bo))
            if sb & on:
                whole += (sb <= on)
                partial += not (sb <= on)
    print(f"validation: whole signals {whole}, partially covered {partial} (higher=better)\n")

    for cid in sorted(msgs):
        buf, d = msgs[cid]
        if buf not in used:
            continue
        on = bits_used(words, used[buf])
        名 = [(nm, set(signal_bits(st, ln, bo)))
              for nm, st, ln, bo in sigs.get(cid, [])]
        usedsig = [nm for nm, sb in 名 if sb & on]
        ign = [nm for nm, sb in 名 if not (sb & on)]
        print(f"===== 0x{cid:03X} {d}  buffer X:${buf:04X} "
              f"mask X:${used[buf]:04X} =====")
        print(f"  USED    ({len(usedsig)}): "
              + (", ".join(usedsig) if usedsig else "-"))
        print(f"  IGNORED ({len(ign)}): "
              + (", ".join(ign) if ign else "-"))
        unclaimed = on - set().union(*[sb for _n, sb in 名]) if 名 else on
        if unclaimed:
            print(f"  set bits not in any DBC signal: {sorted(unclaimed)}")
        print()

    nomask = [c for c in sorted(msgs) if msgs[c][0] not in used]
    print("messages with NO use-mask record (all-or-nothing handling): "
          + ", ".join(f"0x{c:03X}" for c in nomask))


if __name__ == "__main__":
    main()
