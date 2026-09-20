#!/usr/bin/env python3
"""Stage 2b: solve the two 0x0A5 lane-assist checksums from a captured corpus.

The DBC states exactly what each checksum covers (CM_ SG_ 165):

    LaActvStats_No_Cs  (47|8 -> byte 5)  protects
        LdwActvIntns_D_Req  (9|2   -> byte 1 bits 1..0)
        LdwActvStats_D_Req  (14|3  -> byte 1 bits 6..4)
        LkaActvStats_D_Req  (30|3  -> byte 3 bits 6..4)   <-- the LKA/LCA field
    LaActvReq_No_RollCnt (55|4 -> byte 6 bits 7..4) counts the same group

    LaStePar_No_Cs     (23|8 -> byte 2)  protects
        LaRampType_B_Req    (31|1  -> byte 3 bit 7)
        LaRefAng_No_Req     (27|12 -> byte 3 bits 3..0 + byte 4)
        LaCurvature_No_Calc (51|12 -> byte 6 bits 3..0 + byte 7)

Note the two groups INTERLEAVE inside bytes 3 and 6, so a byte-wise checksum
must either cover a whole byte (including the other group's bits) or operate
on extracted signal VALUES.  Both families are searched.

Hypothesis space (all combinations, scored against every frame):
    op        : sum | xor  of the selected terms
    terms     : a byte subset, or the DBC-specified signal values
    sign      : +op or -op
    constant  : 0..255
    extras    : optionally include the CAN ID and/or the rolling counter
  ->  CS == (sign*op(terms) + k) & 0xFF

`0xFF - sum`, `0x100 - sum`, `sum + k`, `xor ^ k` etc. are all inside this
space.  Every hypothesis that fits ALL frames is reported; if several fit, the
corpus is too uniform to discriminate and that is stated rather than hidden.

Usage:
    python3 solve_checksum.py corpus.jsonl --stats
    python3 solve_checksum.py corpus.jsonl
    python3 solve_checksum.py --selftest
"""
import argparse
import itertools
import json
import sys
from collections import Counter

CS_A = 5      # LaActvStats_No_Cs  byte
CS_B = 2      # LaStePar_No_Cs     byte

GROUP_A_BYTES = [1, 3, 6]          # + the rolling counter lives in byte 6
GROUP_B_BYTES = [3, 4, 6, 7]


def be_extract(data, start, length):
    val = 0
    b, bit = start // 8, start % 8
    for _ in range(length):
        if b >= len(data):
            return None
        val = (val << 1) | ((data[b] >> bit) & 1)
        bit -= 1
        if bit < 0:
            bit, b = 7, b + 1
    return val


SIGS_A = {"LdwActvIntns_D_Req": (9, 2), "LdwActvStats_D_Req": (14, 3),
          "LkaActvStats_D_Req": (30, 3)}
SIGS_B = {"LaRampType_B_Req": (31, 1), "LaRefAng_No_Req": (27, 12),
          "LaCurvature_No_Calc": (51, 12)}
ROLLCNT = (55, 4)


def load(path):
    out = []
    for ln in open(path):
        ln = ln.strip()
        if not ln:
            continue
        r = json.loads(ln)
        if r["id"] == 0x0A5 and len(r["d"]) == 16:
            out.append(bytes.fromhex(r["d"]))
    return out


def terms_for(frame, byteset, sigs, use_roll, use_id):
    t = [frame[i] for i in byteset]
    if sigs:
        t = [be_extract(frame, s, l) for s, l in sigs.values()]
    if use_roll:
        t.append(be_extract(frame, *ROLLCNT))
    if use_id:
        t.append(0xA5)
    return t


def search(frames, cs_byte, cand_bytes, sigs):
    """Return every (desc, fn) consistent with all frames."""
    hits = []
    subsets = []
    for r in range(1, len(cand_bytes) + 1):
        subsets += [list(c) for c in itertools.combinations(cand_bytes, r)]
    modes = [("bytes", s, None) for s in subsets] + [("signals", [], sigs)]

    for mode, bs, sg in modes:
        for use_roll in (False, True):
            for use_id in (False, True):
                for opname in ("sum", "xor"):
                    for sign in (1, -1):
                        # solve k from the first frame, then verify the rest
                        f0 = frames[0]
                        t0 = terms_for(f0, bs, sg, use_roll, use_id)
                        if any(x is None for x in t0):
                            continue
                        v0 = sum(t0) if opname == "sum" else \
                            __import__("functools").reduce(lambda a, b: a ^ b, t0)
                        k = (f0[cs_byte] - sign * v0) & 0xFF
                        good = True
                        for fr in frames:
                            t = terms_for(fr, bs, sg, use_roll, use_id)
                            v = sum(t) if opname == "sum" else \
                                __import__("functools").reduce(
                                    lambda a, b: a ^ b, t)
                            if ((sign * v + k) & 0xFF) != fr[cs_byte]:
                                good = False
                                break
                        if good:
                            d = (f"{'-' if sign < 0 else ''}{opname}("
                                 + (f"bytes {bs}" if mode == "bytes"
                                    else "signal values")
                                 + (" + rollcnt" if use_roll else "")
                                 + (" + id" if use_id else "")
                                 + f") + {k:#04x}")
                            hits.append(d)
    return hits


