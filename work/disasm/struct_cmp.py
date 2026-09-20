#!/usr/bin/env python3
"""Compile-invariant structural comparison of three PSCM 14C217 builds.

  BV6T-14C217-AF   believed to support LCA
  CV6T-14C217-AH   same family as the vehicle build, older  -- NEGATIVE CONTROL
  CV6T-14C217-AR   the build on the vehicle

Everything here is symmetric: every BV6T-vs-AR measurement is repeated for
AH-vs-AR so recompilation noise can be subtracted.

Stages (select with --stage):
  inventory   function discovery + shape statistics
  fingerprint call-graph fingerprint matching, unmatched-function census
  consts      opcode-filtered 16-bit immediate SETS
  jtables     jump/branch-table inventory

No disassembly text is ever invented: every number here comes from
flow56800e.decode() on real image words.
"""
import argparse
import collections
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "work", "wordA"))
sys.path.insert(0, HERE)

import dis56800e as base            # noqa: E402
import flow56800e as flow           # noqa: E402

BUILDS = {
    "BV6T-AF": "BV6T-14C217-AF",
    "CV6T-AH": "CV6T-14C217-AH",
    "CV6T-AR": "CV6T-14C217-AR",
}
# P-space blocks only (blk2 @ byte 0x04008C00 is X-space data flash).
PBLOCKS = [("_blk0_0x00000000.bin", 0x00000000),
           ("_blk1_0x0001C000.bin", 0x0001C000)]


class Img:
    """Sparse P-space word image, same address model as flow56800e.Image."""

    def __init__(self, name):
        d = os.path.join(ROOT, "bins", BUILDS[name])
        self.name = name
        self.spans = []
        for suf, ba in PBLOCKS:
            p = os.path.join(d, BUILDS[name] + suf)
            b = open(p, "rb").read()
            nw = len(b) // 2
            self.spans.append((ba // 2,
                               list(struct.unpack("<%dH" % nw, b[:nw * 2]))))
        self.spans.sort()
        self._cache = {}

    def get(self, wa):
        for s, w in self.spans:
            if s <= wa < s + len(w):
                return w[wa - s]
        return None

    def word(self, wa):
        v = self.get(wa)
        return 0xFFFF if v is None else v

    def window(self, wa, n=4):
        return [self.word(wa + k) for k in range(n)]

    def iter_words(self):
        for s, w in self.spans:
            for i, v in enumerate(w):
                yield s + i, v

    def code_ranges(self):
        return [(s, s + len(w)) for s, w in self.spans]


# ---------------------------------------------------------------- discovery
def entry_seeds(img, table):
    """Reset + interrupt vector table: 2-word JSR/JMP slots at P:$0000..."""
    seeds = []
    wa = 0
    while wa < 0x100:
        w = img.word(wa)
        if (w & 0xFFF4) in (0xE254, 0xE154, 0xE354):
            d = flow.decode(img, wa, table)
            if d["target"] is not None:
                seeds.append(d["target"])
            wa += 2
        else:
            wa += 1
    return seeds


def walk_function(img, table, entry, max_insn=3000):
    """Linear-with-branches walk of ONE function; does not follow calls.

    Returns (body {wa: ins}, callees set) or None if the entry does not decode
    into anything that looks like code.
    """
    body = {}
    calls = set()
    todo = [entry]
    while todo and len(body) < max_insn:
        wa = todo.pop()
        while wa not in body and img.get(wa) is not None:
            ins = flow.decode(img, wa, table)
            body[wa] = ins
            f = ins["flow"]
            if f == "call":
                if ins["target"] is not None:
                    calls.add(ins["target"])
            elif f == "end":
                break
            elif ins["target"] is not None:
                todo.append(ins["target"])
                if f == "jump":
                    break
            wa += max(1, ins["len"])
            if len(body) >= max_insn:
                break
    return body, calls


