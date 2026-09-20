#!/usr/bin/env python3
"""Determine empirically which CAN IDs the IPMA sends, from a silence window.

Method: a capture that runs (a) with the camera working, (b) with the camera
held in programmingSession, (c) working again.  Any ID that is present in the
before/after phases and ABSENT during the silence window is transmitted by the
IPMA.  This is ground truth and beats reading the DBC's sender column, which
describes the network design rather than this vehicle's actual configuration.

The diagnostic exchange on 0x706/0x70E marks the phase boundaries: the first
0x706 request starts the silence, the hardReset (11 01) or the last 0x706
ends it.

Usage:
    python3 find_ipma_ids.py candump-....log
    python3 find_ipma_ids.py --selftest
"""
import argparse
import re
import sys
from collections import defaultdict

LINE = re.compile(r"\((\d+\.\d+)\)\s+(\S+)\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")
REQ_ID, RESP_ID = 0x706, 0x70E


def parse(path):
    out = []
    for ln in open(path, errors="replace"):
        m = LINE.match(ln)
        if m:
            out.append((float(m.group(1)), int(m.group(3), 16),
                        m.group(4).upper()))
    return out


def phases(rows):
    """-> (t_start, t_silence_begin, t_silence_end, t_end) or None."""
    diag = [(t, d) for t, i, d in rows if i == REQ_ID]
    if not diag:
        return None
    begin = diag[0][0]
    # silence ends at the hardReset (11 01) if present, else the last request
    end = None
    for t, d in diag:
        if d.startswith("0211 01".replace(" ", "")) or d.startswith("021101"):
            end = t
            break
    if end is None:
        end = diag[-1][0]
    return rows[0][0], begin, end, rows[-1][0]


def analyse(path, guard=1.0):
    rows = parse(path)
    if not rows:
        print("no frames parsed", file=sys.stderr)
        return 2
    ph = phases(rows)
    if not ph:
        print("no 0x706 diagnostic traffic found -- cannot locate the "
              "silence window", file=sys.stderr)
        return 3
    t0, tb, te, t1 = ph
    print(f"capture spans {t1 - t0:.1f}s, {len(rows)} frames")
    print(f"  before  : {t0 - t0:6.1f} .. {tb - t0:6.1f}s")
    print(f"  SILENCED: {tb - t0:6.1f} .. {te - t0:6.1f}s "
          f"({te - tb:.1f}s)")
    print(f"  after   : {te - t0:6.1f} .. {t1 - t0:6.1f}s\n")

    # guard bands: ignore frames right at the boundaries
    before, during, after = defaultdict(int), defaultdict(int), defaultdict(int)
    for t, cid, _d in rows:
        if cid in (REQ_ID, RESP_ID):
            continue
        if t < tb - guard:
            before[cid] += 1
        elif tb + guard < t < te - guard:
            during[cid] += 1
        elif t > te + guard:
            after[cid] += 1

    ids = sorted(set(before) | set(during) | set(after))
    stopped, reduced, unchanged = [], [], []
    for cid in ids:
        b, d, a = before[cid], during[cid], after[cid]
        active_outside = b > 0 and a > 0
        if active_outside and d == 0:
            stopped.append((cid, b, d, a))
        elif active_outside and b and d < b * 0.25:
            reduced.append((cid, b, d, a))
        else:
            unchanged.append((cid, b, d, a))

    print("=== STOPPED during silence -> transmitted by the IPMA ===")
    print(f"{'ID':>6} {'before':>8} {'during':>8} {'after':>8}")
    for cid, b, d, a in stopped:
        print(f" {cid:#05x} {b:>8} {d:>8} {a:>8}")
    if not stopped:
        print("  (none)")

    if reduced:
        print("\n=== REDUCED but not absent (inspect manually) ===")
        for cid, b, d, a in reduced:
            print(f" {cid:#05x} {b:>8} {d:>8} {a:>8}")

    print(f"\n{len(unchanged)} other IDs unaffected "
          f"(other modules kept transmitting, as intended)")
    print("\nIDs to emulate: "
          + ", ".join(f"{c:#05x}" for c, _b, _d, _a in stopped))
    return 0


def selftest():
    ok = True

    def chk(n, cond):
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL'}  {n}")

    import tempfile
    import os
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.log")
    with open(p, "w") as f:
        t = 1000.0
        # before: 0x0A5 and 0x140 both active
        for i in range(100):
            f.write(f"({t + i*0.02:.6f}) can0 0A5#0011223344556677\n")
            f.write(f"({t + i*0.02:.6f}) can0 140#0011223344556677\n")
        t += 5
        f.write(f"({t:.6f}) can0 706#021002000000000000\n")
        # during: only 0x140
        for i in range(100):
            f.write(f"({t + 1 + i*0.02:.6f}) can0 140#0011223344556677\n")
        t += 5
        f.write(f"({t:.6f}) can0 706#0211010000000000\n")
        for i in range(100):
            f.write(f"({t + 1 + i*0.02:.6f}) can0 0A5#0011223344556677\n")
            f.write(f"({t + 1 + i*0.02:.6f}) can0 140#0011223344556677\n")
    rows = parse(p)
    chk("log parsed", len(rows) > 500)
    ph = phases(rows)
    chk("phases located", ph is not None)
    chk("silence window is ~5s", ph and 4 < (ph[2] - ph[1]) < 6)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?")
    ap.add_argument("--guard", type=float, default=1.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.log:
        sys.exit(0 if selftest() else 1)
    sys.exit(analyse(a.log, a.guard))


if __name__ == "__main__":
    main()
