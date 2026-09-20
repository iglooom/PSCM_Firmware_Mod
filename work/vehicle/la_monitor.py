#!/usr/bin/env python3
"""Live lane-assist status monitor + logger. READ-ONLY: transmits nothing.

Answers one question on a drive: as speed rises, WHICH module unlocks first --
the IPMA (starts commanding) or the PSCM (declares lane assist available)?

    IPMA  0x0A5  LkaActvStats_D_Req    what the camera is asking for
    PSCM  0x140  LaActAvail_D_Actl     the PSCM's own verdict  <-- the gate
                 LaActDeny_B_Actl      active denial
                 LaHandsOff_B_Actl     hands-off detection
                 TorsionBarTorque      steering torque actually measured
    BCM   0x1B5  La*LineStats/VLvl     what the cluster is being told
    ABS   0x1E0  Veh_V_ActlBrk         road speed (0.01 kph)
    SASM  0x010  SteeringAngle         steering wheel angle

Every state change is timestamped WITH THE SPEED AT THAT MOMENT, so the
engage/disengage thresholds and their hysteresis fall straight out of the log.

Outputs (all optional but ON by default with --log PREFIX):
    PREFIX.csv     one row per change, human-readable
    PREFIX.jsonl   every decoded sample (for later re-analysis)
    PREFIX.log     raw candump-format frames (re-analysable with any tool)

Usage:
    python3 la_monitor.py --iface can0 --log drive1
    python3 la_monitor.py --replay ../../candump-2026-09-14_111406.log
    python3 la_monitor.py --selftest
"""
import argparse
import json
import os
import re
import socket
import struct
import sys
import time

# ---------------------------------------------------------------- signals --
# (start_bit, length) big-endian '@0+' as in the DBC.
S_140 = {                       # PSCM_h_FrP01 -- the gate
    "LaActAvail_D_Actl": (59, 2),
    "LaActDeny_B_Actl": (60, 1),
    "LaHandsOff_B_Actl": (61, 1),
    "TorsionBarTorque": (17, 10),
    "TorsionBarTorqueSign": (21, 1),
    "LaActStats_No_RollCnt": (43, 4),
}
S_0A5 = {                       # IPMA_h_FrP01 -- the request
    "LkaActvStats_D_Req": (30, 3),
    "LdwActvStats_D_Req": (14, 3),
    "LdwActvIntns_D_Req": (9, 2),
    "LaRampType_B_Req": (31, 1),
    "LaRefAng_No_Req": (27, 12),
    "LaCurvature_No_Calc": (51, 12),
    "LaActvReq_No_RollCnt": (55, 4),
}
S_1B5 = {                       # IPMA_h_FrP02 -- what the cluster shows
    "LaLLineStats_D_Dsply": (1, 2),
    "LaRLineStats_D_Dsply": (14, 2),
    "LkaVLvl_B_Dsply": (2, 1),
    "LcaVLvl_B_Dsply": (3, 1),
    "LdwVLvl_B_Dsply": (15, 1),
    "LaDenyStats_B_Dsply": (21, 1),
    "LaHandsOff_D_Dsply": (23, 2),
    "LaMenuEnbl_B_Actl": (24, 1),
    "LkaMenuStats_B_Actl": (37, 1),
    "LcaMenuStats_B_Actl": (36, 1),
}
S_1E0 = {"Veh_V_ActlBrk": (39, 16), "VehVActlBrk_D_Qf": (53, 2)}
S_010 = {"SteeringAngle": (54, 15)}

AVAIL = {3: "LKA/LCA+LDW AVAILABLE", 2: "LKA/LCA avail, LDW suppr",
         1: "LKA/LCA SUPPRESSED", 0: "ALL SUPPRESSED"}
LKA_REQ = {0: "Idle", 1: "Idle/LCA suppr", 2: "INTERV LEFT",
           3: "Suppr Left", 4: "INTERV RIGHT", 5: "Suppr Right",
           6: "LCA IN PROGRESS", 7: "Suppr L+R"}
LINE = {0: "none", 1: "detected", 2: "not-overridable", 3: "overridable"}

# Signals whose change is worth a log row + console line.
WATCH = ["LaActAvail_D_Actl", "LaActDeny_B_Actl", "LaHandsOff_B_Actl",
         "LkaActvStats_D_Req", "LdwActvStats_D_Req",
         "LaLLineStats_D_Dsply", "LaRLineStats_D_Dsply",
         "LkaVLvl_B_Dsply", "LcaVLvl_B_Dsply", "LaDenyStats_B_Dsply"]


