#!/usr/bin/env python3
"""Decode the PSCM CAN mailbox table in the 14C386 *Signal Configuration* VBF.

This is the part that answers "where is CAN ID X handled": the 14C386 block is
a separate VBF (sw_part_type = "Signal Configuration") that loads at byte
0x04008000, i.e. X:$4000 -- the start of data flash, immediately BELOW the
14C217 blk2 calibration at 0x04008C00 (X:$4600).  Scanning only 14C217/14C218
misses it completely.

Record layout (10 words), recovered by consistency across all 20 entries:

  +0  CAN identifier (11-bit, raw -- NOT the FlexCAN ID<<2 layout)
  +1  0x0000            (identifier high / extended-ID slot, unused)
  +2  X-RAM pointer to the 4-word (=8-byte) frame payload buffer; stride 4
  +3  index within this group
  +4  X-RAM pointer, stride 2 (per-message state: rolling count / timeout)
  +5  pointer into the signal-mask area at X:$42B0 (0 for diagnostic frames)
  +6  0x0000
  +7  0x0000
  +8  one-hot bit for this slot (0x0001, 0x0002, 0x0004 ... 0x0100)
  +9  flags: 0x0048 normal application frame / 0x0058 diagnostic frame

Two descriptor groups exist, each with its own pointer table that lists the
record addresses together with a direction code:

  group 1  records X:$41E8..  pointer table X:$4338   codes 0x0005 / 0x0007
  group 2  records X:$4256..  pointer table X:$4378   code  0x0007

Direction code 0x0005 marks exactly the four frames the PSCM TRANSMITS
(0x0B0 PSCM_h_FrP00, 0x140 PSCM_h_FrP01, 0x695 CCP_PSCM_FrTx,
0x738 TST_PhysicalRespPSCM) and 0x0007 marks the received ones -- an
independent confirmation that field +0 really is the CAN identifier.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DBC = "/home/gl/Projects/ford/CANBus/CAN-HS.dbc"

# Signal-config VBF block -> byte load address 0x04008000.
# X word address = byte_address / 2 - 0x2000000  (see Research/README.md).
SC_BYTE_BASE = 0x04008000
XBASE = SC_BYTE_BASE // 2 - 0x2000000          # = 0x4000

REC_WORDS = 10
# Pointer-table entries are {record_addr, 0x0000, direction_code, 0x0001}.
# Table ADDRESSES differ between builds (BV6T's sit 2 words earlier), so they
# are located by shape rather than hardcoded, and the record COUNT comes from
# walking until the shape breaks.
REC_LO, REC_HI = 0x41E0, 0x42B0
DIRECTION = {0x0005: "TX", 0x0007: "RX"}


def dbc_names():
    ids = {}
    if not os.path.exists(DBC):
        return ids
    for ln in open(DBC, errors="replace"):
        m = re.match(r"^BO_ (\d+) (\w+)", ln)
        if m and int(m.group(1)) < 0x800:
            ids[int(m.group(1))] = m.group(2)
    return ids


def load(path):
    d = open(path, "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def rec(words, xaddr):
    i = xaddr - XBASE
    return words[i:i + REC_WORDS]


def entry_ok(words, i):
    if i + 4 > len(words):
        return False
    addr, zero, code, one = words[i:i + 4]
    return (REC_LO <= addr < REC_HI and zero == 0 and one == 1
            and code in DIRECTION)


def find_ptr_tables(words):
    """Locate pointer tables by shape; return [(xaddr, [(rec,code), ...])]."""
    tables = []
    i = 0
    while i < len(words):
        if entry_ok(words, i):
            start = i
            ents = []
            while entry_ok(words, i):
                addr, _z, code, _o = words[i:i + 4]
                ents.append((addr, code))
                i += 4
            if len(ents) >= 4:            # ignore incidental 1-2 entry noise
                tables.append((XBASE + start, ents))
        else:
            i += 1
    return tables


def decode(path, label):
    words = load(path)
    names = dbc_names()
    print(f"===== {label} =====")
    print(f"{'rec':>7} {'ID':>6} {'dir':>4} {'name':<24}"
          f"{'buf':>8}{'state':>8}{'mask':>8}{'bit':>7} flags")
    seen = {}
    for gi, (ptab, entries) in enumerate(find_ptr_tables(words), 1):
        print(f"-- group {gi}: {len(entries)} records "
              f"(pointer table X:${ptab:04X}) --")
        for xa, code in entries:
            r = rec(words, xa)
            cid = r[0]
            d = DIRECTION.get(code, f"?{code:04X}")
            print(f" ${xa:04X} 0x{cid:03X} {d:>4} {names.get(cid, '-'):<24}"
                  f"X:${r[2]:04X} X:${r[4]:04X} X:${r[5]:04X}"
                  f" {r[8]:#06x} {r[9]:#06x}")
            seen[cid] = (xa, d, r)
    return seen


def main():
    cur = decode(os.path.join(ROOT, "bins", "CV6T-14C386-AB",
                              "CV6T-14C386-AB_blk0_0x04008000.bin"),
                 "CV6T-14C386-AB (current, signal configuration)")
    print()
    old = decode(os.path.join(ROOT, "bins", "BV6T-14C386-AA",
                              "BV6T-14C386-AA_blk0_0x04008000.bin"),
                 "BV6T-14C386-AA (earlier build)")

    print("\n===== 0xA5 verdict =====")
    for lbl, tbl in (("CV6T-14C386-AB", cur), ("BV6T-14C386-AA", old)):
        if 0xA5 in tbl:
            xa, d, r = tbl[0xA5]
            print(f"  {lbl}: PRESENT at X:${xa:04X}  dir={d}  "
                  f"payload buffer X:${r[2]:04X}  state X:${r[4]:04X}  "
                  f"slot bit {r[8]:#06x}")
        else:
            print(f"  {lbl}: absent")

    print("\n===== delta between the two builds =====")
    for cid in sorted(set(cur) | set(old)):
        if cid not in old:
            print(f"  + 0x{cid:03X} added in CV6T")
        elif cid not in cur:
            print(f"  - 0x{cid:03X} removed in CV6T")


if __name__ == "__main__":
    main()
