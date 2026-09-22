#!/usr/bin/env python3
"""Confirm patch 4/5 offsets and dump all HV6T constants for the builder."""
from __future__ import annotations
import hashlib, struct
from pathlib import Path
import build_fd22_snapshot_vbf as base

ROOT = Path(__file__).resolve().parents[2]
STOCK = ROOT / "HV6T-14C217-AC.VBF"
CAL = ROOT / "HV6T-14C218-AD.VBF"
stock = base.parse_vbf(STOCK); cal = base.parse_vbf(CAL)
H = stock.block_at(base.BLK1_FLASH).data
def wl(o): return struct.unpack_from("<H", H, o)[0]
def hx(o,n): return " ".join(f"{x:04X}" for x in struct.unpack_from("<%dH"%n,H,o))

d1=0x3A18C; d2=0x3A28E
p4=d1+0x14; p5=d2+0x14
print(f"Patch4 d1 entry5 @0x{p4:05X}: {wl(p4):04X} (code4 arm={wl(d1+8):04X}) -> set {wl(d1+8):04X}")
print(f"Patch5 d2 entry5 @0x{p5:05X}: {wl(p5):04X} (code1 arm={wl(d2+2):04X}) -> set {wl(d2+2):04X}")
print(f"d1 full table @0x{d1:05X}: {hx(d1,11)}")
print(f"d2 full table @0x{d2:05X}: {hx(d2,11)}")

print("\n--- SHAs ---")
print("stock file :", hashlib.sha256(stock.raw).hexdigest())
print("cal   file :", hashlib.sha256(cal.raw).hexdigest())
for b in stock.blocks:
    print(f"blk 0x{b.address:08X}: {hashlib.sha256(b.data).hexdigest()}")
print("\n--- internal words ---")
print(f"word A @0x{base.WORD_A_OFF:X}: {wl(base.WORD_A_OFF):04X}")
print(f"word B @0x{base.WORD_B_OFF:X}: {wl(base.WORD_B_OFF):04X}")
print(f"START offset: 0x{base.find_start_offset(H):X}")
print(f"cal block addr: 0x{cal.blocks[0].address:08X}  self-sum: {base.sum16le(cal.blocks[0].data):04X}")
print(f"blocks map: {[hex(b.address) for b in stock.blocks]}")
