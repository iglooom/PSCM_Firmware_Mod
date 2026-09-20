#!/usr/bin/env python3
"""Stage 2a: capture a corpus of IPMA / PSCM lane-assist frames.  READ-ONLY.

Records raw frames for offline checksum solving.  It never transmits.

Captures by default:
    0x0A5  IPMA_h_FrP01   the lane-assist command (carries both checksums)
    0x140  PSCM_h_FrP01   the PSCM's own lane-assist status
    0x010  SASM_h_FrP00   steering angle (context)

Output is JSON Lines: one {"t","id","d"} per frame, hex payload -- trivially
re-readable by solve_checksum.py and safe to archive.

Usage
-----
    python3 capture_lane.py --iface can0 --seconds 300 --out corpus.jsonl
    python3 capture_lane.py --from-candump dump.log --out corpus.jsonl
    python3 capture_lane.py --selftest

For a *useful* checksum corpus the protected signals must VARY.  Sitting still
with lane assist idle gives thousands of identical frames, which constrain
nothing.  See the guidance printed by --advice.
"""
import argparse
import json
import os
import re
import socket
import struct
import sys
import time

IDS = {0x0A5: "IPMA_h_FrP01", 0x140: "PSCM_h_FrP01", 0x010: "SASM_h_FrP00"}

ADVICE = """\
Corpus quality matters more than corpus size.

The solver can only pin down a checksum if the bytes it protects CHANGE across
frames.  10000 identical idle frames constrain nothing; 200 varied ones solve
it.  To get variation while stationary, ignition on:

  * turn the steering wheel slowly lock to lock  (varies LaRefAng / angle and,
    on some builds, the lane-assist request)
  * drive a short low-speed loop if safe and lawful, ideally on a marked road
    where the camera actually engages LDW/LKA -- that is what makes
    LkaActvStats_D_Req and the rolling counter move
  * at minimum, capture long enough for the rolling counter to wrap many times

After capture, check variability before trusting any result:
    python3 solve_checksum.py corpus.jsonl --stats
"""


def parse_candump(path):
    """Parse `candump -l` or plain `candump` output lines."""
    out = []
    # (1700000000.000000) can0 0A5#1122334455667788
    pat = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")
    # candump plain: "  can0  0A5   [8]  11 22 33 44 55 66 77 88"
    pat2 = re.compile(r"\s*\S+\s+([0-9A-Fa-f]+)\s+\[(\d+)\]\s+((?:[0-9A-Fa-f]{2}\s*)+)")
    t0 = None
    for ln in open(path, errors="replace"):
        m = pat.match(ln)
        if m:
            t, cid, data = float(m.group(1)), int(m.group(2), 16), m.group(3)
            t0 = t if t0 is None else t0
            out.append({"t": round(t - t0, 6), "id": cid, "d": data.upper()})
            continue
        m = pat2.match(ln)
        if m:
            cid = int(m.group(1), 16)
            data = m.group(3).replace(" ", "").strip().upper()
            out.append({"t": 0.0, "id": cid, "d": data})
    return out


def capture(iface, bustype, seconds, ids):
    """Raw SocketCAN capture (python-can is not installed; the kernel suffices)."""
    try:
        sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        sock.bind((iface,))
    except OSError as e:
        print(f"ERROR: cannot open {iface}: {e}\n"
              f"       or capture with: candump -l {iface}  then --from-candump",
              file=sys.stderr)
        return None
    sock.settimeout(1.0)
    print(f"capturing on {iface} for {seconds}s -- READ-ONLY, nothing is sent")
    print("watching: " + ", ".join(f"{i:#05x} {IDS.get(i,'')}" for i in sorted(ids)))
    out, t0 = [], time.time()
    counts = {}
    try:
        while time.time() - t0 < seconds:
            try:
                f = sock.recv(16)
            except socket.timeout:
                continue
            cid, dlc = struct.unpack("=IB3x", f[:8])
            cid &= 0x1FFFFFFF
            if cid not in ids:
                continue
            counts[cid] = counts.get(cid, 0) + 1
            out.append({"t": round(time.time() - t0, 6), "id": cid,
                        "d": f[8:8 + dlc].hex().upper()})
            if len(out) % 500 == 0:
                print(f"  {len(out)} frames  " +
                      " ".join(f"{k:#05x}={v}" for k, v in sorted(counts.items())),
                      end="\r", flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        sock.close()
    print()
    for i in sorted(ids):
        print(f"  {i:#05x} {IDS.get(i,''):<14} {counts.get(i,0)} frames")
    if not counts.get(0x0A5):
        print("\nNOTE: no 0x0A5 captured. Either no IPMA on this bus, or the "
              "camera is silent.\n      That is itself a finding -- report it.")
    return out


def selftest():
    ok = True

    def chk(n, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"  {'PASS' if good else 'FAIL'}  {n}: {got!r}"
              + ("" if good else f" want {want!r}"))

    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "c.log")
    with open(p, "w") as f:
        f.write("(1700000000.000000) can0 0A5#0011223344556677\n")
        f.write("(1700000000.010000) can0 140#8899AABBCCDDEEFF\n")
        f.write("(1700000000.020000) can1 0A5#0102030405060708\n")
    r = parse_candump(p)
    chk("candump -l lines parsed", len(r), 3)
    chk("first id", r[0]["id"], 0x0A5)
    chk("first payload", r[0]["d"], "0011223344556677")
    chk("relative time", r[1]["t"], 0.01)

    p2 = os.path.join(d, "c2.log")
    with open(p2, "w") as f:
        f.write("  can0  0A5   [8]  11 22 33 44 55 66 77 88\n")
    r2 = parse_candump(p2)
    chk("plain candump parsed", (r2[0]["id"], r2[0]["d"]),
        (0x0A5, "1122334455667788"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--bustype", default="socketcan")
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--out", default="corpus.jsonl")
    ap.add_argument("--from-candump")
    ap.add_argument("--ids", default="0x0A5,0x140,0x010")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--advice", action="store_true")
    a = ap.parse_args()

    if a.advice:
        print(ADVICE)
        return
    if a.selftest or len(sys.argv) == 1:
        sys.exit(0 if selftest() else 1)

    ids = {int(x, 0) for x in a.ids.split(",")}
    rows = (parse_candump(a.from_candump) if a.from_candump
            else capture(a.iface, a.bustype, a.seconds, ids))
    if rows is None:
        sys.exit(2)
    rows = [r for r in rows if r["id"] in ids]
    with open(a.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} frames -> {a.out}")
    print(f"next: python3 solve_checksum.py {a.out} --stats")


if __name__ == "__main__":
    main()
