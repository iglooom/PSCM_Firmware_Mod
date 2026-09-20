#!/usr/bin/env python3
"""Fully decode ONE instruction word: every matching encoding, every field
value, and the resolved register name from each relevant manual table.

The listing renderer hides exactly what we now need: which register each
EOR / ZXT.B / LSRR operand refers to. This prints the raw field bits so the
loop body at P:$16216 can be read operand-by-operand instead of guessed.

Usage:
  python3 decode_word.py 7AFA 7ED2 5FA8 ...
  python3 decode_word.py --erm 7AFA      # also print the manual context
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ERM = "/home/gl/Projects/ford/PSCM/Research/docs/erm.txt"

# ---- register tables, transcribed in dis56800e.py (ERM A-7, A-10..A-13) ----
import dis56800e as D

TABLES = {
    "DD": lambda v: D.DD.get(v),
    "F": lambda v: D.F1.get(v),
    "FF": lambda v: D.FF.get(v),
    "FFF": lambda v: D.FFF.get(v),
    "EEE": lambda v: D.EEE.get(v),
    "GGG": lambda v: D.GGG.get(v),
    "GGGG": lambda v: D.GGGG.get(v),
    "RRR": lambda v: D.RRR.get(v),
    "NNN": lambda v: D.NNN.get(v),
    "SSS": lambda v: D.SSS.get(v),
    "RR": lambda v: D.RR.get(v),
    "DDDDD_load": lambda v: D.D5.get(v, ("?", "?"))[0],
    "DDDDD_store": lambda v: D.D5.get(v, ("?", "?"))[1],
    "ddddd": lambda v: D.d5.get(v),
    "hhh_wl": lambda v: D.h3.get(v, ("?",) * 4)[0],
    "hhh_ws": lambda v: D.h3.get(v, ("?",) * 4)[1],
    "hhhh_wl": lambda v: D.h4.get(v, ("?",) * 4)[0],
    "hhhh_ws": lambda v: D.h4.get(v, ("?",) * 4)[1],
}

tbl = json.load(open(os.path.join(HERE, "encodings.json")))
for e in tbl:
    e["nb"] = bin(e["mask"]).count("1")
tbl.sort(key=lambda e: -e["nb"])


def fld(word, bits):
    v = 0
    for b in bits:
        v = (v << 1) | ((word >> b) & 1)
    return v


def interp(width, v, ops):
    """Plausible register names for a field of this width."""
    out = []
    if width == 1:
        out.append(("F", D.F1.get(v)))
    if width == 2:
        out += [("DD", D.DD.get(v)), ("FF", D.FF.get(v)), ("RR", D.RR.get(v))]
    if width == 3:
        out += [("EEE/FFF", D.EEE.get(v)), ("GGG", D.GGG.get(v)),
                ("RRR", D.RRR.get(v)), ("hhh(load)", D.h3.get(v, ("?",))[0]),
                ("hhh(store)", D.h3.get(v, ("?", "?"))[1])]
    if width == 4:
        out += [("GGGG", D.GGGG.get(v)), ("hhhh(load)", D.h4.get(v, ("?",))[0]),
                ("hhhh(store)", D.h4.get(v, ("?", "?"))[1])]
    if width == 5:
        out += [("DDDDD load", D.D5.get(v, ("?", "?"))[0]),
                ("DDDDD store", D.D5.get(v, ("?", "?"))[1]),
                ("ddddd", D.d5.get(v))]
    return [(k, n) for k, n in out if n and n != "??"]


def erm_context(mnem, bits, lines=6):
    """Grep the manual for the grid line of this encoding."""
    try:
        txt = open(ERM, encoding="utf-8", errors="replace").read().split("\n")
    except OSError:
        return []
    pat = re.compile(r"^\s*" + re.escape(mnem) + r"\b")
    return [f"  erm:{i+1}  {ln.strip()[:110]}"
            for i, ln in enumerate(txt) if pat.match(ln)][:lines]


args = [a for a in sys.argv[1:] if not a.startswith("--")]
show_erm = "--erm" in sys.argv

for a in args:
    word = int(a, 16)
    print("=" * 78)
    print(f"WORD {word:04X}   = {word:016b}")
    cands = [e for e in tbl if (word & e["mask"]) == e["value"]]
    if not cands:
        print("  no matching encoding")
        continue
    for e in cands[:6]:
        print(f"\n  {e['mnem']:<10} {e['operands']:<32} [{e['bits']}] "
              f"erm:{e['line']}  fixed={e['nb']}b")
        for letter, bits in sorted(e["fields"].items(),
                                   key=lambda kv: -max(kv[1])):
            v = fld(word, bits)
            width = len(bits)
            names = interp(width, v, e["operands"])
            nm = ", ".join(f"{k}={n}" for k, n in names)
            print(f"      field '{letter}' bits{bits} = {v:0{width}b} "
                  f"= 0x{v:X} ({v}){'   ' + nm if nm else ''}")
        if show_erm:
            for ln in erm_context(e["mnem"]):
                print(ln)