def sweep_seeds(img, table):
    """Linear sweep for call opcodes anywhere in the image.

    Recursive descent from the vector table alone reaches <10% of the words,
    because most of this firmware is invoked through scheduler / function
    pointer tables.  A linear sweep for the two CALL encodings recovers the
    rest.  It is applied IDENTICALLY to all three builds, so any residual
    false-positive rate is common-mode and cancels in the comparison.
    """
    seeds = set()
    for s, words in img.spans:
        for i, w in enumerate(words):
            wa = s + i
            if (w & 0xFFF4) == 0xE254 or (w & 0xFFFC) == 0xE26C:
                d = flow.decode(img, wa, table)
                t = d["target"]
                if t is not None and d["flow"] == "call" and img.get(t) is not None:
                    seeds.add(t)
    return seeds


def discover(img, table, max_funcs=20000):
    """Recursive descent over call edges. -> {entry: (body, callees)}"""
    funcs = {}
    todo = list(entry_seeds(img, table)) + list(sweep_seeds(img, table))
    seen = set(todo)
    while todo and len(funcs) < max_funcs:
        e = todo.pop()
        if img.get(e) is None:
            continue
        body, calls = walk_function(img, table, e)
        if not body:
            continue
        funcs[e] = (body, calls)
        for c in calls:
            if c not in seen and img.get(c) is not None:
                seen.add(c)
                todo.append(c)
    return funcs


# ------------------------------------------------------------- fingerprints
def mnem(ins):
    t = ins["text"].split()
    if not t:
        return "?"
    m = t[0]
    # normalise conditional branch/jump suffixes: Bne/Beq/... -> B.cc
    if len(m) > 1 and m[0] in "BJ" and m[1:].islower() and m[1:] in (
            "cc", "cs", "ne", "eq", "ge", "lt", "gt", "le", "hi", "ls",
            "nn", "nr"):
        return m[0].upper() + ".CC"
    return m.upper()


def opclass(m):
    """Coarse, compiler-stable opcode class."""
    if m in ("B.CC", "J.CC"):
        return "cond"
    if m in ("BRA", "BRAD", "JMP", "JMPD"):
        return "jmp"
    if m in ("JSR", "BSR"):
        return "call"
    if m.startswith(("RTS", "RTI", "FRTID")):
        return "ret"
    if m.startswith(("MOVE", "MOVEU", "LEA", "PUSH", "POP", "SWAP", "TFR")):
        return "move"
    if m.startswith(("ADD", "SUB", "INC", "DEC", "NEG", "ABS", "CLR", "CMP",
                     "TST")):
        return "alu"
    if m.startswith(("MPY", "MAC", "IMPY", "DIV", "MACR", "MPYR")):
        return "mul"
    if m.startswith(("AND", "OR", "EOR", "NOT", "LSL", "LSR", "ASL", "ASR",
                     "ROL", "ROR", "BFTST", "BFSET", "BFCLR", "BFCHG")):
        return "logic"
    if m.startswith(("DO", "REP", "ENDDO", "BRK", "CONT")):
        return "loop"
    return "other"


def fingerprint(body, calls):
    cls = collections.Counter(opclass(mnem(i)) for i in body.values())
    return {
        "n": len(body),
        "cond": cls["cond"],
        "call": cls["call"],
        "ret": cls["ret"],
        "loop": cls["loop"],
        "mul": cls["mul"],
        "logic": cls["logic"],
        "move": cls["move"],
        "alu": cls["alu"],
        "callees": len(calls),
    }


KEYS = ("n", "cond", "call", "ret", "loop", "mul", "logic", "move", "alu")


def fp_key(fp):
    return tuple(fp[k] for k in KEYS)


def fp_dist(a, b):
    """L1 distance normalised by size -- 0.0 == identical shape."""
    num = sum(abs(a[k] - b[k]) for k in KEYS)
    den = max(1, a["n"] + b["n"])
    return num / den


# ------------------------------------------------------------ immediates
# Opcode words whose FOLLOWING word is a genuine 16-bit immediate/abs operand.
IMM_PREFIX = set(flow.ABS_PREFIX)


def imm_set(img, funcs):
    """16-bit immediates that appear in DECODED instruction streams only."""
    out = collections.Counter()
    for entry, (body, _c) in funcs.items():
        for wa, ins in body.items():
            if ins["len"] >= 2:
                w = img.word(wa)
                if w in IMM_PREFIX:
                    out[img.word(wa + 1)] += 1
                elif (w & 0xFF00) in (0x8600, 0x8700, 0x8400, 0x8500):
                    # generic "opcode + extension word" immediate forms
                    out[img.word(wa + 1)] += 1
    return out


