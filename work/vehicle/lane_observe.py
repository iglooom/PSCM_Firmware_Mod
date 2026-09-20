#!/usr/bin/env python3
"""PASSIVE lane-assist observer for the Ford PSCM.  READ-ONLY: never transmits.

Why this comes before any spoofing test
---------------------------------------
The PSCM *publishes its own lane-assist availability* on 0x140 PSCM_h_FrP01:

    LaActAvail_D_Actl  3 = "LKA/LCA and LDW available"
                       2 = "LCA/LKA available, LDW suppressed"
                       1 = "LCA/LKA suppressed, LDW available"
                       0 = "LCA/LKA and LDW suppressed"
    LaActDeny_B_Actl   1 = "LA denied"
    LaHandsOff_B_Actl  1 = "Hands off"

So the module tells us whether it considers lane assist available, with no
transmission required.  If a build never reports a non-zero LaActAvail under
any condition, that is strong evidence on its own -- obtained at zero risk.

It also logs the IPMA's own 0x0A5 request so the two can be correlated:
what the camera asked for vs how the PSCM responded.

Usage
-----
    python3 lane_observe.py --selftest            # offline, no bus needed
    python3 lane_observe.py --iface can0          # live, read-only
    python3 lane_observe.py --iface can0 --csv run.csv --seconds 300

Requires python-can for live capture (`pip install python-can`); --selftest
runs without it.
"""
import argparse
import socket
import struct
import sys
import time

# --- DBC signal definitions (Motorola / big-endian, @0+) ------------------
# name: (start_bit, length, scale, offset)
SIG_140 = {
    "LaActAvail_D_Actl":     (59, 2, 1, 0),
    "LaActDeny_B_Actl":      (60, 1, 1, 0),
    "LaHandsOff_B_Actl":     (61, 1, 1, 0),
    "LaActStats_No_Cs":      (55, 8, 1, 0),
    "LaActStats_No_RollCnt": (43, 4, 1, 0),
    "TorsionBarTorque":      (17, 10, 0.02, 0),
    "TorsionBarTorqueSign":  (21, 1, 1, 0),
    "TorsionBarTorqueQF":    (20, 2, 1, 0),
}
SIG_0A5 = {
    "LkaActvStats_D_Req":    (30, 3, 1, 0),
    "LdwActvStats_D_Req":    (14, 3, 1, 0),
    "LdwActvIntns_D_Req":    (9, 2, 1, 0),
    "LaRampType_B_Req":      (31, 1, 1, 0),
    "LaRefAng_No_Req":       (27, 12, 0.05, -102.4),
    "LaCurvature_No_Calc":   (51, 12, 5e-06, -0.01024),
    "LaActvReq_No_RollCnt":  (55, 4, 1, 0),
    "LaActvStats_No_Cs":     (47, 8, 1, 0),
    "LaStePar_No_Cs":        (23, 8, 1, 0),
}
SIG_010 = {"SteeringAngle": (7, 16, 0.0625, -2048)}

AVAIL = {3: "LKA/LCA+LDW available", 2: "LCA/LKA avail, LDW suppr",
         1: "LCA/LKA suppr, LDW avail", 0: "LCA/LKA+LDW suppressed"}
LKA = {0: "LKA Idle", 1: "LKA Idle/LCA Suppressed",
       2: "LKA Interv Left", 3: "LKA Suppr Left",
       4: "LKA Interv Right", 5: "LKA Suppr Right",
       6: "*** LCA IN PROGRESS ***", 7: "LKA/LCA Suppr Both"}


def be_extract(data, start, length):
    """DBC Motorola/@0+ extraction: start bit is the signal's MSB."""
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


def be_insert(data, start, length, value):
    """Inverse of be_extract, for the self-test."""
    b, bit = start // 8, start % 8
    for k in range(length - 1, -1, -1):
        v = (value >> k) & 1
        data[b] = (data[b] & ~(1 << bit)) | (v << bit)
        bit -= 1
        if bit < 0:
            bit, b = 7, b + 1
    return data


def decode(data, spec):
    out = {}
    for nm, (st, ln, sc, off) in spec.items():
        raw = be_extract(data, st, ln)
        out[nm] = None if raw is None else (
            raw if sc == 1 and off == 0 else round(raw * sc + off, 4))
        out["_raw_" + nm] = raw
    return out


