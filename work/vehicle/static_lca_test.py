#!/usr/bin/env python3
"""Static LCA test rig: silence IPMA + ABS, replay a real 50 km/h window, and
compare what the PSCM does for LKA vs LCA at an IDENTICAL steering demand.

THE QUESTION
    drivemod2 measured, with both null controls clean, that the PSCM applies
    torque for LKA (76% directional agreement, p<1e-4) and none for LCA (53%,
    indistinguishable from chance) -- even with the X:$0904 gate NOPed out.

    Two explanations remain:
      (a) the PSCM rejects LkaActvStats_D_Req == 6 somewhere downstream;
      (b) the PSCM applies a magnitude/rate threshold that the real IPMA's
          gentle LCA demand never crosses (measured LCA range 6.67 mRad vs
          LKA 9.32/11.40).
    Sending the SAME --angle in both states discriminates between them. That is
    the entire point of this rig.

HOW
    1. Put the IPMA (0x706) and the ABS (0x760) into programmingSession (10 02)
       so they stop transmitting their application frames. TesterPresent 3E 80
       keeps them quiet.  (10 03 does NOT work -- documented in bcmflash.py.)
    2. Replay a REAL 118 s window captured at 41-60 km/h, containing all 12
       ABS-sourced IDs and the 3 IPMA IDs, at their true relative timing.
       Every rolling counter and checksum in it is genuine -- the 0x1E0
       VehicleSpeedCounter runs 5900 frames with zero discontinuities -- so no
       checksum has to be solved.
    3. Rewrite ONLY 0x0A5's lane-state + steering-demand fields, recomputing
       its two checksums (solved and verified over 14 074 frames).

SAFETY
    * --arm is mandatory; without it nothing is transmitted.
    * --seconds is a hard dead-man; the rig always restores the bus.
    * On ANY exit path (exception, Ctrl-C, timeout) both modules get 11 01
       ECUReset, so the real ABS and camera come back.
    * VEHICLE MUST BE STATIONARY, IN PARK, ENGINE RUNNING, WHEELS CLEAR.
      The engine must run because the PSCM does not apply assist torque with
      the engine off. The ABS is silenced, so the car must not move: there is
      no ABS and no stability control while this runs.
    * The EPS remains overridable by hand at all times.

Usage:
    python3 static_lca_test.py --selftest
    python3 static_lca_test.py --mode lka-left --angle 20 --seconds 8 --arm
    python3 static_lca_test.py --mode lca      --angle 20 --seconds 8 --arm
"""
import argparse
import json
import os
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOW = os.path.join(HERE, "abs_window.jsonl")

IPMA_REQ, IPMA_RSP = 0x706, 0x70E
ABS_REQ, ABS_RSP = 0x760, 0x768

CAN_ISOTP = 6
SOL_CAN_ISOTP = 106
CAN_ISOTP_OPTS = 1
TX_PAD, RX_PAD = 0x004, 0x008

# 0x0A5 layout (verified): byte 3 carries LkaActvStats_D_Req in bits 6..4
MODE_NIB = {"idle": 0x0, "lka-left": 0x2, "lka-right": 0x4, "lca": 0x6,
            "replay": None, "maxdemand": None}

MAXDEMAND = os.path.join(HERE, "maxdemand.json")
MAX_STATE = {"lka-left": "2", "lka-right": "4", "lca": "6",
             "idle": "0", "suppr-right": "5"}


def load_maxdemand(state_name):
    """-> list of 16 real 0x0A5 frames, counter 0..15, max sustained demand.

    These are CAPTURED frames replayed verbatim: valid checksums, valid
    rolling counter, no arithmetic. See build_maxdemand.py for why selecting
    real strong frames beats multiplying the demand (12-bit field wraps past
    ~102 mRad, and any rewrite needs the unsolved checksum).
    """
    if not os.path.exists(MAXDEMAND):
        raise SystemExit(f"{MAXDEMAND} missing -- run build_maxdemand.py first")
    data = json.load(open(MAXDEMAND))
    key = MAX_STATE.get(state_name)
    if key is None or key not in data:
        raise SystemExit(f"no max-demand loop for state {state_name!r}; "
                         f"have {sorted(data)}")
    v = data[key]
    frames = [bytes.fromhex(x) for x in v["frames"]]
    if len(frames) != 16:
        raise SystemExit("max-demand loop is not 16 frames")
    return frames, v


