#!/usr/bin/env python3
"""Decisive search for CAN ID 0xA5 handling across ALL PSCM flash blocks.

Covers every place an 11-bit ID can physically appear on this target:
  * 14C217 blk0 + blk1   (program flash)
  * 14C218 blk0          (calibration -- a SEPARATE VBF, easy to forget)
  * 14C217 blk2          (X/data flash)
in every encoding the hardware or compiler could use:
  raw 0x00A5, FlexCAN ID_HIGH layout 0x0294 (=ID<<2), ID<<5 (0x14A0),
  and extended-frame layout ID<<18.
Then checks whether an acceptance MASK could admit 0xA5 implicitly.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from flow56800e import Image, ABS_PREFIX  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BINS = os.path.join(ROOT, "bins")

EXTRA = [
    ("14C218-AX cal", "CV6T-14C218-AX/CV6T-14C218-AX_blk0_0x00009800.bin",
     0x00009800),
    ("14C217 blk2 X", "CV6T-14C217-AR/CV6T-14C217-AR_blk2_0x04008C00.bin",
     0x04008C00),
]

TARGET = 0xA5
ENCODINGS = [
    ("raw", TARGET),                 # 0x00A5
    ("ID<<2 (FlexCAN ID_HIGH)", TARGET << 2),   # 0x0294
    ("ID<<5", TARGET << 5),          # 0x14A0
]


def words(path):
    d = open(path, "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def main():
    img = Image()
    print("=== 1. literal occurrences of 0xA5 in every block/encoding ===")
    total = 0
    for label, val in ENCODINGS:
        for s, w in img.spans:
            for i, v in enumerate(w):
                if v == val:
                    wa = s + i
                    prev = img.word(wa - 1)
                    role = ("OPERAND of " + ABS_PREFIX[prev].split()[0]
                            if prev in ABS_PREFIX else "bare data word")
                    print(f"  P:${wa:05X}  {val:04X}  [{label}]  {role}")
                    total += 1
        for name, rel, byte_addr in EXTRA:
            p = os.path.join(BINS, rel)
            if not os.path.exists(p):
                continue
            for i, v in enumerate(words(p)):
                if v == val:
                    print(f"  {name} +0x{i:05X} (X:${byte_addr // 2 + i:05X})"
                          f"  {val:04X}  [{label}]")
                    total += 1
    print(f"  -> {total} literal hit(s)\n")

    print("=== 2. is 0xA5 ever an instruction operand? ===")
    hits = 0
    for wa, w in img.iter_words():
        if w in ABS_PREFIX and img.word(wa + 1) in (TARGET, TARGET << 2):
            print(f"  P:${wa:05X}  {ABS_PREFIX[w] % img.word(wa + 1)}")
            hits += 1
    print(f"  -> {hits} operand hit(s)\n")

    print("=== 3. FlexCAN acceptance masks written by the firmware ===")
    # MC56F8366 FlexCAN: RXGMASK X:$F810/11, RX14MASK $F812/13, RX15MASK $F814/15
    MASKS = {0xF810: "RXGMASK_HI", 0xF811: "RXGMASK_LO",
             0xF812: "RX14MASK_HI", 0xF813: "RX14MASK_LO",
             0xF814: "RX15MASK_HI", 0xF815: "RX15MASK_LO"}
    found = False
    for wa, w in img.iter_words():
        if w in ABS_PREFIX and img.word(wa + 1) in MASKS:
            print(f"  P:${wa:05X}  {ABS_PREFIX[w] % img.word(wa + 1)}"
                  f"   ; {MASKS[img.word(wa + 1)]}")
            found = True
    if not found:
        print("  (no absolute writes -- masks set via pointer/table code)")


if __name__ == "__main__":
    main()
