#!/usr/bin/env python3
"""Ask the PSCM whether lane assist is available at all, before spoofing.

The PSCM publishes its own verdict on 0x140 PSCM_h_FrP01:

    LaActAvail_D_Actl  3 = LKA/LCA and LDW available
                       2 = LCA/LKA available, LDW suppressed
                       1 = LCA/LKA SUPPRESSED, LDW available
                       0 = LCA/LKA and LDW SUPPRESSED
    LaActDeny_B_Actl   1 = lane assist denied

If LaActAvail says LKA/LCA is suppressed while the REAL camera is working,
then no camera message can make the wheel move -- the precondition fails
inside the PSCM and a spoof cannot succeed.  Checking this first turns a
mysterious null result into a measured one.

Works on a capture file or live.

Usage:
    python3 precondition_check.py candump-....log
    python3 precondition_check.py corpus.jsonl
    python3 precondition_check.py --iface can0 --seconds 15
    python3 precondition_check.py --selftest
"""
import argparse
import json
import re
import socket
import struct
import sys
import time
from collections import Counter

AVAIL = {3: "LKA/LCA and LDW available",
         2: "LCA/LKA available, LDW suppressed",
         1: "LCA/LKA SUPPRESSED, LDW available",
         0: "LCA/LKA and LDW SUPPRESSED"}

# DBC geometry (big-endian start bits) -- verified against CAN-HS.dbc:
#   BO_ 320 PSCM_h_FrP01:  LaActAvail_D_Actl 59|2@0+ , LaActDeny_B_Actl 60|1@0+
#                          LaHandsOff_B_Actl 61|1@0+
# (An earlier version of this file used start bit 7 and mis-read the signal --
#  it reported "suppressed" for payloads that actually said otherwise.)
SIG = {
    "LaActAvail_D_Actl": (0x140, 59, 2),
    "LaActDeny_B_Actl": (0x140, 60, 1),
    "LaHandsOff_B_Actl": (0x140, 61, 1),
    # 0x1E0 ABS_h_FrP04 -- Veh_V_ActlBrk 39|16@0+ (0.01 kph)
    "Veh_V_ActlBrk": (0x1E0, 39, 16),
    # 0x010 SASM_h_FrP00 -- SteeringAngle 54|15@0+ (0.04395 deg)
    "SteeringAngle": (0x010, 54, 15),
}


def be_extract(data, start, length):
    """Vector/DBC big-endian ('@0') extraction."""
    v = 0
    b, bit = start // 8, start % 8
    for _ in range(length):
        v = (v << 1) | ((data[b] >> bit) & 1)
        bit -= 1
        if bit < 0:
            bit, b = 7, b + 1
    return v


LINE = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")


def read_file(path):
    for ln in open(path, errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("{"):
            r = json.loads(ln)
            if len(r.get("d", "")) == 16:
                yield r["id"], bytes.fromhex(r["d"])
        else:
            m = LINE.match(ln)
            if m and len(m.group(3)) == 16:
                yield int(m.group(2), 16), bytes.fromhex(m.group(3))


def read_live(iface, seconds):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    s.settimeout(1.0)
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            try:
                f = s.recv(16)
            except socket.timeout:
                continue
            cid, dlc = struct.unpack("=IB3x", f[:8])
            yield cid & 0x1FFFFFFF, f[8:8 + dlc]
    finally:
        s.close()


def analyse(rows):
    avail, deny, hands = Counter(), Counter(), Counter()
    speed, angle = [], []
    n140 = n1e0 = 0
    _, av_s, av_l = SIG["LaActAvail_D_Actl"]
    _, dn_s, dn_l = SIG["LaActDeny_B_Actl"]
    _, ho_s, ho_l = SIG["LaHandsOff_B_Actl"]
    for cid, d in rows:
        if cid == 0x140:
            n140 += 1
            avail[be_extract(d, av_s, av_l)] += 1
            deny[be_extract(d, dn_s, dn_l)] += 1
            hands[be_extract(d, ho_s, ho_l)] += 1
        elif cid == 0x1E0:
            n1e0 += 1
            speed.append(be_extract(d, 39, 16) * 0.01)
        elif cid == 0x010:
            angle.append(be_extract(d, 54, 15) * 0.04395)

    if not n140:
        print("no 0x140 seen -- PSCM not transmitting, or wrong bus.",
              file=sys.stderr)
        return 2

    print(f"0x140 PSCM_h_FrP01: {n140} frames")
    for k, v in avail.most_common():
        print(f"   LaActAvail_D_Actl = {k}  {AVAIL.get(k, '?')}   x{v}")
    for k, v in deny.most_common():
        print(f"   LaActDeny_B_Actl  = {k}   x{v}")
    for k, v in hands.most_common():
        print(f"   LaHandsOff_B_Actl = {k}   x{v}")
    if speed:
        print(f"\n0x1E0 Veh_V_ActlBrk: {n1e0} frames, "
              f"{min(speed):.2f} .. {max(speed):.2f} kph")
    if angle:
        print(f"0x010 SteeringAngle: {min(angle):.1f} .. {max(angle):.1f} deg")

    lka_ok = any(k in (2, 3) for k in avail)
    print()
    if lka_ok:
        print("VERDICT: the PSCM reports LKA/LCA AVAILABLE -- a correctly")
        print("         formed 0x0A5 should be able to command steering.")
        return 0
    print("VERDICT: the PSCM reports LKA/LCA SUPPRESSED.")
    print("         No camera message can move the wheel in this state --")
    print("         the precondition fails inside the PSCM, not on the bus.")
    if speed and max(speed) < 1.0:
        print("         Road speed is ~0; a speed precondition is the prime")
        print("         suspect. Re-check this while driving.")
    return 1


def selftest():
    ok = True

    def chk(n, cond, d=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {n}" + (f"  {d}" if d else ""))

    # LaActAvail_D_Actl is 59|2@0+ -> byte 7, bits 3..2 (DBC-verified).
    _, av_s, av_l = SIG["LaActAvail_D_Actl"]
    chk("LaActAvail geometry matches the DBC", (av_s, av_l) == (59, 2))
    for val in range(4):
        f = bytearray(8)
        f[7] = val << 2
        chk(f"LaActAvail={val} round-trips", be_extract(f, av_s, av_l) == val)
    # Veh_V_ActlBrk 39|16 -> bytes 4..5
    f = bytearray(8)
    f[4], f[5] = 0x13, 0x88          # 5000 -> 50.00 kph
    chk("speed decodes to 50.00 kph",
        abs(be_extract(f, 39, 16) * 0.01 - 50.0) < 1e-9)
    f = bytearray(8)
    chk("zero payload is 0 kph", be_extract(f, 39, 16) == 0)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--iface")
    ap.add_argument("--seconds", type=float, default=15)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if a.iface:
        sys.exit(analyse(read_live(a.iface, a.seconds)))
    if not a.path:
        ap.error("give a capture file or --iface")
    sys.exit(analyse(read_file(a.path)))


if __name__ == "__main__":
    main()
