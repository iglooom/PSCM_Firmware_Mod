#!/usr/bin/env python3
"""Solve the 0x0A5 checksums from a corpus where ONLY the rolling counter varies.

The captured corpus (stationary, camera suppressed) has every protected signal
constant except LaActvReq_No_RollCnt.  That is a much weaker constraint than a
varied corpus -- but it is not useless, and it already forces one structural
conclusion:

    LaStePar_No_Cs (byte 2) protects LaRampType / LaRefAng / LaCurvature,
    ALL of which are constant in the corpus -- yet byte 2 takes 16 distinct
    values, one per rolling-counter value.

    => byte 2's checksum input must include the rolling counter.
       The only way that happens without contradicting the DBC is a BYTE-WISE
       checksum that covers byte 6 whole (LaCurvature's high nibble lives
       there, and the rolling counter shares that byte).

So the scheme is byte-wise, not signal-value-wise.  This tool verifies that by
measuring the SLOPE of checksum vs rolling counter:

    slope 0x10 per rollcnt step -> byte-wise  (rollcnt enters as byte6 = cnt<<4)
    slope 0x01 per rollcnt step -> signal-wise (rollcnt enters as its value)

and then enumerates the byte subsets consistent with the data, emitting ready
-to-use --cs-rule strings for ipma_spoof.py.

LIMITATION, stated plainly: byte 3 is CONSTANT in this corpus, and byte 3 is
exactly the byte the experiment changes (LkaActvStats_D_Req).  No corpus with a
constant byte 3 can prove how byte 3 enters the checksum.  The candidates are
therefore PREDICTIONS, to be validated on the car by the LKA positive control.

Usage:
    python3 solve_rollcnt_cs.py corpus.jsonl
    python3 solve_rollcnt_cs.py --selftest
"""
import argparse
import json
import sys
from collections import defaultdict

CS_A, CS_B = 5, 2          # checksum byte positions
ROLL = (55, 4)             # LaActvReq_No_RollCnt


def be_extract(d, start, length):
    v, b, bit = 0, start // 8, start % 8
    for _ in range(length):
        v = (v << 1) | ((d[b] >> bit) & 1)
        bit -= 1
        if bit < 0:
            bit, b = 7, b + 1
    return v


def load(path):
    out = []
    for ln in open(path):
        ln = ln.strip()
        if not ln:
            continue
        r = json.loads(ln)
        if r.get("id") == 0x0A5 and len(r.get("d", "")) == 16:
            out.append(bytes.fromhex(r["d"]))
    return out


def rollcnt_map(frames, cs_byte):
    """rollcnt -> set of checksum values seen."""
    m = defaultdict(set)
    for f in frames:
        m[be_extract(f, *ROLL)].add(f[cs_byte])
    return m


def slope_of(m):
    """If cs is linear in rollcnt mod 256, return the step; else None."""
    if len(m) < 3 or any(len(v) != 1 for v in m.values()):
        return None
    pts = sorted((k, next(iter(v))) for k, v in m.items())
    step = (pts[1][1] - pts[0][1]) & 0xFF
    for (k0, c0), (k1, c1) in zip(pts, pts[1:]):
        if ((c1 - c0) & 0xFF) != (step * (k1 - k0)) & 0xFF:
            return None
    return step


def terms_bytes(f, sub):
    return [f[i] for i in sub]


def terms_nibbles(f, sub):
    """Each selected byte contributes its two nibbles separately."""
    t = []
    for i in sub:
        t.append(f[i] >> 4)
        t.append(f[i] & 0xF)
    return t


def candidates(frames, cs_byte, slope=None):
    """Byte-sum AND nibble-sum hypotheses that reproduce every checksum.

    Two term families are needed because the observed slopes differ:
      slope -0x10 per rollcnt -> the counter enters as the whole byte 6
                                 (BYTE-wise sum)
      slope -0x01 per rollcnt -> the counter enters as a nibble of byte 6
                                 (NIBBLE-wise sum)
    Both are standard automotive schemes; the data picks between them.
    """
    import itertools
    f0 = frames[0]
    others = [i for i in range(8) if i != cs_byte]
    out = []
    for kind, fn in (("sum", terms_bytes), ("nib", terms_nibbles)):
        for r in range(1, len(others) + 1):
            for sub in itertools.combinations(others, r):
                for sign in (1, -1):
                    k = (f0[cs_byte] - sign * sum(fn(f0, sub))) & 0xFF
                    if all(((sign * sum(fn(f, sub)) + k) & 0xFF) == f[cs_byte]
                           for f in frames):
                        out.append((kind, list(sub), sign, k))
    return out