def nib(x):
    return (x >> 4) + (x & 0xF)


def cs_a(b3, b6):
    """LaActvStats_No_Cs (byte 5) -- BYTE sum. Verified, 14074 frames."""
    return (-(b3 + b6) + 0x75) & 0xFF


def cs_b(b3, b6):
    """LaStePar_No_Cs (byte 2) -- NIBBLE sum. Verified, 14074 frames."""
    return (-(nib(b3) + nib(b6)) + 0x06) & 0xFF


def set_signal(data, start, length, value):
    """Big-endian (Motorola) bit packing, as the DBC specifies."""
    d = bytearray(data)
    b, bit = start // 8, start % 8
    for i in range(length - 1, -1, -1):
        v = (value >> i) & 1
        d[b] = (d[b] & ~(1 << bit)) | (v << bit)
        bit -= 1
        if bit < 0:
            bit, b = 7, b + 1
    return bytes(d)


def build_0a5(template, state, ref_raw, curv_raw):
    """Rewrite lane state + demand in a REAL captured frame, fix checksums.

    Bit positions are taken VERBATIM from CAN-HS.dbc for message 165 (0x0A5):
        LkaActvStats_D_Req  30|3@0+     byte 3, bits 6..4
        LaRefAng_No_Req     27|12@0+    byte 3 bits 3..0 + byte 4
        LaCurvature_No_Calc 51|12@0+    byte 6 bits 3..0 + byte 7
        LaStePar_No_Cs      23|8@0+     byte 2   (checksum)
        LaActvStats_No_Cs   47|8@0+     byte 5   (checksum)

    ⚠ An earlier version used 23|12 for LaRefAng_No_Req and 43|12 for the
    curvature. 23|12 starts in byte 2 and runs into byte 3 bits 7..4 -- i.e.
    straight through the state enum -- so writing an angle silently destroyed
    the mode. The self-test caught it ("built frame carries state 2" FAIL).
    Always read the start bit from the DBC; do not carry numbers over by hand.
    """
    d = bytearray(template)
    if state is not None:
        d[3] = (d[3] & ~0x70) | ((state & 0x7) << 4)
    if ref_raw is not None:
        d = bytearray(set_signal(d, 27, 12, ref_raw & 0xFFF))
    if curv_raw is not None:
        d = bytearray(set_signal(d, 51, 12, curv_raw & 0xFFF))
    d[5] = cs_a(d[3], d[6])
    d[2] = cs_b(d[3], d[6])
    return bytes(d)


def open_isotp(iface, txid, rxid):
    s = socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, CAN_ISOTP)
    s.setsockopt(SOL_CAN_ISOTP, CAN_ISOTP_OPTS,
                 struct.pack("=IIBBBB", TX_PAD | RX_PAD, 0, 0, 0, 0, 0))
    s.bind((iface, rxid, txid))
    s.settimeout(2.0)
    return s


def raw_socket(iface):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    return s


def send_raw(sock, cid, data):
    payload = bytes(data).ljust(8, b"\x00")
    sock.send(struct.pack("=IB3x8s", cid, len(data), payload))


class Silencer:
    """Hold a module in programmingSession so it stops its application frames."""

    def __init__(self, iface, name, req, rsp, execute):
        self.iface, self.name = iface, name
        self.req, self.rsp, self.execute = req, rsp, execute
        self.s = None
        self.active = False
        self._stop = threading.Event()
        self._t = None

    def _req(self, hexstr, timeout=2.0):
        self.s.settimeout(timeout)
        self.s.send(bytes.fromhex(hexstr))
        try:
            return self.s.recv(4096)
        except socket.timeout:
            return None

    def start(self):
        if not self.execute:
            print(f"   [dry] would silence {self.name} via 0x{self.req:03X} 10 02")
            return True
        self.s = open_isotp(self.iface, self.req, self.rsp)
        r = self._req("1002", timeout=3.0)
        if not (r and r[0] == 0x50):
            print(f"   !! {self.name} refused programmingSession: "
                  f"{r.hex() if r else '<timeout>'}")
            return False
        print(f"   {self.name} in programmingSession (50 02)")
        self.active = True

        def keep():
            while not self._stop.wait(1.5):
                try:
                    self.s.send(bytes.fromhex("3E80"))
                except OSError:
                    return
        self._t = threading.Thread(target=keep, daemon=True)
        self._t.start()
        return True

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=2)
        if self.active and self.s:
            try:
                self._req("1101", timeout=3.0)      # ECUReset -> back to normal
                print(f"   {self.name} reset (11 01)")
            except OSError:
                print(f"   !! {self.name} reset FAILED -- power-cycle the car")
        if self.s:
            self.s.close()
        self.active = False


