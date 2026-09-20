#!/usr/bin/env python3
"""Full-image recursive disassembly + dataflow search for the LCA question.

Seeds from the interrupt/reset vector table, follows every call/branch, and
additionally seeds from any word that decodes as a JSR <ABS19> whose target
lands inside a mapped span (a conservative second pass, clearly labelled).

Writes the listing to work/disasm/out/<build>.lst so later greps are cheap.

Usage:
  python3 lca_trace.py --build CV6T-AR --dump
  python3 lca_trace.py --build CV6T-AR --grep 7ED7
"""
import argparse
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "work", "wordA"))
sys.path.insert(0, HERE)

import dis56800e as base          # noqa: E402
import flow56800e as flow         # noqa: E402

BUILDS = {
    "CV6T-AR": [("CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin", 0x00000000),
                ("CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin", 0x0001C000)],
    "CV6T-AH": [("CV6T-14C217-AH/CV6T-14C217-AH_blk0_0x00000000.bin", 0x00000000),
                ("CV6T-14C217-AH/CV6T-14C217-AH_blk1_0x0001C000.bin", 0x0001C000)],
    "BV6T-AF": [("BV6T-14C217-AF/BV6T-14C217-AF_blk0_0x00000000.bin", 0x00000000),
                ("BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin", 0x0001C000)],
}


class Img(flow.Image):
    def __init__(self, blocks):
        self.spans = []
        for fn, byte_addr in blocks:
            d = open(os.path.join(ROOT, "bins", fn), "rb").read()
            nw = len(d) // 2
            self.spans.append((byte_addr // 2,
                               list(struct.unpack("<%dH" % nw, d[:nw * 2]))))
        self.spans.sort()


def seeds(img):
    """Vector-table entries + every plausible JSR/JMP <ABS19> target."""
    s = set()
    for wa in range(0, 0x100, 2):          # vector table: 2-word JSR entries
        w = img.get(wa)
        if w is None:
            continue
        if (w & 0xFFF4) in (0xE254, 0xE154, 0xE354):
            a = (((w >> 3) & 1) << 18) | (((w >> 1) & 1) << 17) | ((w & 1) << 16)
            t = a | img.word(wa + 1)
            if img.get(t) is not None:
                s.add(t)
    # second pass: any JSR-shaped word anywhere with an in-image target
    for wa, w in img.iter_words():
        if (w & 0xFFF4) == 0xE254:
            a = (((w >> 3) & 1) << 18) | (((w >> 1) & 1) << 17) | ((w & 1) << 16)
            t = a | img.word(wa + 1)
            if img.get(t) is not None:
                s.add(t)
    return s


def disassemble_all(img, table):
    seen = {}
    todo = list(seeds(img))
    while todo:
        wa = todo.pop()
        while wa not in seen and img.get(wa) is not None:
            ins = flow.decode(img, wa, table)
            seen[wa] = ins
            if ins["flow"] == "end":
                break
            if ins["target"] is not None and ins["flow"] != "call":
                todo.append(ins["target"])
                if ins["flow"] == "jump":
                    break
            elif ins["flow"] == "cond" and ins["target"] is not None:
                todo.append(ins["target"])
            wa += max(1, ins["len"])
    return seen


def render(img, seen):
    out = []
    for wa in sorted(seen):
        ins = seen[wa]
        raw = " ".join(f"{img.word(wa + k):04X}" for k in range(ins["len"]))
        out.append(f"P:${wa:05X}  {raw:<14} {ins['text']}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="CV6T-AR")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()
    img = Img(BUILDS[a.build])
    table = base.load_table()
    seen = disassemble_all(img, table)
    total = sum(len(w) for _s, w in img.spans)
    cov = sum(i["len"] for i in seen.values())
    print(f"{a.build}: {len(seen)} instructions, {cov}/{total} words covered "
          f"({100.0*cov/total:.1f}%)", file=sys.stderr)
    od = os.path.join(HERE, "out")
    os.makedirs(od, exist_ok=True)
    p = os.path.join(od, f"{a.build}.lst")
    with open(p, "w") as f:
        f.write(render(img, seen) + "\n")
    print(p, file=sys.stderr)


if __name__ == "__main__":
    main()