def be(data, start, length):
    """Vector/DBC big-endian ('@0') bit extraction."""
    v = 0
    b, bit = start // 8, start % 8
    for _ in range(length):
        if b >= len(data):
            return v
        v = (v << 1) | ((data[b] >> bit) & 1)
        bit -= 1
        if bit < 0:
            bit, b = 7, b + 1
    return v


def decode(data, spec):
    return {k: be(data, s, l) for k, (s, l) in spec.items()}


# ------------------------------------------------------------------- io --
CANDUMP = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")


def read_replay(path):
    for ln in open(path, errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("{"):
            r = json.loads(ln)
            if len(r.get("d", "")) >= 2:
                yield r.get("t", 0.0), r["id"], bytes.fromhex(r["d"])
        else:
            m = CANDUMP.match(ln)
            if m and m.group(3):
                yield (float(m.group(1)), int(m.group(2), 16),
                       bytes.fromhex(m.group(3)))


def read_live(iface):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    s.settimeout(1.0)
    try:
        while True:
            try:
                f = s.recv(16)
            except socket.timeout:
                continue
            cid, dlc = struct.unpack("=IB3x", f[:8])
            yield time.time(), cid & 0x1FFFFFFF, f[8:8 + dlc]
    finally:
        s.close()


# ------------------------------------------------------------- monitor --
class Monitor:
    def __init__(self, prefix=None, quiet=False):
        self.st = {}
        self.speed = None
        self.angle = None
        self.torque = None
        self.prev = {}
        self.events = []
        self.t0 = None
        self.quiet = quiet
        self.n = {0x140: 0, 0x0A5: 0, 0x1B5: 0, 0x1E0: 0, 0x010: 0}
        self.csv = self.jsonl = self.raw = None
        if prefix:
            self.csv = open(prefix + ".csv", "w")
            self.csv.write("t,speed_kph,angle_deg,torque_nm,signal,"
                           "old,new,meaning\n")
            self.jsonl = open(prefix + ".jsonl", "w")
            self.raw = open(prefix + ".log", "w")

    def close(self):
        for f in (self.csv, self.jsonl, self.raw):
            if f:
                f.close()

    def feed(self, t, cid, data):
        if self.t0 is None:
            self.t0 = t
        if self.raw:
            self.raw.write(f"({t:.6f}) can0 {cid:03X}#{data.hex().upper()}\n")
        if cid not in self.n:
            return
        self.n[cid] += 1

        if cid == 0x1E0:
            r = decode(data, S_1E0)
            self.speed = r["Veh_V_ActlBrk"] * 0.01
            return
        if cid == 0x010:
            self.angle = decode(data, S_010)["SteeringAngle"] * 0.04395
            return

        spec = {0x140: S_140, 0x0A5: S_0A5, 0x1B5: S_1B5}[cid]
        r = decode(data, spec)
        if cid == 0x140:
            sign = -1 if r["TorsionBarTorqueSign"] else 1
            self.torque = round(sign * r["TorsionBarTorque"] * 0.02, 2)
        self.st.update(r)

        if self.jsonl:
            self.jsonl.write(json.dumps({
                "t": round(t - self.t0, 4), "id": cid,
                "speed": self.speed, "angle": self.angle,
                "torque": self.torque, **r}) + "\n")

        for k in spec:
            if k not in WATCH:
                continue
            old, new = self.prev.get(k), r[k]
            if old is None:
                self.prev[k] = new
                continue
            if old != new:
                self.prev[k] = new
                self._event(t, k, old, new)

    def _event(self, t, sig, old, new):
        mean = ""
        if sig == "LaActAvail_D_Actl":
            mean = f"PSCM: {AVAIL.get(old,'?')} -> {AVAIL.get(new,'?')}"
        elif sig == "LkaActvStats_D_Req":
            mean = f"IPMA: {LKA_REQ.get(old,'?')} -> {LKA_REQ.get(new,'?')}"
        elif sig.endswith("LineStats_D_Dsply"):
            mean = f"{LINE.get(old,'?')} -> {LINE.get(new,'?')}"
        elif sig == "LaActDeny_B_Actl":
            mean = "PSCM DENIES" if new else "PSCM denial cleared"
        rel = t - self.t0
        ev = {"t": round(rel, 3), "speed": self.speed, "angle": self.angle,
              "torque": self.torque, "signal": sig, "old": old, "new": new,
              "meaning": mean}
        self.events.append(ev)
        sp = f"{self.speed:6.2f}" if self.speed is not None else "  n/a "
        if self.csv:
            self.csv.write(f"{rel:.3f},{self.speed},{self.angle},"
                           f"{self.torque},{sig},{old},{new},\"{mean}\"\n")
            self.csv.flush()
        if not self.quiet:
            print(f"[{rel:8.2f}s {sp} kph] {sig:<22} {old} -> {new}"
                  f"   {mean}", flush=True)

    def status_line(self):
        a = self.st.get("LaActAvail_D_Actl")
        k = self.st.get("LkaActvStats_D_Req")
        sp = f"{self.speed:6.2f}" if self.speed is not None else "  n/a "
        tq = f"{self.torque:+6.2f}" if self.torque is not None else "  n/a "
        return (f"{sp} kph | PSCM {a} {AVAIL.get(a,'?'):<24} | "
                f"IPMA {k} {LKA_REQ.get(k,'?'):<16} | "
                f"deny {self.st.get('LaActDeny_B_Actl','?')} "
                f"hands {self.st.get('LaHandsOff_B_Actl','?')} | "
                f"L{self.st.get('LaLLineStats_D_Dsply','?')}"
                f"R{self.st.get('LaRLineStats_D_Dsply','?')} | "
                f"tq {tq} Nm")

    def report(self):
        print("\n" + "=" * 78)
        print("frames: " + "  ".join(f"{k:#05x}={v}" for k, v in self.n.items()))
        if not self.events:
            print("NO state changes recorded.")
            if self.n[0x140] and not self.n[0x0A5]:
                print("  0x140 seen but no 0x0A5 -- camera silent?")
            return
        print(f"\n{len(self.events)} state change(s):\n")
        print(f"  {'t':>9} {'kph':>7}  {'signal':<22} {'chg':<10} meaning")
        for e in self.events:
            sp = f"{e['speed']:7.2f}" if e["speed"] is not None else "    n/a"
            print(f"  {e['t']:9.2f} {sp}  {e['signal']:<22} "
                  f"{str(e['old'])+'->'+str(e['new']):<10} {e['meaning']}")
        self._verdict()

    def _verdict(self):
        """Who unlocked first -- the whole point of the drive."""
        pscm = [e for e in self.events
                if e["signal"] == "LaActAvail_D_Actl" and e["new"] in (2, 3)]
        ipma = [e for e in self.events
                if e["signal"] == "LkaActvStats_D_Req"
                and e["new"] in (2, 4, 6)]
        print("\n" + "-" * 78)
        print("VERDICT -- which module released the lock first?")
        if not pscm and not ipma:
            print("  Neither unlocked. LKA never became available on this run.")
            print("  If speed exceeded ~65 kph, the gate is NOT speed alone.")
            return
        if pscm:
            e = pscm[0]
            print(f"  PSCM declared AVAILABLE at t={e['t']:.2f}s, "
                  f"speed={e['speed']} kph")
        else:
            print("  PSCM NEVER declared lane assist available.")
        if ipma:
            e = ipma[0]
            print(f"  IPMA began commanding  at t={e['t']:.2f}s, "
                  f"speed={e['speed']} kph")
        else:
            print("  IPMA never commanded an intervention.")
        if pscm and ipma:
            d = ipma[0]["t"] - pscm[0]["t"]
            if d > 0:
                print(f"  => PSCM unlocked FIRST, {d:.2f}s before the IPMA "
                      f"commanded. The camera is the later gate.")
            else:
                print(f"  => IPMA commanded FIRST, {-d:.2f}s before the PSCM "
                      f"allowed it. The PSCM is the binding gate.")
        elif pscm and not ipma:
            print("  => PSCM allowed it but the camera never asked. "
                  "The IPMA is the binding gate (its own threshold).")
        elif ipma and not pscm:
            print("  => Camera asked but the PSCM never allowed it. "
                  "The PSCM is the binding gate.")
        # disengage side
        off = [e for e in self.events
               if e["signal"] == "LaActAvail_D_Actl" and e["new"] in (0, 1)
               and e["t"] > (pscm[0]["t"] if pscm else 0)]
        if pscm and off:
            print(f"\n  PSCM re-locked at speed={off[0]['speed']} kph "
                  f"(engaged at {pscm[0]['speed']} kph)")
            try:
                print(f"  => hysteresis "
                      f"{pscm[0]['speed'] - off[0]['speed']:.2f} kph")
            except TypeError:
                pass


# ----------------------------------------------------------------- main --
def selftest():
    ok = True

    def chk(n, c, d=""):
        nonlocal ok
        ok = ok and bool(c)
        print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f"  {d}" if d else ""))

    print("-- bit extraction matches the DBC geometry --")
    f = bytearray(8)
    f[7] = 3 << 2                      # LaActAvail 59|2 -> byte7 bits 3..2
    chk("LaActAvail=3 decodes", decode(f, S_140)["LaActAvail_D_Actl"] == 3)
    f = bytearray(8)
    f[3] = 6 << 4                      # LkaActvStats 30|3 -> byte3 bits 6..4
    chk("LkaActvStats=6 (LCA) decodes",
        decode(f, S_0A5)["LkaActvStats_D_Req"] == 6)
    f = bytearray(8)
    f[4], f[5] = 0x13, 0x88            # 5000 -> 50.00 kph
    chk("speed 50.00 kph decodes",
        abs(decode(f, S_1E0)["Veh_V_ActlBrk"] * 0.01 - 50.0) < 1e-9)

    print("-- transitions are detected with the speed attached --")
    m = Monitor(quiet=True)
    sp = bytearray(8)
    sp[4], sp[5] = 0x19, 0x64          # 6500 -> 65.00 kph
    m.feed(0.0, 0x1E0, bytes(sp))
    a = bytearray(8)                   # PSCM suppressed
    m.feed(0.1, 0x140, bytes(a))
    b = bytearray(8)
    b[7] = 3 << 2                      # PSCM available
    m.feed(0.2, 0x140, bytes(b))
    chk("one event recorded", len(m.events) == 1, str(len(m.events)))
    chk("event carries the speed", m.events and m.events[0]["speed"] == 65.0,
        str(m.events[0]["speed"] if m.events else None))
    chk("event names the right signal",
        m.events and m.events[0]["signal"] == "LaActAvail_D_Actl")

    print("-- verdict logic --")
    m2 = Monitor(quiet=True)
    m2.t0 = 0.0
    m2.events = [
        {"t": 1.0, "speed": 64.0, "angle": 0, "torque": 0,
         "signal": "LaActAvail_D_Actl", "old": 0, "new": 3, "meaning": ""},
        {"t": 3.0, "speed": 66.0, "angle": 0, "torque": 0,
         "signal": "LkaActvStats_D_Req", "old": 0, "new": 2, "meaning": ""},
    ]
    chk("PSCM-first case has both events", len(m2.events) == 2)

    print("-- replay on the real capture --")
    here = os.path.dirname(os.path.abspath(__file__))
    cap = os.path.join(here, "..", "..", "candump-2026-09-14_111406.log")
    if os.path.exists(cap):
        m3 = Monitor(quiet=True)
        n = 0
        for t, cid, d in read_replay(cap):
            m3.feed(t, cid, d)
            n += 1
        chk("capture parsed", n > 1000, f"{n} frames")
        chk("0x140 frames seen", m3.n[0x140] > 0, str(m3.n[0x140]))
        chk("speed decoded from the capture", m3.speed is not None,
            f"{m3.speed} kph")
        chk("PSCM verdict matches precondition_check (suppressed)",
            m3.st.get("LaActAvail_D_Actl") == 0,
            str(m3.st.get("LaActAvail_D_Actl")))
    else:
        print("  SKIP  capture not found")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--replay", help="candump .log or .jsonl to re-analyse")
    ap.add_argument("--log", help="output prefix (.csv/.jsonl/.log)")
    ap.add_argument("--seconds", type=float,
                    help="stop after N seconds (default: until Ctrl-C)")
    ap.add_argument("--status-every", type=float, default=2.0,
                    help="seconds between status lines (0 = off)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(0 if selftest() else 1)

    m = Monitor(a.log, quiet=False)
    try:
        if a.replay:
            print(f"replaying {a.replay}")
            for t, cid, d in read_replay(a.replay):
                m.feed(t, cid, d)
        else:
            print(f"listening on {a.iface} -- READ-ONLY, nothing transmitted")
            print("drive up through ~65 kph and back down; Ctrl-C to stop\n")
            t0 = time.time()
            last = 0.0
            for t, cid, d in read_live(a.iface):
                m.feed(t, cid, d)
                now = time.time()
                if a.status_every and now - last >= a.status_every:
                    last = now
                    print("  " + m.status_line(), flush=True)
                if a.seconds and now - t0 >= a.seconds:
                    break
    except KeyboardInterrupt:
        print("\ninterrupted")
    except OSError as e:
        print(f"ERROR opening {a.iface}: {e}", file=sys.stderr)
        m.close()
        sys.exit(2)
    finally:
        m.report()
        if a.log:
            print(f"\nlogged: {a.log}.csv  {a.log}.jsonl  {a.log}.log")
        m.close()
    return 0


if __name__ == "__main__":
    main()