def selftest():
    ok = True

    def chk(name, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"  {'PASS' if good else 'FAIL'}  {name}: {got!r}"
              + ("" if good else f"  want {want!r}"))

    print("-- bit geometry vs the firmware's own extraction specs --")
    # The 14C386 extraction table stores (mask, shift) per signal; these were
    # verified against firmware spec words.  Re-derive them from the DBC here.
    for nm, spec, want_mask, want_shift in (
            ("LkaActvStats_D_Req", SIG_0A5, 0x70, 4),
            ("LdwActvIntns_D_Req", SIG_0A5, 0x03, 0),
            ("LaRampType_B_Req", SIG_0A5, 0x80, 7)):
        st, ln = spec[nm][0], spec[nm][1]
        shift = (st % 8) - ln + 1
        mask = ((1 << ln) - 1) << shift
        chk(f"{nm} mask/shift", (mask, shift), (want_mask, want_shift))

    print("-- round-trip encode/decode --")
    for nm, spec, val in (("LaActAvail_D_Actl", SIG_140, 3),
                          ("LaActDeny_B_Actl", SIG_140, 1),
                          ("LkaActvStats_D_Req", SIG_0A5, 6),
                          ("LaActvReq_No_RollCnt", SIG_0A5, 0xD)):
        st, ln = spec[nm][0], spec[nm][1]
        d = be_insert(bytearray(8), st, ln, val)
        chk(f"{nm} = {val}", be_extract(d, st, ln), val)

    print("-- signals must not overlap within a message --")
    for label, spec in (("0x140", SIG_140), ("0x0A5", SIG_0A5)):
        used = {}
        clash = []
        for nm, (st, ln, _s, _o) in spec.items():
            b, bit = st // 8, st % 8
            for _ in range(ln):
                key = (b, bit)
                if key in used:
                    clash.append((nm, used[key]))
                used[key] = nm
                bit -= 1
                if bit < 0:
                    bit, b = 7, b + 1
        chk(f"{label} no overlapping signals", clash, [])

    print("-- a real-looking 0x140 frame decodes sensibly --")
    d = bytearray(8)
    be_insert(d, 59, 2, 3)          # LaActAvail = available
    be_insert(d, 17, 10, 500)       # TorsionBarTorque raw 500 -> 10.0 Nm
    r = decode(d, SIG_140)
    chk("LaActAvail_D_Actl", r["_raw_LaActAvail_D_Actl"], 3)
    chk("TorsionBarTorque Nm", r["TorsionBarTorque"], 10.0)
    return ok


def raw_can_socket(iface):
    """Raw SocketCAN (python-can is not installed; the kernel suffices)."""
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    return s


def read_frame(sock):
    """-> (can_id, data) from one raw CAN frame."""
    f = sock.recv(16)
    cid, dlc = struct.unpack("=IB3x", f[:8])
    return cid & 0x1FFFFFFF, f[8:8 + dlc]


def run(iface, seconds, csvpath, bustype=None):
    try:
        sock = raw_can_socket(iface)
    except OSError as e:
        print(f"ERROR: cannot open {iface}: {e}", file=sys.stderr)
        return 2
    sock.settimeout(1.0)
    print(f"listening on {iface} (READ-ONLY, nothing is transmitted)")
    print("watching 0x0A5 (IPMA request), 0x140 (PSCM status), "
          "0x010 (steering angle)\n")

    csv = open(csvpath, "w") if csvpath else None
    if csv:
        csv.write("t,id,LkaActvStats,LaRefAng,LaCurvature,"
                  "LaActAvail,LaActDeny,LaHandsOff,TorsionBarTorque,angle\n")

    t0 = time.time()
    last = {}
    seen = {0x0A5: 0, 0x140: 0, 0x010: 0}
    state = {"lka": None, "avail": None, "deny": None, "tq": None,
             "ang": None, "refang": None, "curv": None}
    try:
        while seconds is None or time.time() - t0 < seconds:
            try:
                cid, data = read_frame(sock)
            except socket.timeout:
                continue
            t = time.time() - t0
            if cid == 0x0A5:
                seen[0x0A5] += 1
                r = decode(data, SIG_0A5)
                state["lka"] = r["_raw_LkaActvStats_D_Req"]
                state["refang"] = r["LaRefAng_No_Req"]
                state["curv"] = r["LaCurvature_No_Calc"]
            elif cid == 0x140:
                seen[0x140] += 1
                r = decode(data, SIG_140)
                state["avail"] = r["_raw_LaActAvail_D_Actl"]
                state["deny"] = r["_raw_LaActDeny_B_Actl"]
                sign = -1 if r["_raw_TorsionBarTorqueSign"] else 1
                state["tq"] = round(sign * r["TorsionBarTorque"], 2)
            elif cid == 0x010:
                seen[0x010] += 1
                state["ang"] = decode(data, SIG_010)["SteeringAngle"]
            else:
                continue

            key = (state["lka"], state["avail"], state["deny"])
            if key != last.get("key") and None not in key:
                last["key"] = key
                print(f"[{t:7.2f}s] IPMA LkaActvStats={state['lka']} "
                      f"({LKA.get(state['lka'], '?')})   "
                      f"PSCM LaActAvail={state['avail']} "
                      f"({AVAIL.get(state['avail'], '?')}) "
                      f"deny={state['deny']}  torque={state['tq']} Nm")
            if csv:
                csv.write(f"{t:.3f},{cid:#05x},{state['lka']},"
                          f"{state['refang']},{state['curv']},"
                          f"{state['avail']},{state['deny']},"
                          f"{state['tq']},{state['ang']}\n")
    except KeyboardInterrupt:
        pass
    finally:
        if csv:
            csv.close()
        sock.close()

    print(f"\nframes seen: " + ", ".join(f"{k:#05x}={v}" for k, v in seen.items()))
    if seen[0x140] == 0:
        print("WARNING: no 0x140 seen -- PSCM not transmitting, or wrong bus.")
    if seen[0x0A5] == 0:
        print("NOTE: no 0x0A5 seen -- the camera is silent (expected while "
              "silence_ipma.py is running).")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--bustype", default="socketcan")
    ap.add_argument("--seconds", type=int, default=None)
    ap.add_argument("--csv")
    a = ap.parse_args()
    if a.selftest or len(sys.argv) == 1:
        sys.exit(0 if selftest() else 1)
    sys.exit(run(a.iface, a.seconds, a.csv, a.bustype))


if __name__ == "__main__":
    main()
