#!/usr/bin/env python3
"""Extract a steady-speed replay window from a candump log.

Pulls every frame of the IDs we intend to spoof, over a time window in which the
car was really travelling 41-60 km/h, and writes them to a compact JSONL file.

WHY A RECORDED WINDOW RATHER THAN SYNTHESISED FRAMES
    Several of these messages carry rolling counters and checksums (0x1E0
    carries VehicleSpeedCounter + VehicleSpeedCS, and its counter runs 5900
    frames with ZERO discontinuities in this window). Replaying real captured
    bytes means every counter and checksum is already valid -- no algorithm has
    to be solved, and no "defaults are not neutral" mistake is possible.

    The lesson is recorded from the IPMA spoof that triggered a dash fault:
    synthesised frames built from DBC defaults were wrong in all 8 bytes.

Usage:
    python3 extract_window.py --out abs_window.jsonl
    python3 extract_window.py --selftest
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

LINE = re.compile(r"\((\d+\.\d+)\)\s+(\S+)\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")

# ABS-transmitted IDs (from CAN-HS.dbc) + the three the IPMA sends.
ABS_IDS = [0x160, 0x180, 0x190, 0x1C0, 0x1D0, 0x1E0,
           0x210, 0x213, 0x218, 0x252, 0x2D0, 0x2D4]
IPMA_IDS = [0x0A5, 0x1B5, 0x298]

DEFAULT_LOG = os.path.join(ROOT, "drivemod2.log")
# 118 s of steady 41-60 km/h with 5329 LCA samples
T0, T1 = 1789395839.8, 1789395957.8


def extract(log, t0, t1, ids):
    want = set(ids)
    out = []
    for ln in open(log, errors="replace"):
        m = LINE.match(ln)
        if not m:
            continue
        t = float(m.group(1))
        if t < t0:
            continue
        if t > t1:
            break
        cid = int(m.group(3), 16)
        if cid not in want:
            continue
        data = m.group(4)
        if len(data) % 2:
            continue
        out.append((round(t - t0, 6), cid, data))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--t0", type=float, default=T0)
    ap.add_argument("--t1", type=float, default=T1)
    ap.add_argument("--out", default=os.path.join(HERE, "abs_window.jsonl"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    fr = extract(a.log, a.t0, a.t1, ABS_IDS + IPMA_IDS)
    if not fr:
        raise SystemExit("no frames extracted -- wrong log or window?")
    with open(a.out, "w") as fh:
        fh.write(json.dumps({"t0": a.t0, "t1": a.t1,
                             "src": os.path.basename(a.log),
                             "duration": round(a.t1 - a.t0, 3)}) + "\n")
        for t, cid, d in fr:
            fh.write(json.dumps([t, cid, d]) + "\n")

    import collections
    c = collections.Counter(cid for _, cid, _ in fr)
    print(f"wrote {a.out}")
    print(f"  {len(fr)} frames over {a.t1 - a.t0:.1f} s, {len(c)} IDs")
    for cid in sorted(c):
        print(f"    0x{cid:03X}: {c[cid]:>6}")
    return 0


def selftest():
    ok = True

    def chk(n, c, d=""):
        nonlocal ok
        ok = ok and bool(c)
        print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f"  {d}" if d else ""))

    print("SELFTEST\n")
    m = LINE.match("(1789395839.812345) can0  1E0#7D58E5700000FC33")
    chk("candump line parses", m is not None)
    if m:
        chk("  timestamp", abs(float(m.group(1)) - 1789395839.812345) < 1e-6)
        chk("  id", int(m.group(3), 16) == 0x1E0)
        chk("  data", m.group(4) == "7D58E5700000FC33")
    chk("ABS id list has 12 entries", len(ABS_IDS) == 12, str(len(ABS_IDS)))
    chk("IPMA ids are the measured three",
        IPMA_IDS == [0x0A5, 0x1B5, 0x298])
    chk("window is ~118 s", 110 < (T1 - T0) < 125, f"{T1 - T0:.1f}")
    out = os.path.join(HERE, "abs_window.jsonl")
    if os.path.exists(out):
        lines = open(out).read().splitlines()
        hdr = json.loads(lines[0])
        fr = [json.loads(x) for x in lines[1:]]
        chk("existing window file parses", len(fr) > 1000, str(len(fr)))
        chk("  frames are time-ordered",
            all(fr[i][0] <= fr[i + 1][0] for i in range(len(fr) - 1)))
        chk("  0x1E0 present", any(f[1] == 0x1E0 for f in fr))
        chk("  0x0A5 present", any(f[1] == 0x0A5 for f in fr))
        chk("  all payloads 8 bytes",
            all(len(f[2]) == 16 for f in fr if f[1] in (0x1E0, 0x0A5)))
        # the point of the whole exercise: counters must be continuous
        def be(d, s, l):
            v = 0
            b, bit = s // 8, s % 8
            for _ in range(l):
                v = (v << 1) | ((d[b] >> bit) & 1)
                bit -= 1
                if bit < 0:
                    bit, b = 7, b + 1
            return v
        seq = [be(bytes.fromhex(f[2]), 51, 4) for f in fr if f[1] == 0x1E0]
        bad = sum(1 for i in range(1, len(seq)) if (seq[i - 1] + 1) % 16 != seq[i])
        chk("  0x1E0 counter has NO discontinuities", bad == 0, f"{bad} bad")
    print("\n" + "=" * 52)
    print("SELFTEST:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
