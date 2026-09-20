#!/usr/bin/env python3
"""Pair 14C218 calibration parameters by their CODE USE SITE, not address.

Why: the two calibrations share no coordinate system (only 1.9% positionally
alignable), so addresses cannot be compared.  But a parameter's MEANING is
fixed by the code that reads it.  If the same function in both builds loads a
pointer and reads calibration through it, those two calibration addresses are
the same parameter regardless of where they sit.

Mechanism: on DSP56800E, program memory is read ONLY through register-indirect
addressing -- `MOVE.W P:<ea_m>,GGG` = `1000 0GGG 0110 1mRR` (ERM line 26953).
There is no absolute P: read.  So every calibration access looks like:

      moveu.w #$<cal_addr>,Rn        ; 0x8748/0x874A/... + address word
      ...
      move.w  P:(Rn)+,<reg>          ; 0x80.. 0x68/0x6C..

This scans for pointer-register loads whose immediate lands in the calibration
range P:$04C00..$0DFFF, and reports them with the enclosing function (nearest
preceding JSR target), giving a use-site keyed inventory per build.
"""
import os
import struct
import sys
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CAL_LO, CAL_HI = 0x04C00, 0x0DFFF
AGU_LOADS = {0x8748: "R0", 0x8749: "R1", 0x874A: "R2", 0x874B: "R3",
             0x874C: "R4", 0x874D: "R5"}

BUILDS = {
    "BV6T": [("BV6T-14C217-AF/BV6T-14C217-AF_blk0_0x00000000.bin", 0x00000),
             ("BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin", 0x0E000)],
    "CV6T": [("CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin", 0x00000),
             ("CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin", 0x0E000)],
}


def load(rel):
    d = open(os.path.join(ROOT, "bins", rel), "rb").read()
    return list(struct.unpack("<%dH" % (len(d) // 2), d))


def is_pmem_read(w):
    """MOVE.W P:<ea_m>,GGG  -> 1000 0GGG 0110 1mRR (mask 0xF8F8 == 0x8068)."""
    return (w & 0xF8F8) == 0x8068


def jsr_targets(spec):
    t = set()
    for rel, base in spec:
        w = load(rel)
        for i in range(len(w) - 1):
            if (w[i] & 0xFFF4) == 0xE254:
                a = (((w[i] >> 3) & 1) << 18) | (((w[i] >> 1) & 1) << 17) \
                    | ((w[i] & 1) << 16)
                t.add(a | w[i + 1])
    return sorted(t)


def cal_sites(spec, window=24):
    """[(p_addr, cal_addr, reg, reads_nearby)] for calibration pointer loads."""
    out = []
    for rel, base in spec:
        w = load(rel)
        for i in range(len(w) - 1):
            if w[i] in AGU_LOADS and CAL_LO <= w[i + 1] <= CAL_HI:
                reads = sum(1 for k in range(i + 2, min(i + 2 + window, len(w)))
                            if is_pmem_read(w[k]))
                out.append((base + i, w[i + 1], AGU_LOADS[w[i]], reads))
    return out


def main():
    info = {}
    for label, spec in BUILDS.items():
        sites = cal_sites(spec)
        funcs = jsr_targets(spec)
        withread = [s for s in sites if s[3] > 0]
        info[label] = (sites, withread, funcs)
        print(f"{label}: {len(sites)} calibration pointer loads, "
              f"{len(withread)} with a P-memory read within 24 words")

    print()
    for label in BUILDS:
        sites, withread, funcs = info[label]
        # attribute each site to its enclosing function
        byfunc = defaultdict(list)
        for p, cal, reg, n in withread:
            f = max([t for t in funcs if t <= p], default=None)
            byfunc[f].append((p, cal, reg, n))
        print(f"--- {label}: {len(byfunc)} functions read calibration ---")
        top = sorted(byfunc.items(), key=lambda kv: -len(kv[1]))[:10]
        for f, lst in top:
            cals = sorted({c for _p, c, _r, _n in lst})
            print(f"   sub_{f:05X}: {len(lst)} loads, "
                  f"{len(cals)} distinct cal addrs "
                  f"[{', '.join(f'${c:05X}' for c in cals[:5])}"
                  f"{', ...' if len(cals) > 5 else ''}]")
        print()


if __name__ == "__main__":
    main()