def cmp_imm(img, funcs):
    """Immediates used with a COMPARE opcode (the enum-test signature)."""
    out = collections.Counter()
    for entry, (body, _c) in funcs.items():
        for wa, ins in body.items():
            m = mnem(ins)
            if m.startswith("CMP") and ins["len"] >= 2:
                out[img.word(wa + 1)] += 1
    return out


# ------------------------------------------------------------- jump tables
def jump_tables(img, code_addrs, minlen=4):
    """Runs of >=minlen consecutive words that are plausible code addresses.

    On this core a 19-bit P address stored in data would need two words, but
    the common compiler idiom for a small switch is a table of 16-bit low
    words within the same 64K page, or a table of 2-word JMPs.  Both are
    detected: (a) word runs whose values all land inside a discovered
    function's address range, (b) runs of JMP/BRA instructions.
    """
    tables = []
    lo = min(code_addrs)
    hi = max(code_addrs)
    for s, words in img.spans:
        run = []
        for i, v in enumerate(words):
            wa = s + i
            page = wa & 0xF0000
            cand = page | v
            if lo <= cand <= hi and cand in code_addrs:
                run.append((wa, cand))
            else:
                if len(run) >= minlen:
                    tables.append(("addr16", run[0][0], len(run),
                                   [c for _a, c in run]))
                run = []
        if len(run) >= minlen:
            tables.append(("addr16", run[0][0], len(run),
                           [c for _a, c in run]))
    return tables


def jmp_tables(img, table, funcs, minlen=3):
    """Runs of consecutive unconditional JMP <ABS19> -- classic switch arms."""
    out = []
    inside = set()
    for _e, (body, _c) in funcs.items():
        inside.update(body)
    for s, words in img.spans:
        wa = s
        end = s + len(words)
        while wa < end - 1:
            run = []
            p = wa
            while p < end - 1 and (img.word(p) & 0xFFF4) == 0xE154:
                d = flow.decode(img, p, table)
                run.append((p, d["target"]))
                p += 2
            if len(run) >= minlen:
                out.append((run[0][0], len(run), [t for _a, t in run]))
                wa = p
            else:
                wa += 1
    return out


# ------------------------------------------------------------------ report
def load_all():
    table = base.load_table()
    imgs = {}
    funcs = {}
    for n in BUILDS:
        imgs[n] = Img(n)
        funcs[n] = discover(imgs[n], table)
    return table, imgs, funcs


def pct(x, n):
    return f"{100.0 * x / n:.1f}%" if n else "n/a"


def hist(vals, edges):
    c = collections.Counter()
    for v in vals:
        for e in edges:
            if v <= e:
                c[e] += 1
                break
        else:
            c["inf"] += 1
    return c


