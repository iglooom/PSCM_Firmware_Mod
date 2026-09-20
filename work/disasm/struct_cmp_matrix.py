#!/usr/bin/env python3
"""Pairwise unmatched matrix + proper dispatch/jump-table detection."""
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
funcs = {n: S.discover(imgs[n], table) for n in S.BUILDS}
N = list(S.BUILDS)

print("=== pairwise unmatched matrix (row A -> col B), tol 0.02 / 0.05 ===")
for tol in (0.02, 0.05):
    print(f"\n-- tol {tol} --")
    hdr = "A\\B"
    print(f"{hdr:10s} " + " ".join(f"{b:>12s}" for b in N))
    for a in N:
        row = []
        for b in N:
            if a == b:
                row.append("      -     ")
                continue
            m, un = S.match(funcs[a], funcs[b], tol)
            n = len(funcs[a])
            row.append(f"{len(un):5d} {100.0*len(un)/n:5.1f}%")
        print(f"{a:10s} " + " ".join(row))

print("\n=== dispatch-idiom census (compile-invariant switch detection) ===")
# A C switch on a small enum compiles on DSP56800E to either
#   (a) a chain of CMP #k / Bcc          -> "compare chain"
#   (b) a bounds check then an indexed indirect jump (JMP (Rn) / via table)
# Count BOTH per build, normalised per 1000 instructions.
def census(name):
    img, F = imgs[name], funcs[name]
    chain = collections.Counter()   # length of consecutive CMP#imm/Bcc runs
    indirect = 0
    for e, (body, _c) in F.items():
        addrs = sorted(body)
        run = 0
        for wa in addrs:
            ins = body[wa]
            m = S.mnem(ins)
            txt = ins["text"]
            if m.startswith("CMP") and "#" in txt:
                run += 1
            elif m in ("B.CC", "J.CC"):
                pass
            else:
                if run >= 2:
                    chain[min(run, 12)] += 1
                run = 0
            # indirect jump: JMP with a register operand (no abs target)
            if m in ("JMP", "JMPD") and ins["target"] is None:
                indirect += 1
        if run >= 2:
            chain[min(run, 12)] += 1
    tot = sum(len(b) for b, _c in F.values())
    return chain, indirect, tot

for n in N:
    ch, ind, tot = census(n)
    total_chains = sum(ch.values())
    print(f"\n-- {n} ({tot} insns) --")
    print(f"   compare-chains (>=2 CMP#): {total_chains}"
          f"   per-1k-insn {1000.0*total_chains/tot:.2f}")
    print(f"   chain-length histogram: {dict(sorted(ch.items()))}")
    print(f"   indirect JMPs (register/table dispatch): {ind}"
          f"   per-1k-insn {1000.0*ind/tot:.2f}")
    # chains long enough to dispatch an 8-value enum
    big = sum(v for k, v in ch.items() if k >= 4)
    print(f"   chains >=4 arms (could dispatch a 3-bit enum): {big}")

print("\n=== 2-word P-address tables in data (19-bit code pointers) ===")
for n in N:
    ents = set(funcs[n])
    img = imgs[n]
    found = collections.Counter()
    for s, words in img.spans:
        i = 0
        while i < len(words) - 1:
            run = 0
            j = i
            while j < len(words) - 1:
                hi, lo = words[j], words[j + 1]
                addr = ((hi & 0x7) << 16) | lo
                if hi <= 0x7 and addr in ents:
                    run += 1
                    j += 2
                else:
                    break
            if run >= 3:
                found[run] += 1
                i = j
            else:
                i += 1
    print(f"   {n}: {sum(found.values())} tables, sizes {dict(sorted(found.items()))}")
