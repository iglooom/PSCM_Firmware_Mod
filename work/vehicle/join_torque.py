#!/usr/bin/env python3
"""Join a DID watch log to a candump lane-state capture on ABSOLUTE time.

Both logs carry unix epoch seconds, so the join is exact -- no correlation, no
guessed offset. (The first attempt at this measurement logged relative time and
could not be aligned at all: cross-correlating the same physical sensor across
the two logs gave |r| < 0.04 at every lag, and the positive control failed.)

Usage:
    python3 work/vehicle/join_torque.py torque.csv lanetest.log
    python3 work/vehicle/join_torque.py --selftest
"""
import bisect
import collections
import os
import re
import statistics
import sys

LINE = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")

STATE = {0: "idle", 1: "LKAidle/LCAsup", 2: "LKA-LEFT", 3: "state3",
         4: "LKA-RIGHT", 5: "suppr-R", 6: "LCA", 7: "suppr-L+R"}
ACTIVE = (2, 4, 6)


def s8(h):
    """signed 8-bit from a hex byte string"""
    if not h:
        return None
    v = int(h, 16)
    return v - 256 if v > 127 else v


def load_lane(path):
    """-> sorted [(epoch, state)] from 0x0A5 byte3 bits 6..4"""
    out = []
    with open(path, errors="replace") as f:
        for ln in f:
            m = LINE.match(ln)
            if not m or int(m.group(2), 16) != 0x0A5:
                continue
            d = bytes.fromhex(m.group(3))
            if len(d) == 8:
                out.append((float(m.group(1)), (d[3] >> 4) & 0x07))
    out.sort()
    return out


def load_dids(path):
    """-> (fieldnames, [row dicts]) with float epoch"""
    rows = []
    hdr = None
    with open(path) as f:
        for ln in f:
            if ln.startswith("#"):
                continue
            parts = ln.rstrip("\n").split(",")
            if hdr is None:
                hdr = parts
                continue
            if len(parts) == len(hdr):
                rows.append(dict(zip(hdr, parts)))
    return hdr, rows


def join(lane, rows):
    lt = [t for t, _ in lane]
    ls = [s for _, s in lane]
    out = []
    for r in rows:
        e = float(r.get("epoch", r.get("t", "nan")))
        i = bisect.bisect_right(lt, e) - 1
        if 0 <= i < len(ls):
            out.append((e, ls[i], r))
    return out


def report(joined, field="FD0E"):
    by = collections.defaultdict(list)
    for e, st, r in joined:
        v = s8(r.get(field, ""))
        if v is not None:
            by[st].append(v)
    print("\n%s BY LANE STATE" % field)
    print("=" * 66)
    print("%-17s%7s%9s%9s%9s%9s" % ("state", "n", "mean", "|mean|", "max", "nonzero"))
    for st in sorted(by):
        v = by[st]
        a = [abs(x) for x in v]
        nz = 100.0 * sum(1 for x in v if x != 0) / len(v)
        print("%-17s%7d%9.2f%9.2f%9d%8.1f%%"
              % (STATE.get(st, st), len(v), statistics.mean(v),
                 statistics.mean(a), max(a), nz))
    return by


def verdict(by):
    """The positive control decides whether the run is interpretable."""
    lka = [x for st in (2, 4) for x in by.get(st, [])]
    lca = by.get(6, [])
    idle = by.get(0, []) + by.get(7, [])
    if not lka or not lca:
        print("\nINCONCLUSIVE: need both LKA and LCA episodes "
              "(LKA n=%d, LCA n=%d)" % (len(lka), len(lca)))
        return
    f = lambda v: 100.0 * sum(1 for x in v if x != 0) / len(v)
    print("\nCONTROL CHECK")
    print("   LKA  active %5.1f%%  (n=%d)" % (f(lka), len(lka)))
    print("   LCA  active %5.1f%%  (n=%d)" % (f(lca), len(lca)))
    if idle:
        print("   idle active %5.1f%%  (n=%d)   <- null control"
              % (f(idle), len(idle)))
    if f(lka) <= f(idle or [1]):
        print("\n*** POSITIVE CONTROL FAILED -- LKA is not above idle.")
        print("    The measurement is invalid; do not read the LCA number.")
    else:
        print("\nPositive control OK: LKA is above idle, so the signal is real.")
        print("   -> LCA at %.1f%% is therefore interpretable." % f(lca))


def selftest():
    ok = True

    def chk(n, c, d=""):
        nonlocal ok
        ok = ok and bool(c)
        print("  %s  %s%s" % ("PASS" if c else "FAIL", n, ("  " + d) if d else ""))

    print("join_torque.py selftest\n")
    chk("s8 decodes negatives", s8("FF") == -1 and s8("80") == -128)
    chk("s8 decodes positives", s8("7F") == 127 and s8("00") == 0)
    chk("s8 handles blanks", s8("") is None)

    lane = [(1000.0, 7), (1001.0, 2), (1002.0, 6), (1003.0, 7)]
    rows = [{"epoch": "1000.5", "FD0E": "00"},
            {"epoch": "1001.5", "FD0E": "20"},
            {"epoch": "1002.5", "FD0E": "10"},
            {"epoch": "1003.5", "FD0E": "00"}]
    j = join(lane, rows)
    chk("join maps each row to the state in force", len(j) == 4)
    chk("row at 1001.5 -> LKA-LEFT", j[1][1] == 2, str(j[1][1]))
    chk("row at 1002.5 -> LCA", j[2][1] == 6, str(j[2][1]))
    chk("row before any state change still joins", j[0][1] == 7)

    # a row EARLIER than the first lane sample must be dropped, not mis-joined
    j2 = join(lane, [{"epoch": "999.0", "FD0E": "7F"}])
    chk("rows before the lane log are dropped", len(j2) == 0, str(len(j2)))

    # relative timestamps must NOT silently produce a bogus join
    j3 = join(lane, [{"epoch": "0.5", "FD0E": "7F"}])
    chk("relative timestamps join to nothing (caught, not silent)",
        len(j3) == 0)

    print("\n" + "=" * 56)
    print("SELFTEST:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    csvp, logp = sys.argv[1], sys.argv[2]
    lane = load_lane(logp)
    hdr, rows = load_dids(csvp)
    print("lane samples: %d   DID sweeps: %d" % (len(lane), len(rows)))
    if not rows:
        print("no DID rows")
        return 1
    if "epoch" not in hdr:
        print("\n*** %s has no 'epoch' column -- it was written by the old\n"
              "    relative-time version and CANNOT be joined. Re-run the\n"
              "    capture with the current dump_dids.py." % csvp)
        return 1
    e0, e1 = float(rows[0]["epoch"]), float(rows[-1]["epoch"])
    print("DID epoch range : %.1f .. %.1f" % (e0, e1))
    print("lane epoch range: %.1f .. %.1f" % (lane[0][0], lane[-1][0]))
    overlap = min(e1, lane[-1][0]) - max(e0, lane[0][0])
    print("overlap: %.1f s" % overlap)
    if overlap <= 0:
        print("\n*** NO OVERLAP -- the two captures do not cover the same time.")
        return 1
    joined = join(lane, rows)
    print("joined rows: %d" % len(joined))
    by = report(joined, "FD0E")
    report(joined, "FD0C")
    verdict(by)
    return 0


if __name__ == "__main__":
    sys.exit(main())