def report(frames):
    print(f"corpus: {len(frames)} 0x0A5 frames\n")
    varying = [i for i in range(8)
               if len({f[i] for f in frames}) > 1]
    print(f"varying bytes: {varying}")
    const = {i: frames[0][i] for i in range(8) if i not in varying}
    print("constant bytes: "
          + ", ".join(f"{i}={v:#04x}" for i, v in sorted(const.items())))

    for label, cs in (("LaActvStats_No_Cs (group A, LKA/LCA)", CS_A),
                      ("LaStePar_No_Cs (group B, steering)", CS_B)):
        print(f"\n=== {label} -- byte {cs} ===")
        m = rollcnt_map(frames, cs)
        if any(len(v) != 1 for v in m.values()):
            bad = {k: sorted(v) for k, v in m.items() if len(v) != 1}
            print(f"  NOT a pure function of rollcnt: {list(bad)[:4]}")
            print("  (payload must vary somewhere else too)")
            continue
        pts = sorted((k, next(iter(v))) for k, v in m.items())
        print("  rollcnt -> checksum: "
              + " ".join(f"{k:X}:{c:02X}" for k, c in pts))
        s = slope_of(m)
        if s is None:
            print("  not linear in rollcnt -> not a simple additive scheme")
        else:
            kind = ("BYTE-wise (counter enters as byte6 = cnt<<4)"
                    if s in (0x10, 0xF0) else
                    "NIBBLE-wise (counter enters as one nibble)"
                    if s in (0x01, 0xFF) else "linear")
            sign = "negated" if s in (0xF0, 0xFF) else "additive"
            print(f"  linear, step {s:#04x} per rollcnt -> {kind}, {sign}")
        cands = candidates(frames, cs)
        print(f"  {len(cands)} hypotheses fit this corpus")
        with3 = [c for c in cands if 3 in c[1]]
        print(f"  ...of which {len(with3)} include byte 3 "
              f"(the LkaActvStats byte, which the DBC says this checksum "
              f"protects)" if cs == CS_A else
              f"  ...of which {len(with3)} include byte 3")
        for kind, sub, sign, k in with3[:8]:
            rule = f"{kind}:{','.join(str(x) for x in sub)}:" \
                   f"{'+' if sign > 0 else '-'}:{k:#04x}"
            print(f"     --cs-rule \"{rule}\"")

    print("\n" + "=" * 68)
    print("LIMITATION: byte 3 is CONSTANT here, and byte 3 is what the LKA/LCA")
    print("experiment changes.  These rules are PREDICTIONS; the LKA positive")
    print("control on the car is what validates them.")


def selftest():
    ok = True

    def chk(n, cond):
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL'}  {n}")

    # Synthesise the observed situation: only rollcnt varies, byte-wise sum.
    def mk(cnt, b3=0x28, rule=(1, 3, 4, 6, 7)):
        f = bytearray([0x00, 0x2C, 0x00, b3, 0x00, 0x00, (cnt << 4) | 8, 0x00])
        f[CS_A] = (sum(f[i] for i in rule)) & 0xFF
        return bytes(f)

    frames = [mk(i) for i in range(16)] * 50
    m = rollcnt_map(frames, CS_A)
    chk("checksum is a function of rollcnt", all(len(v) == 1
                                                 for v in m.values()))
    chk("slope is 0x10 (byte-wise)", slope_of(m) == 0x10)
    c = candidates(frames, CS_A)
    chk("true rule is among the candidates",
        any(set(s) == {1, 3, 4, 6, 7} and sg == 1 for _kd, s, sg, _k in c))
    chk("corpus with constant byte3 leaves >1 candidate", len(c) > 1)

    # signal-wise variant: rollcnt contributes its value, slope 1
    def mk2(cnt):
        f = bytearray([0, 0x2C, 0, 0x28, 0, 0, (cnt << 4) | 8, 0])
        f[CS_A] = (cnt + 0x11) & 0xFF
        return bytes(f)
    m2 = rollcnt_map([mk2(i) for i in range(16)], CS_A)
    chk("slope 0x01 detected for signal-wise", slope_of(m2) == 0x01)

    # non-linear (CRC-like) must be reported as such
    def mk3(cnt):
        f = bytearray([0, 0x2C, 0, 0x28, 0, 0, (cnt << 4) | 8, 0])
        f[CS_A] = ((cnt * cnt * 7 + 3) ^ 0x5A) & 0xFF
        return bytes(f)
    chk("non-linear detected", slope_of(rollcnt_map([mk3(i) for i in range(16)],
                                                    CS_A)) is None)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.corpus:
        sys.exit(0 if selftest() else 1)
    frames = load(a.corpus)
    if not frames:
        print("no 0x0A5 frames", file=sys.stderr)
        sys.exit(2)
    report(frames)


if __name__ == "__main__":
    main()
