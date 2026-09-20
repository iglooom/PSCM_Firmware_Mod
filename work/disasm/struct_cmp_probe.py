#!/usr/bin/env python3
"""Inspect the specific candidates thrown up by struct_cmp.py.

Prints REAL decoded instructions only (flow56800e.decode on actual words).
"""
import os
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "wordA"))
sys.path.insert(0, HERE)
import dis56800e as base          # noqa: E402
import flow56800e as flow         # noqa: E402
import struct_cmp as S            # noqa: E402

table = base.load_table()
imgs = {n: S.Img(n) for n in S.BUILDS}


def cmp_sites(img, val):
    out = []
    for s, words in img.spans:
        for i, w in enumerate(words):
            wa = s + i
            if img.word(wa + 1) != val:
                continue
            d = flow.decode(img, wa, table)
            m = d["text"].split()[0] if d["text"].split() else ""
            if m.upper().startswith("CMP") and d["len"] >= 2:
                out.append((wa, d["text"]))
    return out


print("=== CMP-immediate candidates that survived the AH control ===")
for val in (0x8170, 0x8641):
    print(f"\n--- #${val:04X} ---")
    for n in S.BUILDS:
        h = cmp_sites(imgs[n], val)
        print(f"  {n}: {len(h)} CMP site(s)")
        for wa, t in h[:6]:
            print(f"      P:${wa:05X}  {t}")

print("\n=== chance-coincidence baseline for 'survives control' ===")
# How often does an arbitrary CMP constant present in BV6T but absent from AR
# ALSO happen to be present in AH?  Compare against the same statistic for a
# reversed/neutral pairing.
funcs = {n: S.discover(imgs[n], table) for n in S.BUILDS}
C = {n: S.cmp_imm(imgs[n], funcs[n]) for n in S.BUILDS}
A = {n: S.imm_set(imgs[n], funcs[n]) for n in S.BUILDS}
for lbl, D in (("CMP", C), ("ALL", A)):
    bv, ah, ar = set(D["BV6T-AF"]), set(D["CV6T-AH"]), set(D["CV6T-AR"])
    only_bv = bv - ar
    surv = only_bv & ah
    # neutral control: constants in AH but not AR, that are also in BV6T
    only_ah = ah - ar
    surv_ah = only_ah & bv
    print(f"  {lbl}: |BV-only vs AR|={len(only_bv)}  also-in-AH={len(surv)}"
          f"  ({100.0*len(surv)/max(1,len(only_bv)):.1f}%)")
    print(f"  {lbl}: |AH-only vs AR|={len(only_ah)}  also-in-BV={len(surv_ah)}"
          f"  ({100.0*len(surv_ah)/max(1,len(only_ah)):.1f}%)   <- CONTROL")

print("\n=== AR-only addr16 'tables' — are they real? ===")
ents = set(funcs["CV6T-AR"])
t16 = S.jump_tables(imgs["CV6T-AR"], ents, minlen=4)
for kind, at, ln, vals in t16:
    print(f"  P:${at:05X} len={ln} values={[hex(v) for v in vals]}")
    # what do the same addresses hold in the other builds?
    for n in ("BV6T-AF", "CV6T-AH"):
        print(f"      {n} @same addr: "
              + " ".join(f"{imgs[n].word(at+k):04X}" for k in range(ln)))
    print("      AR raw:               "
          + " ".join(f"{imgs['CV6T-AR'].word(at+k):04X}" for k in range(ln)))
