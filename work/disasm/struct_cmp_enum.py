#!/usr/bin/env python3
"""Enum-test census done properly: parse the immediate out of the DECODED
instruction text, so 1-word short-immediate forms are included.

1-word forms dominate on this core (2370 one-word CMP.W #imm vs 404 two-word
in CV6T-AR), so a census that only reads extension words sees almost nothing.
"""
import os
import re
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "wordA"))
sys.path.insert(0, HERE)
import dis56800e as base          # noqa: E402
import struct_cmp as S            # noqa: E402

table = base.load_table()
imgs = {n: S.Img(n) for n in S.BUILDS}
funcs = {n: S.discover(imgs[n], table) for n in S.BUILDS}
N = list(S.BUILDS)
IMM = re.compile(r"#\$?([0-9A-Fa-f]+)")


def imm_of(img, wa, ins):
    m = IMM.search(ins["text"])
    if not m:
        return None
    try:
        v = int(m.group(1), 16)
    except ValueError:
        return None
    # a '%04X'-formatted extension-word operand is the real value already
    return v


def census(name, pred):
    img, F = imgs[name], funcs[name]
    c = collections.Counter()
    sites = collections.defaultdict(list)
    for e, (body, _x) in F.items():
        for wa, ins in body.items():
            if not pred(S.mnem(ins)):
                continue
            v = imm_of(img, wa, ins)
            if v is not None:
                c[v] += 1
                sites[v].append((e, wa, ins["text"]))
    return c, sites


print("=== CMP #imm census, values 0..8 (the 'test the 3-bit enum' idiom) ===")
CMPP = lambda m: m.startswith("CMP")
res = {n: census(n, CMPP) for n in N}
print(f"{'#imm':>8s} " + " ".join(f"{n:>10s}" for n in N))
for v in range(9):
    print(f"  {v:6d} " + " ".join(f"{res[n][0][v]:10d}" for n in N))
tots = {n: sum(res[n][0].values()) for n in N}
print("   total CMP#imm sites: " + " ".join(f"{n}={tots[n]}" for n in N))
print("   normalised per 1000 insns: " + " ".join(
    f"{n}={1000.0*tots[n]/sum(len(b) for b,_c in funcs[n].values()):.2f}"
    for n in N))

print("\n=== AND/BFTST/BRCLR/BRSET #mask census for lane-enum masks ===")
MASKP = lambda m: m.startswith(("AND", "BFTST", "BFCLR", "BFSET", "BRCLR",
                                "BRSET", "OR.", "EOR"))
res2 = {n: census(n, MASKP) for n in N}
MASKS = (0x70, 0x07, 0x06, 0xF0, 0x0F, 0x03, 0x60, 0x7000, 0x0070)
print(f"{'mask':>8s} " + " ".join(f"{n:>10s}" for n in N))
for v in sorted(set(MASKS)):
    print(f"  0x{v:04X} " + " ".join(f"{res2[n][0][v]:10d}" for n in N))

print("\n=== the key differential: CMP #6 / mask 0x70 sites, per build ===")
for v, (lbl, R) in ((6, ("CMP", res)), (0x70, ("MASK", res2))):
    for n in N:
        hits = R[n][1].get(v, [])
        print(f"  {lbl} #{v:#x} in {n}: {len(hits)} site(s)")
        for e, wa, t in hits[:8]:
            print(f"      sub_{e:05X}  P:${wa:05X}  {t}")

print("\n=== distribution-level control: is the whole CMP#imm histogram "
      "the same shape across builds? ===")
allv = set()
for n in N:
    allv |= set(res[n][0])
def l1(a, b):
    ta, tb = sum(res[a][0].values()), sum(res[b][0].values())
    return sum(abs(res[a][0][v]/ta - res[b][0][v]/tb) for v in allv) / 2
print(f"   L1/2 distance BV6T-AF vs CV6T-AR : {l1('BV6T-AF','CV6T-AR'):.4f}")
print(f"   L1/2 distance CV6T-AH vs CV6T-AR : {l1('CV6T-AH','CV6T-AR'):.4f}  <- CONTROL")
print(f"   L1/2 distance BV6T-AF vs CV6T-AH : {l1('BV6T-AF','CV6T-AH'):.4f}")