def stats(frames):
    print(f"corpus: {len(frames)} 0x0A5 frames")
    if not frames:
        return
    print("\nper-byte distinct values (checksum needs its inputs to VARY):")
    for i in range(8):
        vals = Counter(f[i] for f in frames)
        flag = "  <-- CONSTANT, constrains nothing" if len(vals) == 1 else ""
        print(f"  byte {i}: {len(vals):>4} distinct{flag}")
    print("\nprotected signal variability:")
    for nm, (s, l) in list(SIGS_A.items()) + list(SIGS_B.items()):
        v = Counter(be_extract(f, s, l) for f in frames)
        print(f"  {nm:<22} {len(v):>4} distinct  {dict(list(v.most_common(4)))}")
    r = Counter(be_extract(f, *ROLLCNT) for f in frames)
    print(f"  {'LaActvReq_No_RollCnt':<22} {len(r):>4} distinct "
          f"(expect 16 if it wraps)")
    lk = Counter(be_extract(f, 30, 3) for f in frames)
    print(f"\nLkaActvStats_D_Req values seen: {dict(lk)}")
    if set(lk) <= {0}:
        print("  -> only 'LKA Idle'.  The camera never engaged; this corpus "
              "cannot\n     show what an intervention looks like.")


def selftest():
    ok = True

    def chk(n, cond):
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL'}  {n}")

    import random
    rnd = random.Random(1234)

    # Build synthetic frames with a KNOWN rule, then check the solver finds it.
    def make(rule_bytes, k, sign, op):
        fr = bytearray(rnd.randrange(256) for _ in range(8))
        fr[CS_A] = 0
        t = [fr[i] for i in rule_bytes]
        v = sum(t) if op == "sum" else \
            __import__("functools").reduce(lambda a, b: a ^ b, t)
        fr[CS_A] = (sign * v + k) & 0xFF
        return bytes(fr)

    for rule, k, sign, op in (([1, 3, 6], 0x00, 1, "sum"),
                              ([1, 3, 6], 0xFF, -1, "sum"),
                              ([1, 3], 0x5A, 1, "xor")):
        frames = [make(rule, k, sign, op) for _ in range(300)]
        hits = search(frames, CS_A, GROUP_A_BYTES, SIGS_A)
        want = (f"{'-' if sign < 0 else ''}{op}(bytes {rule}) + {k:#04x}")
        chk(f"recovers {want}", want in hits)

    # A corpus with a CONSTANT payload must be reported as under-determined,
    # not silently "solved".
    same = [make([1, 3, 6], 0, 1, "sum")] * 50
    h = search(same, CS_A, GROUP_A_BYTES, SIGS_A)
    chk("constant corpus yields many ambiguous fits (>5)", len(h) > 5)

    # Wrong data must yield NO fit.
    bad = [bytes(rnd.randrange(256) for _ in range(8)) for _ in range(300)]
    chk("random corpus yields no fit", search(bad, CS_A, GROUP_A_BYTES,
                                              SIGS_A) == [])

    chk("be_extract LkaActvStats geometry",
        be_extract(bytes([0, 0, 0, 0x70, 0, 0, 0, 0]), 30, 3) == 7)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", nargs="?")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.corpus:
        sys.exit(0 if selftest() else 1)

    frames = load(a.corpus)
    if not frames:
        print("no 0x0A5 frames in corpus", file=sys.stderr)
        sys.exit(2)
    stats(frames)
    if a.stats:
        return

    for label, cs, cand, sigs in (
            ("LaActvStats_No_Cs (LKA/LCA group)", CS_A, GROUP_A_BYTES, SIGS_A),
            ("LaStePar_No_Cs (steering group)", CS_B, GROUP_B_BYTES, SIGS_B)):
        print(f"\n=== {label}, checksum in byte {cs} ===")
        hits = search(frames, cs, cand, sigs)
        if not hits:
            print("  NO hypothesis fits.  The algorithm is outside the searched"
                  "\n  space (e.g. a CRC or a lookup table). Not guessing.")
        elif len(hits) == 1:
            print(f"  SOLVED: {hits[0]}")
        else:
            print(f"  {len(hits)} hypotheses fit -- corpus too uniform to "
                  f"discriminate:")
            for h in hits[:12]:
                print(f"     {h}")
            print("  Capture more VARIED frames (see capture_lane.py --advice)")


if __name__ == "__main__":
    main()
