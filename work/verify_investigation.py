#!/usr/bin/env python3
"""Verify PSCM_LCA_investigation.md against the artefacts it cites.

Every load-bearing number in that document is re-derived here from the binaries
and logs. Run after any edit. A doc that cannot be re-derived is a doc that has
drifted.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DOC = os.path.join(ROOT, "PSCM_LCA_investigation.md")

ok = True


def chk(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", name,
                          ("  " + detail) if detail else ""))


def load(build):
    spec = {
        "BV6T": [("bins/BV6T-14C217-AF/BV6T-14C217-AF_blk0_0x00000000.bin", 0),
                 ("bins/BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin", 0xE000)],
        "CV6T": [("bins/CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin", 0),
                 ("bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin", 0xE000)],
    }[build]
    spans = []
    for rel, base in spec:
        d = open(os.path.join(ROOT, rel), "rb").read()
        spans.append((base, list(struct.unpack("<%dH" % (len(d) // 2), d))))
    return spans


def W(spans, a):
    for base, arr in spans:
        if base <= a < base + len(arr):
            return arr[a - base]
    return None


def main():
    if not os.path.exists(DOC):
        print("missing %s" % DOC)
        return 1
    md = open(DOC).read()
    cv, bv = load("CV6T"), load("BV6T")

    print("verify_investigation.py\n")
    print("--- 2.1 the X:$0904 dispatcher gate ---")
    seq = [W(cv, 0x2A77E + i) for i in range(8)]
    chk("CV6T P:$2A77E = 4C06 A207 F07C 0904 4C01 A203 E684 2DDE",
        seq == [0x4C06, 0xA207, 0xF07C, 0x0904, 0x4C01, 0xA203, 0xE684, 0x2DDE],
        " ".join("%04X" % x for x in seq))
    bseq = [W(bv, 0x2691D + i) for i in range(4)]
    chk("BV6T P:$2691D lacks the insert (4C06 A203 E684 ...)",
        bseq[0] == 0x4C06 and bseq[1] == 0xA203 and bseq[2] == 0xE684,
        " ".join("%04X" % x for x in bseq))

    vbf = os.path.join(ROOT, "CV6T-14C217-AR_LCA.VBF")
    if os.path.exists(vbf):
        raw = open(vbf, "rb").read()
        pat = bytes.fromhex("064c07a200e700e700e700e784e6de2d")
        i = raw.find(pat)
        chk("patched VBF contains the NOP form at 0x042BA0",
            i == 0x042BA0, "found at 0x%06X" % i if i >= 0 else "NOT FOUND")
        chk("doc cites offset 0x042BA0", "0x042BA0" in md)

    print("\n--- 2.2 the X:$2DC1 / X:$23C7 consumer gate ---")
    for build, spans, cell, lane in (("CV6T", cv, 0x2DC1, 0x2DDE),
                                     ("BV6T", bv, 0x23C7, 0x23E4)):
        rd = wr = 0
        for base, arr in spans:
            for i in range(1, len(arr)):
                if arr[i] != cell:
                    continue
                op = arr[i - 1]
                if op in (0xF07C, 0xFF7C):
                    rd += 1
                elif (op & 0xFF80) == 0xE680:
                    wr += 1
        chk("%s has 4 reads of the gate cell" % build, rd == 4, str(rd))
        if build == "CV6T":
            chk("CV6T writes it exactly once", wr == 1, str(wr))
        else:
            chk("BV6T writes it 3 times (can disable at runtime)", wr == 3,
                str(wr))
    chk("doc records the offset -0x1D relationship", "-0x1D" in md)
    chk("doc corrects the CV6T-only claim", "Correction to an earlier claim" in md)

    print("\n--- 2.3 per-state codes ---")
    sites = []
    for base, arr in cv:
        for i in range(len(arr) - 3):
            if arr[i] == 0xF07C and arr[i + 1] == 0x2DDE \
                    and (arr[i + 2] & 0xFF00) == 0x4C00:
                sites.append((base + i, arr[i + 2] & 0xFF))
    lca = [a for a, k in sites if k == 4]
    chk("4 LCA consumer sites test lane state == 4", len(lca) == 4, str(len(lca)))
    for a in (0x2ABF7, 0x2AC76, 0x2ACAE, 0x2ACEA):
        chk("  site P:$%05X present" % a, a in lca)

    print("\n--- 2.4 the torque enable and function ---")
    te = [W(cv, 0x2B0B4 + i) for i in range(11)]
    chk("P:$2B0B4 reads X:$2DB9", te[0] == 0xF07C and te[1] == 0x2DB9)
    chk("  accepts #2 (LKA)", te[2] == 0x4C02)
    chk("  accepts #5 (LCA sustained)", te[5] == 0x4C05)
    chk("  sets Y0=1 then Y0=0", te[8] == 0xE581 and te[10] == 0xE580)

    a_cv = [W(cv, 0x2B07C + i) for i in range(107)]
    a_bv = [W(bv, 0x27476 + i) for i in range(107)]
    diff = [i for i, (x, y) in enumerate(zip(a_cv, a_bv)) if x != y]
    chk("torque function is 107 words in both", None not in a_cv + a_bv)
    chk("exactly 14 words differ", len(diff) == 14, str(len(diff)))
    same_op = all(a_cv[i - 1] == a_bv[i - 1] for i in diff if i > 0)
    chk("every differing word follows an IDENTICAL opcode", same_op)

    print("\n--- 2.5 branch polarity ---")
    def tgt(spans, pc):
        off = W(spans, pc) & 0x7F
        return pc + 1 + (off - 128 if off > 63 else off)
    chk("A303 @P:$2B0B7 -> P:$2B0BB (into Y0=1)", tgt(cv, 0x2B0B7) == 0x2B0BB,
        "P:$%05X" % tgt(cv, 0x2B0B7))
    chk("A203 @P:$2B0BA -> P:$2B0BE (into Y0=0)", tgt(cv, 0x2B0BA) == 0x2B0BE,
        "P:$%05X" % tgt(cv, 0x2B0BA))
    for pc in (0x2AC75, 0x2AC79, 0x2AC7D):
        chk("  branch @P:$%05X lands ON the write P:$2AC81" % pc,
            tgt(cv, pc) == 0x2AC81, "P:$%05X" % tgt(cv, pc))
    chk("doc states A3xx=Beq, A2xx=Bne", "A3xx = Beq" in md and "A2xx = Bne" in md)
    chk("doc corrects the Y0/B precondition claim", "CMP.W #$03,B` was a misreading" in md
        or "was a misreading" in md)

    print("\n--- 2.6 the DID table ---")
    n = 0
    a = 0x0EDC4
    while True:
        did = W(cv, a)
        if did is None or not (0xD000 <= did <= 0xFFFF):
            break
        n += 1
        a += 6
    chk("DID table at P:$0EDC4 has 40 entries", n == 40, str(n))
    chk("doc cites the descriptor-index stride of 3", "step by 3" in md)

    print("\n--- 3.1 the second drive's failed control ---")
    for s in ("0.9 %", "31.6 %", "16.7 %", "8.3 %"):
        chk("doc reports %s" % s, s in md)
    chk("doc states the positive control failed",
        "positive control failed" in md.lower() and "below idle" in md)
    chk("doc records the pooling flaw in join_torque.py",
        "pooled both LKA directions" in md)

    print("\n--- honesty checks ---")
    chk("doc states the question is UNRESOLVED", "UNRESOLVED" in md)
    chk("doc lists what remains untested", "What remains untested" in md)
    chk("doc gives two readings, not one conclusion",
        "Two readings remain consistent" in md)
    chk("doc does NOT claim LCA is fixed",
        "LCA now works" not in md and "problem solved" not in md.lower())

    print("\n" + "=" * 60)
    print("VERIFY:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