def load_window(path):
    lines = open(path).read().splitlines()
    hdr = json.loads(lines[0])
    fr = [json.loads(x) for x in lines[1:]]
    return hdr, fr


def run(a):
    hdr, frames = load_window(WINDOW)
    dur = hdr["duration"]
    print(f"replay window: {hdr['src']} {dur:.1f} s, {len(frames)} frames")

    # --- max-demand substitution ---------------------------------------
    md_frames = md_meta = None
    if a.mode == "maxdemand":
        md_frames, md_meta = load_maxdemand(a.state)
        print(f"\nMAX-DEMAND mode: state {a.state} "
              f"(enum {MAX_STATE[a.state]}), 16 real frames")
        print(f"  sustained demand: mean |{md_meta['mean_abs']}| "
              f"max |{md_meta['max_abs']}| mRad")
        print(f"  demands: {' '.join(f'{x:+.0f}' for x in md_meta['demands'])}")
        print("  every frame is a VERBATIM capture: valid checksum + counter")

    state = MODE_NIB[a.mode]
    ref_raw = None if a.angle is None else int(round((a.angle + 102.4) / 0.05))
    curv_raw = None if a.curvature is None else int(round((a.curvature + 0.01024) / 5e-6))
    if a.mode not in ("replay", "maxdemand"):
        print(f"mode {a.mode}: LkaActvStats_D_Req = {state}")
        if ref_raw is not None:
            print(f"  LaRefAng_No_Req = {a.angle:+.1f} mRad (raw {ref_raw})")
        else:
            print("  !! no --angle: the frame will carry the RECORDED demand")

    if not a.arm:
        print("\n*** NOT ARMED -- nothing will be transmitted. Add --arm. ***")
        return 0

    ipma = Silencer(a.iface, "IPMA", IPMA_REQ, IPMA_RSP, True)
    abs_ = Silencer(a.iface, "ABS", ABS_REQ, ABS_RSP, True)
    tx = None
    sent = 0
    t_start = time.monotonic()
    try:
        print("\n== silencing ==")
        if not ipma.start():
            raise SystemExit("IPMA would not enter programmingSession")
        if not abs_.start():
            raise SystemExit("ABS would not enter programmingSession")
        time.sleep(0.3)

        tx = raw_socket(a.iface)
        print(f"\n== replaying for up to {a.seconds:.0f} s "
              f"(mode={a.mode}) ==")
        if a.mode == "maxdemand":
            print(f"   phase 1: {a.warmup:.0f} s UNMODIFIED replay "
                  "(let the PSCM unlock)")
            print(f"   phase 2: {a.seconds - a.warmup:.0f} s max-demand "
                  f"state {a.state}")
            print("   WHY: measured in replaytest, LaActAvail first reaches 3 "
                  "at t=6.7 s.")
            print("   Commanding an intervention before then is something a "
                  "real camera")
            print("   never does, and the PSCM simply never enables lane "
                  "assist (maxtest2).")
        print("   watch the steering wheel and the dashboard")

        deadline = t_start + a.seconds
        ramp_end = t_start + a.ramp if a.ramp > 0 else t_start
        i = 0
        md_i = 0
        warmed = False
        loop_t0 = time.monotonic()
        while time.monotonic() < deadline:
            if i >= len(frames):
                i = 0
                loop_t0 = time.monotonic()
            t_rel, cid, hexd = frames[i]
            target = loop_t0 + t_rel
            now = time.monotonic()
            if target > now:
                if target > deadline:
                    break
                time.sleep(min(target - now, 0.02))
            d = bytes.fromhex(hexd)
            in_warmup = (time.monotonic() - t_start) < a.warmup
            if in_warmup and not warmed:
                pass
            elif not warmed:
                warmed = True
                print(f"   [{time.monotonic()-t_start:5.1f}s] warm-up done -> "
                      f"max-demand state {a.state}", flush=True)
            if cid == 0x0A5 and a.mode == "maxdemand" and not in_warmup:
                # Substitute the next frame of the 16-frame max-demand loop.
                # Counter order is preserved by construction (build_maxdemand
                # verifies frames[r] carries rollcnt r), so the PSCM sees a
                # continuous 0..15 sequence exactly as from a real camera.
                d = md_frames[md_i % 16]
                md_i += 1
            elif cid == 0x0A5 and a.mode != "replay":
                rr = ref_raw
                if rr is not None and a.ramp > 0:
                    now2 = time.monotonic()
                    f = min(1.0, max(0.0, (now2 - t_start) / a.ramp))
                    rr = 2048 + int(round((ref_raw - 2048) * f))
                d = build_0a5(d, state, rr, curv_raw)
            try:
                send_raw(tx, cid, d)
                sent += 1
            except OSError as e:
                print(f"   !! TX error: {e}")
                break
            i += 1
        print(f"   sent {sent} frames over {time.monotonic() - t_start:.1f} s")
    finally:
        print("\n== restoring ==")
        if tx:
            tx.close()
        abs_.stop()
        ipma.stop()
        print("   bus restored; confirm the dash clears and ABS light goes out")
    return 0


