#!/usr/bin/env python3
"""Word-aligned structural relocation of patch sites 1/2/3 in HV6T-14C217-AC."""
from __future__ import annotations
import struct
from pathlib import Path
import build_fd22_snapshot_vbf as base

ROOT = Path(__file__).resolve().parents[2]
H = base.parse_vbf(ROOT / "HV6T-14C217-AC.VBF").block_at(base.BLK1_FLASH).data
PBASE = base.BLK1_PWORD_BASE
N = len(H)//2
WORDS = struct.unpack("<%dH" % N, H[:N*2])

def off2p(o): return PBASE + o//2
def hx(ws): return " ".join(f"{x:04X}" for x in ws)

def wsearch(template):
    """template: tuple of ints or None (wildcard). Returns list of word-indices."""
    out=[]
    L=len(template)
    for i in range(N-L):
        ok=True
        for j,t in enumerate(template):
            if t is not None and WORDS[i+j]!=t:
                ok=False; break
        if ok: out.append(i)
    return out

# Patch 1: 4C06 A207 F07C <cal> 4C01 A203 E684 <state> A902 E680 <state>
print("== Patch 1 (LCA state entry gate) ==")
for i in wsearch((0x4C06,0xA207,0xF07C,None,0x4C01,0xA203,0xE684,None,0xA902,0xE680)):
    ctx=WORDS[i:i+11]
    if ctx[7]==ctx[10]:  # both <state> equal
        gate=i+2
        print(f"  P:${off2p(i*2):05X} @0x{i*2:05X}: {hx(ctx)}")
        print(f"    cal={ctx[3]:04X} state={ctx[7]:04X}")
        print(f"    PATCH1 @0x{gate*2:05X}: {hx(WORDS[gate:gate+4])} -> E700 E700 E700 E700")
        print(f"    GUARD  @0x{i*2:05X}: {hx(ctx[:8])}")

# Patch 2: F07C <state> 4C04 A20C FF7C <egate> A209  [E700 4C83 A206]
print("\n== Patch 2 (phase-1 entry inhibit) ==")
for i in wsearch((0xF07C,None,0x4C04,0xA20C,0xFF7C,None,0xA209)):
    ctx=WORDS[i:i+10]
    inh=i+4
    print(f"  P:${off2p(i*2):05X} @0x{i*2:05X}: {hx(ctx)}")
    print(f"    state={ctx[1]:04X} egate={ctx[5]:04X}")
    print(f"    PATCH2 @0x{inh*2:05X}: {hx(WORDS[inh:inh+3])} -> E700 E700 E700")
    print(f"    GUARD  @0x{i*2:05X}: {hx(ctx[:7])}")

# Patch 3: F07C <code> 4C02 <a> E700 4C05 <a5> E700 4C03
print("\n== Patch 3 (code-5 availability) ==")
for i in wsearch((0xF07C,None,0x4C02,None,0xE700,0x4C05,None,0xE700,0x4C03)):
    ctx=WORDS[i:i+10]
    br=i+6
    print(f"  P:${off2p(i*2):05X} @0x{i*2:05X}: {hx(ctx)}")
    print(f"    code={ctx[1]:04X} code2br={ctx[3]:04X} code5br={ctx[6]:04X}")
    print(f"    PATCH3 @0x{br*2:05X}: {hx(WORDS[br:br+1])} -> E700")
    print(f"    GUARD  @0x{i*2:05X}: {hx(ctx[:9])}")
