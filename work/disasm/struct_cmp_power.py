#!/usr/bin/env python3
"""(1) Detection-power (ablation) experiment.  (2) Enum-mask idiom census."""
import os
import sys
import random
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "wordA"))
sys.path.insert(0, HERE)
import dis56800e as base          # noqa: E402
import flow56800e as flow         # noqa: E402
import struct_cmp as S            # noqa: E402

table = base.load_table()
imgs = {n: S.Img(n) for n in S.BUILDS}
funcs = {n: S.discover(imgs[n], table) for n in S.BUILDS}
TOL = 0.02

print("=== (1) ABLATION: can the method see a feature that is truly absent? ===")
print("Delete M functions from CV6T-AH, then re-match CV6T-AR -> AH'.")
print("The rise in unmatched count over the M=0 baseline is the DETECTION POWER")
print("for a feature of that size.  AH/AR is the same family, so this measures")
print("signal on top of the LOWEST-noise pairing available.\n")

base_m, base_un = S.match(funcs["CV6T-AR"], funcs["CV6T-AH"], TOL)
nAR = len(funcs["CV6T-AR"])
print(f"  M=0 baseline: {len(base_un)}/{nAR} unmatched "
      f"({100.0*len(base_un)/nAR:.1f}%)")

rng = random.Random(20260914)
# only ablate functions that currently DO match (i.e. real shared code),
# and prefer mid/large ones -- an LCA subsystem is not 5-instruction stubs.
cands = [e for e in funcs["CV6T-AH"] if len(funcs["CV6T-AH"][e][0]) >= 40]
for M in (1, 3, 5, 10, 20, 40):
    deltas = []
    for _t in range(5):
        drop = set(rng.sample(cands, M))
        ah2 = {e: v for e, v in funcs["CV6T-AH"].items() if e not in drop}
        m2, un2 = S.match(funcs["CV6T-AR"], ah2, TOL)
        deltas.append(len(un2) - len(base_un))
    mean = sum(deltas) / len(deltas)
    print(f"  M={M:3d} functions ablated (>=40 insns): unmatched rises by "
          f"{mean:+.1f} on average  (runs {deltas})  "
          f"-> recovery {100.0*mean/M:.0f}%")

print("\n=== (2) enum-mask idiom census: the LkaActvStats signature ===")
print("byte3 shift4 mask0x70 -> the extract idiom is AND #$70 / #$07 after a")
print("shift, or a BFTST/ANDC with those masks.  Count per build.\n")
MASKS = (0x0070, 0x0007, 0x0006, 0x00F0, 0x000F, 0x0003, 0x0060)


def mask_census(name):
    img, F = imgs[name], funcs[name]
    c = collections.Counter()
    for e, (body, _x) in F.items():
        for wa, ins in body.items():
            m = S.mnem(ins)
            if m.startswith(("AND", "BFTST", "BFCLR", "BFSET", "BFCHG", "OR",
                             "EOR")) and ins["len"] >= 2:
                v = img.word(wa + 1)
                if v in MASKS:
                    c[v] += 1
    return c


cs = {n: mask_census(n) for n in S.BUILDS}
print(f"{'mask':>8s} " + " ".join(f"{n:>10s}" for n in S.BUILDS))
for v in MASKS:
    print(f"  0x{v:04X} " + " ".join(f"{cs[n][v]:10d}" for n in S.BUILDS))

print("\n=== (3) CMP-against-small-enum census (the '== 6' test) ===")
def cmpsmall(name):
    img, F = imgs[name], funcs[name]
    c = collections.Counter()
    for e, (body, _x) in F.items():
        for wa, ins in body.items():
            t = ins["text"]
            m = S.mnem(ins)
            if m.startswith("CMP") and "#" in t:
                if ins["len"] >= 2:
                    v = img.word(wa + 1)
                else:
                    # short-form immediate embedded in the opcode word
                    v = None
                if v is not None and v <= 8:
                    c[v] += 1
    return c


ce = {n: cmpsmall(n) for n in S.BUILDS}
print(f"{'#imm':>8s} " + " ".join(f"{n:>10s}" for n in S.BUILDS))
for v in range(9):
    print(f"  0x{v:04X} " + " ".join(f"{ce[n][v]:10d}" for n in S.BUILDS))