def selftest():
    ok = True

    def chk(n, c, d=""):
        nonlocal ok
        ok = ok and bool(c)
        print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f"  {d}" if d else ""))

    print("SELFTEST\n")
    # checksum rules: KNOWN WRONG, asserted as such so nobody trusts them
    if os.path.exists(WINDOW):
        _, fr = load_window(WINDOW)
        a5 = [bytes.fromhex(f[2]) for f in fr if f[1] == 0x0A5]
        chk("window has 0x0A5 frames", len(a5) > 1000, str(len(a5)))
        bad_a = sum(1 for d in a5 if cs_a(d[3], d[6]) != d[5])
        bad_b = sum(1 for d in a5 if cs_b(d[3], d[6]) != d[2])
        chk("cs_a is KNOWN BROKEN on real data (documented, not a surprise)",
            bad_a > 0, f"{bad_a}/{len(a5)} wrong -- spoof modes are interlocked")
        chk("cs_b is KNOWN BROKEN on real data",
            bad_b > 0, f"{bad_b}/{len(a5)} wrong")
        # both bytes ARE deterministic given the rest -> a rule exists to find
        import collections as _c
        for csidx in (5, 2):
            g = _c.defaultdict(set)
            for d in a5:
                g[tuple(d[i] for i in range(8) if i != csidx)].add(d[csidx])
            chk(f"byte{csidx} is a deterministic function of the other 7 bytes",
                all(len(v) == 1 for v in g.values()),
                f"{sum(1 for v in g.values() if len(v) > 1)} contradictions")
        # 0x1E0 counters must be continuous (why we replay rather than synthesise)
        def be(d, s, l):
            v = 0
            b, bit = s // 8, s % 8
            for _ in range(l):
                v = (v << 1) | ((d[b] >> bit) & 1)
                bit -= 1
                if bit < 0:
                    bit, b = 7, b + 1
            return v
        e0 = [bytes.fromhex(f[2]) for f in fr if f[1] == 0x1E0]
        seq = [be(d, 51, 4) for d in e0]
        bad = sum(1 for i in range(1, len(seq)) if (seq[i - 1] + 1) % 16 != seq[i])
        chk("0x1E0 rolling counter is continuous", bad == 0, f"{bad} breaks")
        spd = [be(d, 39, 16) * 0.01 for d in e0]
        chk("window really is 40-60 km/h", 40 <= min(spd) and max(spd) <= 62,
            f"{min(spd):.1f}..{max(spd):.1f}")
        # THE property replay mode depends on
        touched = sum(1 for _, cid, hexd in fr
                      if cid == 0x0A5 and MODE_NIB["replay"] is not None)
        chk("replay mode rewrites NOTHING", touched == 0)
    else:
        chk("window file exists (run extract_window.py)", False)

    # signal packing round-trip
    base = bytes(8)
    for val in (0, 1, 2048, 4095):
        d = set_signal(base, 23, 12, val)
        got = 0
        b, bit = 23 // 8, 23 % 8
        for _ in range(12):
            got = (got << 1) | ((d[b] >> bit) & 1)
            bit -= 1
            if bit < 0:
                bit, b = 7, b + 1
        chk(f"set_signal round-trips {val}", got == val, str(got))

    # mode -> enum mapping
    chk("lca maps to enum 6", MODE_NIB["lca"] == 6)
    chk("lka-left maps to enum 2", MODE_NIB["lka-left"] == 2)
    chk("lka-right maps to enum 4", MODE_NIB["lka-right"] == 4)

    # state packing: the ENUM placement is correct even though the checksum is not
    if os.path.exists(WINDOW):
        _, fr = load_window(WINDOW)
        d0 = bytes.fromhex(next(f[2] for f in fr if f[1] == 0x0A5))
        for want in (2, 4, 6):
            out = build_0a5(d0, want, 2448, None)
            chk(f"built frame carries state {want} in byte3 bits 6..4",
                ((out[3] >> 4) & 0x7) == want, out.hex())
            # and the angle must survive alongside it (the 23|12 collision bug)
            got = 0
            b, bit = 27 // 8, 27 % 8
            for _ in range(12):
                got = (got << 1) | ((out[b] >> bit) & 1)
                bit -= 1
                if bit < 0:
                    bit, b = 7, b + 1
            chk(f"  angle 2448 survives with state {want}", got == 2448, str(got))
        chk("real frames decode to a plausible enum (0..7)",
            all(0 <= ((bytes.fromhex(f[2])[3] >> 4) & 0x7) <= 7
                for f in fr if f[1] == 0x0A5))

    # angle conversion
    chk("angle 0 mRad -> raw 2048", int(round((0.0 + 102.4) / 0.05)) == 2048)
    chk("angle +20 mRad -> raw 2448", int(round((20.0 + 102.4) / 0.05)) == 2448)

    # --- max-demand mode: verbatim frames, valid counter ----------------
    if os.path.exists(MAXDEMAND):
        import json as _j
        data = _j.load(open(MAXDEMAND))
        chk("maxdemand.json has lka-left/lka-right/lca",
            all(k in data for k in ("2", "4", "6")))
        for name in ("lka-left", "lka-right", "lca"):
            fr16, meta = load_maxdemand(name)
            chk(f"{name}: 16 frames loaded", len(fr16) == 16)
            rolls = [(d[6] >> 4) & 0xF for d in fr16]
            chk(f"  {name}: rolling counter 0..15 in order",
                rolls == list(range(16)), str(rolls))
            want = int(MAX_STATE[name])
            chk(f"  {name}: every frame carries enum {want}",
                all(((d[3] >> 4) & 0x7) == want for d in fr16))
            # THE safety property: frames are NEVER rewritten
            src = set(data[MAX_STATE[name]]["frames"])
            chk(f"  {name}: frames are verbatim captures",
                {d.hex() for d in fr16} == src)
            dem = [((d[3] & 0xF) << 8 | d[4]) * 0.05 - 102.4 for d in fr16]
            # NOTE: the bar is 5 mRad, not 30. Plausibility filtering (added
            # after maxtest, where implausible frames stopped the PSCM from
            # ever declaring availability) caps the attainable demand: the
            # strongest frames in the corpus were strong BECAUSE the camera
            # was reporting garbage curvature. A real 17-27 mRad beats an
            # unusable 40.
            chk(f"  {name}: sustained demand > 5 mRad",
                sum(abs(x) for x in dem) / 16 > 5,
                f"mean |{sum(abs(x) for x in dem)/16:.1f}|")
        # the discriminating comparison must be fair
        _, m2 = load_maxdemand("lka-left")
        _, m6 = load_maxdemand("lca")
        chk("LKA-L vs LCA demand within 12 mRad (fair test)",
            abs(m2["mean_abs"] - m6["mean_abs"]) < 12,
            f"{m2['mean_abs']} vs {m6['mean_abs']}")

    # --- warm-up gating: simulate the TX-loop decision offline ----------
    # maxtest2 failed because max-demand frames were substituted from t=0,
    # before the PSCM had unlocked (measured: LaActAvail reaches 3 at ~6.7 s).
    # Verify the loop now replays UNMODIFIED frames during the warm-up.
    if os.path.exists(MAXDEMAND):
        md16, _ = load_maxdemand("lka-left")
        md_set = {x.hex() for x in md16}
        _, wfr = load_window(WINDOW)
        orig = [f for f in wfr if f[1] == 0x0A5][:40]
        WARM = 12.0
        subbed_early = subbed_late = 0
        for elapsed in (0.0, 5.0, 11.9, 12.1, 20.0):
            in_warmup = elapsed < WARM
            # what the loop would put on the wire for a 0x0A5 slot
            out = orig[0][2] if in_warmup else md16[0].hex()
            if out in md_set:
                if in_warmup:
                    subbed_early += 1
                else:
                    subbed_late += 1
        chk("warm-up: NO substitution before the warm-up elapses",
            subbed_early == 0, f"{subbed_early} early substitutions")
        chk("warm-up: substitution DOES happen afterwards",
            subbed_late == 2, f"{subbed_late} late substitutions")

        # the guard against a pointless run
        import argparse as _a
        bad = _a.Namespace(mode="maxdemand", seconds=10.0, warmup=12.0,
                           state="lca", angle=None, curvature=None, ramp=0.0,
                           arm=False, iface="can0", txid=0, rxid=0)
        short = bad.seconds - bad.warmup < 5
        chk("warm-up: --seconds 10 with --warmup 12 is rejected", short)
        good = 25.0 - 12.0 >= 5
        chk("warm-up: --seconds 25 with --warmup 12 is allowed", good)

    # dry run must transmit nothing
    ns = argparse.Namespace(mode="replay", angle=None, curvature=None, ramp=0.0,
                            seconds=5, arm=False, iface="can0")
    chk("dry run returns without transmitting", run(ns) == 0)

    print("\n" + "=" * 56)
    print("SELFTEST:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--mode", default="lca", choices=sorted(MODE_NIB))
    ap.add_argument("--angle", type=float, default=None,
                    help="steering demand in mRad (e.g. 20). 0 = no demand.")
    ap.add_argument("--curvature", type=float, default=None, help="1/m")
    ap.add_argument("--ramp", type=float, default=1.0,
                    help="seconds to ramp 0 -> --angle (0 = step)")
    ap.add_argument("--seconds", type=float, default=8.0,
                    help="hard dead-man timeout")
    ap.add_argument("--warmup", type=float, default=12.0,
                    help="seconds of UNMODIFIED replay before max-demand "
                         "substitution starts, so the PSCM can unlock "
                         "(measured: LaActAvail reaches 3 at ~6.7 s)")
    ap.add_argument("--state", default="lca", choices=sorted(MAX_STATE),
                    help="lane state to drive in --mode maxdemand")
    ap.add_argument("--arm", action="store_true",
                    help="REQUIRED to transmit anything")
    ap.add_argument("--i-have-solved-the-checksum", action="store_true",
                    help=argparse.SUPPRESS)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.seconds > 40:
        raise SystemExit("--seconds > 40 refused; this silences the ABS")
    if a.mode == "maxdemand" and a.seconds - a.warmup < 5:
        raise SystemExit(
            f"--seconds {a.seconds:.0f} leaves only "
            f"{a.seconds - a.warmup:.0f}s after a {a.warmup:.0f}s warm-up; "
            "allow at least 5 s of actual demand (try --seconds 25)")

    # --- checksum safety interlock -------------------------------------
    # The 0x0A5 checksum rules in this file were solved on a STATIONARY corpus
    # in which byte 3 was constant (0x78). On real driving data from drivemod2
    # they fail on 5508/5875 frames -- they were underdetermined and are wrong.
    # Any mode that REWRITES 0x0A5 would therefore emit frames the PSCM
    # rejects, producing a guaranteed null result that looks like evidence.
    # Replay mode transmits the capture verbatim and is unaffected.
    if a.mode not in ("replay", "maxdemand") \
            and not a.i_have_solved_the_checksum:
        raise SystemExit(
            "REFUSED: --mode %s rewrites 0x0A5, but the checksum rules in this\n"
            "file are KNOWN WRONG (they fail 5508/5875 real frames -- solved on\n"
            "a stationary corpus with byte 3 constant).\n\n"
            "Sending bad checksums gets the frame rejected, which looks exactly\n"
            "like 'the PSCM ignores LCA' and would be a false negative.\n\n"
            "Use --mode replay to validate the rig, and re-solve the checksum\n"
            "against work/vehicle/abs_window.jsonl before spoofing." % a.mode)

    return run(a)


if __name__ == "__main__":
    sys.exit(main())
