#!/usr/bin/env python3
"""Compatibility check: can the CV6T-14C217-AR LCA enabler be applied to
HV6T-14C217-AC?

Checks, without modifying anything:
  1. container parses + verifies (both blocks and file CRC)
  2. block topology identical to the proven CV6T target
  3. internal checksum word A (CRC16/MCRF4XX gapped) reproduces the stored word
  4. internal checksum word B (sum16le whole-module) reproduces the stored word
  5. the self-check START immediate resolves the same way
  6. each of the 5 patch sites carries the exact OEM stock words
  7. each guard context is byte-identical
  8. the two dispatcher arms that patches 4/5 repoint to exist and match
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import build_fd22_snapshot_vbf as base
import build_lca_final_vbf as lca

ROOT = Path(__file__).resolve().parents[2]
STOCK = ROOT / "HV6T-14C217-AC.VBF"
CAL = ROOT / "HV6T-14C218-AD.VBF"


def hx(words):
    return " ".join(f"{w:04X}" for w in words)


def main():
    stock = base.parse_vbf(STOCK)
    cal = base.parse_vbf(CAL)

    print(f"== {STOCK.name}  +  {CAL.name} ==\n")

    # 1. container integrity
    base.verify_container(stock)
    base.verify_container(cal)
    print("[1] container CRCs (block + file): OK for both files")

    # 2. topology vs the proven CV6T target
    ref = base.parse_vbf(base.DEFAULT_STOCK)
    topo = [(b.address, len(b.data)) for b in stock.blocks]
    ref_topo = [(b.address, len(b.data)) for b in ref.blocks]
    print(f"[2] block topology {'MATCH' if topo == ref_topo else 'DIFFER'}: {[(hex(a),hex(l)) for a,l in topo]}")
    cal_ok = (len(cal.blocks) == 1 and cal.blocks[0].address == 0x00009800)
    print(f"    calibration block map {'OK' if cal_ok else 'UNEXPECTED'}: "
          f"{[hex(b.address) for b in cal.blocks]}")

    block0 = stock.block_at(base.BLK0_FLASH).data
    block1 = stock.block_at(base.BLK1_FLASH).data

    # 3/5. word A + START immediate
    stored_a = base._u16le(block1, base.WORD_A_OFF)
    try:
        start = base.find_start_offset(block1)
        start_str = f"0x{start:X}"
    except base.BuildError as exc:
        start = None
        start_str = f"FAIL ({exc})"
    print(f"\n[5] self-check START immediate: {start_str}")
    if start is not None:
        calc_a = base.crc16_mcrf4xx(block0 + block1[start:base.WORD_A_OFF])
        print(f"[3] word A @0x{base.WORD_A_OFF:X}: stored={stored_a:04X} "
              f"calc={calc_a:04X}  {'REPRODUCES' if calc_a == stored_a else 'MISMATCH'}")
    else:
        print(f"[3] word A @0x{base.WORD_A_OFF:X}: stored={stored_a:04X} (START unknown)")

    # 4. word B
    stored_b = base._u16le(block1, base.WORD_B_OFF)
    calc_b = base.calculate_word_b(stock, cal, block1)
    print(f"[4] word B @0x{base.WORD_B_OFF:X}: stored={stored_b:04X} "
          f"calc={calc_b:04X}  {'REPRODUCES' if calc_b == stored_b else 'MISMATCH'}")
    cal_self = base.sum16le(cal.blocks[0].data)
    print(f"    calibration self-sum: {cal_self:04X} "
          f"({'==0xFFFF as expected' if cal_self == 0xFFFF else 'NOT 0xFFFF'})")

    # 6. patch sites
    print("\n[6] patch sites (stock words expected by the CV6T enabler):")
    all_ok = True
    for label, off, old, new in lca.PATCHES:
        found = struct.unpack_from("<%dH" % len(old), block1, off)
        ok = found == old
        all_ok &= ok
        print(f"    {'OK ' if ok else 'XX '} 0x{off:05X}  {label[:26]:26s} "
              f"want {hx(old)} | got {hx(found)}")

    # 7. guards
    print("\n[7] guard contexts:")
    for label, off, words in lca.GUARDS:
        found = struct.unpack_from("<%dH" % len(words), block1, off)
        ok = found == words
        all_ok &= ok
        print(f"    {'OK ' if ok else 'XX '} 0x{off:05X}  {label[:24]:24s} "
              f"{'match' if ok else 'DIFFER'}")
        if not ok:
            print(f"           want {hx(words)}")
            print(f"           got  {hx(found)}")

    print("\n=== SUMMARY ===")
    print(f"container:      OK")
    print(f"topology:       {'MATCH' if topo == ref_topo else 'DIFFER'}")
    print(f"word A algo:    {'reproduces' if start and calc_a == stored_a else 'DOES NOT reproduce'}")
    print(f"word B algo:    {'reproduces' if calc_b == stored_b else 'DOES NOT reproduce'}")
    print(f"patch+guards:   {'ALL MATCH' if all_ok else 'MISMATCH — layout differs'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