def stage_inventory(imgs, funcs):
    edges = [5, 10, 20, 40, 80, 160, 320, 640]
    print("=== function inventory ===")
    rows = {}
    for n in BUILDS:
        F = funcs[n]
        sizes = [len(b) for b, _c in F.values()]
        fanout = [len(c) for _b, c in F.values()]
        leaves = sum(1 for _b, c in F.values() if not c)
        covered = set()
        for b, _c in F.values():
            covered.update(b)
        tot = sum(len(w) for _s, w in imgs[n].spans)
        h = hist(sizes, edges)
        rows[n] = dict(
            nfunc=len(F), insn=sum(sizes),
            median=sorted(sizes)[len(sizes) // 2],
            mean=sum(sizes) / len(sizes),
            leaves=leaves, leaf_pct=100.0 * leaves / len(sizes),
            fanout_mean=sum(fanout) / len(fanout),
            fanout_max=max(fanout),
            edges=sum(fanout),
            cover=100.0 * len(covered) / tot,
            hist={str(k): h[k] for k in edges + ["inf"]},
        )
        print(f"\n-- {n} --")
        for k, v in rows[n].items():
            if k == "hist":
                print("   size histogram (<=N insns): " + " ".join(
                    f"{kk}:{vv}" for kk, vv in v.items()))
            else:
                print(f"   {k:12s} {v if isinstance(v,int) else round(v,3)}")
    print("\n-- normalised SHAPE metrics (size-independent) --")
    print(f"{'metric':22s} " + " ".join(f"{n:>10s}" for n in BUILDS))
    for k in ("mean", "median", "leaf_pct", "fanout_mean", "cover"):
        print(f"{k:22s} " + " ".join(
            f"{rows[n][k]:10.3f}" for n in BUILDS))
    print("\n-- size-distribution SHAPE, as % of that build's functions --")
    ks = [str(e) for e in edges] + ["inf"]
    print(f"{'bucket':22s} " + " ".join(f"{n:>10s}" for n in BUILDS))
    for k in ks:
        print(f"<= {k:19s} " + " ".join(
            f"{100.0*rows[n]['hist'][k]/rows[n]['nfunc']:10.2f}"
            for n in BUILDS))
    return rows


def match(funcsA, funcsB, tol):
    """Greedy fingerprint match A->B. Returns (matched, unmatched_entries)."""
    fpB = {e: fingerprint(b, c) for e, (b, c) in funcsB.items()}
    buckets = collections.defaultdict(list)
    for e, f in fpB.items():
        buckets[f["n"]].append((e, f))
    matched, unmatched = 0, []
    used = set()
    for e, (b, c) in funcsA.items():
        fa = fingerprint(b, c)
        best = None
        for dn in range(0, max(2, int(fa["n"] * tol)) + 1):
            for nn in (fa["n"] - dn, fa["n"] + dn):
                for eb, fb in buckets.get(nn, ()):
                    if eb in used:
                        continue
                    d = fp_dist(fa, fb)
                    if d <= tol and (best is None or d < best[0]):
                        best = (d, eb)
            if best is not None and best[0] == 0.0:
                break
        if best is None:
            unmatched.append((e, fa))
        else:
            matched += 1
            used.add(best[1])
    return matched, unmatched


def stage_fingerprint(funcs, tols=(0.0, 0.02, 0.05, 0.10)):
    print("=== call-graph fingerprint matching ===")
    print("(A -> CV6T-AR;  unmatched = no function in AR with the same "
          "compile-invariant shape)")
    res = {}
    for tol in tols:
        line = {}
        for a in ("BV6T-AF", "CV6T-AH"):
            m, un = match(funcs[a], funcs["CV6T-AR"], tol)
            n = len(funcs[a])
            line[a] = (n - m, n, un)
        res[tol] = line
        print(f"\n-- tolerance {tol:.2f} --")
        for a, (u, n, _un) in line.items():
            print(f"   {a} -> CV6T-AR : {u}/{n} unmatched ({pct(u,n)})")
        ub = line["BV6T-AF"][0] / line["BV6T-AF"][1]
        uh = line["CV6T-AH"][0] / line["CV6T-AH"][1]
        print(f"   CONTROL DELTA (BV6T-excess over AH control): "
              f"{100*(ub-uh):+.1f} percentage points")
    return res


def stage_consts(imgs, funcs):
    print("=== constant-set comparison (opcode-filtered immediates) ===")
    S = {n: imm_set(imgs[n], funcs[n]) for n in BUILDS}
    C = {n: cmp_imm(imgs[n], funcs[n]) for n in BUILDS}
    for n in BUILDS:
        print(f"   {n}: {len(S[n])} distinct immediates "
              f"({sum(S[n].values())} sites); "
              f"{len(C[n])} distinct CMP immediates "
              f"({sum(C[n].values())} sites)")

    def rep(lbl, A, B, H):
        onlyA = set(A) - set(B)
        onlyH = set(H) - set(B)
        print(f"\n-- {lbl} --")
        print(f"   in A not in CV6T-AR : {len(onlyA)}")
        print(f"   in CV6T-AH not in AR (CONTROL): {len(onlyH)}")
        return onlyA, onlyH

    oB, oH = rep("all immediates", S["BV6T-AF"], S["CV6T-AR"], S["CV6T-AH"])
    print(f"   BV6T-only AND ALSO present in AH (survives control): "
          f"{len(oB & set(S['CV6T-AH']))}")
    cB, cH = rep("CMP immediates", C["BV6T-AF"], C["CV6T-AR"], C["CV6T-AH"])
    surv = cB & set(C["CV6T-AH"])
    print(f"   BV6T-only CMP consts ALSO in AH (survives control): "
          f"{len(surv)}  -> {sorted(hex(x) for x in surv)[:40]}")

    print("\n-- lane-assist candidate constants, site counts per build --")
    cands = [0x0006, 0x0002, 0x0004, 0x0070, 0x0007, 0x7004, 0x0003,
             0x0005, 0x0001, 0x7EDF, 0x7ED7, 0x8628, 0x863C]
    print(f"{'const':>8s} " + " ".join(f"{n:>10s}" for n in BUILDS)
          + "   |  CMP-only per build")
    for v in cands:
        print(f"  0x{v:04X} " + " ".join(f"{S[n][v]:10d}" for n in BUILDS)
              + "   |  " + " ".join(f"{C[n][v]:6d}" for n in BUILDS))
    return S, C


def stage_jtables(imgs, funcs, table):
    print("=== jump/branch table inventory ===")
    for n in BUILDS:
        code = set()
        for b, _c in funcs[n].values():
            code.update(b)
        entries = set(funcs[n])
        t16 = jump_tables(imgs[n], entries, minlen=4)
        tj = jmp_tables(imgs[n], table, funcs[n], minlen=3)
        sz16 = collections.Counter(t[2] for t in t16)
        szj = collections.Counter(t[1] for t in tj)
        print(f"\n-- {n} --")
        print(f"   addr16 tables (runs of function-entry low words, >=4): "
              f"{len(t16)}   size histogram {dict(sorted(sz16.items()))}")
        print(f"   JMP-arm tables (>=3 consecutive JMP abs19): {len(tj)}"
              f"   size histogram {dict(sorted(szj.items()))}")
        for lbl, cnt in (("addr16", sz16), ("JMParm", szj)):
            for k in (8, 7, 6):
                if cnt.get(k):
                    print(f"      {lbl} size {k}: {cnt[k]} table(s)")
    return None


def unmatched_set(funcsA, funcsB, tol):
    """Entries of A with no shape-match in B (greedy, one-to-one)."""
    _m, un = match(funcsA, funcsB, tol)
    return {e: f for e, f in un}


def stage_unique(funcs, tols=(0.0, 0.02, 0.05)):
    """A function is genuinely UNIQUE to build X only if it has no shape
    counterpart in EITHER of the other two builds.  Cross-family drift alone
    cannot produce that, because AH and AR are different compilations."""
    print("=== build-unique functions (unmatched in BOTH other builds) ===")
    names = list(BUILDS)
    for tol in tols:
        print(f"\n-- tolerance {tol:.2f} --")
        for x in names:
            others = [y for y in names if y != x]
            u0 = unmatched_set(funcs[x], funcs[others[0]], tol)
            u1 = unmatched_set(funcs[x], funcs[others[1]], tol)
            both = set(u0) & set(u1)
            n = len(funcs[x])
            big = sorted((e for e in both if len(funcs[x][e][0]) >= 40),
                         key=lambda e: -len(funcs[x][e][0]))
            huge = [e for e in big if len(funcs[x][e][0]) >= 80]
            print(f"   {x:8s} unique={len(both):4d}/{n} ({pct(len(both),n)})"
                  f"  >=40 insns: {len(big):3d}   >=80 insns: {len(huge):3d}"
                  f"   [vs {others[0]} & {others[1]}]")
            if tol == 0.05 and big:
                for e in big[:12]:
                    b, c = funcs[x][e]
                    f = fingerprint(b, c)
                    print(f"        P:${e:05X}  n={f['n']:4d} cond={f['cond']:3d}"
                          f" call={f['call']:3d} loop={f['loop']:2d}"
                          f" mul={f['mul']:3d} callees={f['callees']:3d}")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all")
    a = ap.parse_args()
    table, imgs, funcs = load_all()
    if a.stage in ("all", "inventory"):
        stage_inventory(imgs, funcs)
        print()
    if a.stage in ("all", "fingerprint"):
        stage_fingerprint(funcs)
        print()
    if a.stage in ("all", "unique"):
        stage_unique(funcs)
        print()
    if a.stage in ("all", "consts"):
        stage_consts(imgs, funcs)
        print()
    if a.stage in ("all", "jtables"):
        stage_jtables(imgs, funcs, table)


if __name__ == "__main__":
    main()
