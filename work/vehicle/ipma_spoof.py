#!/usr/bin/env python3
"""Full IPMA emulator for the Ford HS CAN bus.

Transmits the three functional messages a real IPMA sends, so the PSCM *and*
the instrument cluster both see a coherent camera:

    0x0A5  IPMA_h_FrP01   lane-assist command  -> PSCM   (the steering request)
    0x1B5  IPMA_h_FrP02   lane-assist display  -> BCM    (what the dash shows)
    0x298  IPMA_h_FrP00   camera status/AHB    -> BCM/HCM

Spoofing only 0x0A5 would make the PSCM act while the cluster showed nothing,
because the dashboard indicators are driven from 0x1B5 by the BCM, and the
BCM will also see the camera as failed unless 0x298 reports it healthy.

SAFETY
------
* Requires --arm to transmit.  Without it the tool prints frames and exits.
* Dead-man timeout (--seconds, default 20) then sends an idle/neutral frame
  set and stops.  Ctrl-C does the same.
* Torque only results if the PSCM accepts the request; keep hands clear of the
  wheel rim and be ready to switch off the ignition.
* The real IPMA MUST be silenced first (diagnostic session, or unplugged) or
  two senders will contend on the same IDs and the results are meaningless.

Checksums: 0x0A5 carries two (LaActvStats_No_Cs, LaStePar_No_Cs).  Their
algorithm is NOT known yet -- supply it via --cs-rule once solve_checksum.py
has produced one.  Without it the tool refuses to arm, because an unvalidated
checksum produces a silent-reject that looks exactly like "feature absent".

Usage:
    python3 ipma_spoof.py --selftest
    python3 ipma_spoof.py --mode lka-left --dry-run
    python3 ipma_spoof.py --mode lka-left --cs-rule "sum:1,3,6:+:0" --arm
"""
import argparse
import socket
import struct
import sys
import time

# ---------------------------------------------------------------- signals --
# name: (start_bit, length)   DBC Motorola/@0+
S_0A5 = {
    "LdwActvIntns_D_Req": (9, 2),
    "LaCurvature_No_Calc_UB": (11, 1),
    "LaRefAng_No_Req_UB": (10, 1),
    "LdwActvStats_D_Req": (14, 3),
    "LaStePar_No_Cs": (23, 8),
    "LaRefAng_No_Req": (27, 12),
    "LkaActvStats_D_Req": (30, 3),
    "LaRampType_B_Req": (31, 1),
    "LaActvStats_No_Cs": (47, 8),
    "LaCurvature_No_Calc": (51, 12),
    "LaActvReq_No_RollCnt": (55, 4),
}
S_1B5 = {
    "LaLLineStats_D_Dsply": (1, 2), "LkaVLvl_B_Dsply": (2, 1),
    "LcaVLvl_B_Dsply": (3, 1), "TsrSysInf_D_Dsply": (7, 4),
    "TsrCountr_D_Actl": (12, 5), "LaRLineStats_D_Dsply": (14, 2),
    "LdwVLvl_B_Dsply": (15, 1), "TsrFusionStat_D_Dsply": (18, 3),
    "TsrVLimUnit_D_Dsply": (20, 2), "LaDenyStats_B_Dsply": (21, 1),
    "LaHandsOff_D_Dsply": (23, 2), "LaMenuEnbl_B_Actl": (24, 1),
    "LaMenuConfg_B_Actl": (25, 1), "TsrVLim1Type_B_Dsply": (26, 1),
    "TsrVLim1Rstrct_B_Dsply": (27, 1), "TsrVLim2Type_B_Dsply": (28, 1),
    "TsrVLim2Rstrct_B_Dsply": (29, 1), "TsrOvtkType_B_Dsply": (30, 1),
    "TsrCountr_D_Actl_UB": (31, 1), "TsrVLim2Attrb_D_Dsply": (33, 2),
    "TsrOvtkAttrb_D_Dsply": (35, 2), "LcaMenuStats_B_Actl": (36, 1),
    "LkaMenuStats_B_Actl": (37, 1), "LdwMenuStats_B_Actl": (38, 1),
    "LaMenuSens_B_Actl": (39, 1), "TsrOvtkStat_D_Dsply": (42, 3),
    "LdwMenuIntns_D_Actl": (44, 2), "TsrVLim1Attrb_D_Dsply": (46, 2),
    "TsrFusionStat_D_Dsply_UB": (47, 1), "TsrVLim1_D_Dsply": (55, 8),
    "TsrVLim2_D_Dsply": (63, 8),
}
S_298 = {
    "AhbcSens_D_Actl": (26, 2), "AhbcSens_D_Actl_UB": (27, 1),
    "AhbcMenu_B_Actl_UB": (24, 1), "DasSwtch_B_Actl_UB": (28, 1),
    "CamraDefog_B_Req": (34, 1), "AhbcHighBeam_D_Req": (36, 2),
    "CamraStats_D_Dsply_UB": (38, 1), "AhbcMenu_B_Actl": (39, 1),
    "DasSwtch_B_Actl": (40, 1), "DasStats_D_Dsply": (42, 2),
    "DasAlrtLvl_D_Dsply": (45, 3), "DasWarn_D_Dsply": (47, 2),
    "AhbcTrgtDist_D_Stat": (49, 2), "AhbcTrgtDir_D_Stat": (51, 2),
    "CamraStats_D_Dsply": (54, 2), "AhbcTrgtDist_L_Actl": (63, 8),
}

CYCLE_MS = {0x0A5: 20, 0x1B5: 100, 0x298: 100}   # refine from a real capture


def be_insert(data, start, length, value):
    b, bit = start // 8, start % 8
    for k in range(length - 1, -1, -1):
        data[b] = (data[b] & ~(1 << bit)) | (((value >> k) & 1) << bit)
        bit -= 1
        if bit < 0:
            bit, b = 7, b + 1
    return data


def be_extract(data, start, length):
    v = 0
    b, bit = start // 8, start % 8
    for _ in range(length):
        v = (v << 1) | ((data[b] >> bit) & 1)
        bit -= 1
        if bit < 0:
            bit, b = 7, b + 1
    return v


def build(spec, values):
    d = bytearray(8)
    for nm, v in values.items():
        st, ln = spec[nm]
        if not 0 <= v < (1 << ln):
            raise ValueError(f"{nm}={v} does not fit in {ln} bits")
        be_insert(d, st, ln, v)
    return d


# ------------------------------------------------------------- checksums --
def parse_cs_rule(s):
    """Parse a rule string into callable(frame)->int.

    Forms (as emitted by solve_rollcnt_cs.py):
        sum:<bytes>:<+|->:<k>    sum of whole bytes
        nib:<bytes>:<+|->:<k>    sum of each byte's two nibbles
        xor:<bytes>:<+|->:<k>
    e.g. 'sum:3,6:-:0x75'  ->  (-(b3+b6) + 0x75) & 0xFF
    """
    if not s:
        return None
    op, byts, sign, k = s.split(":")
    idx = [int(x, 0) for x in byts.split(",")]
    sgn = 1 if sign == "+" else -1
    kk = int(k, 0)

    def fn(fr):
        if op == "nib":
            v = sum((fr[i] >> 4) + (fr[i] & 0xF) for i in idx)
        elif op == "xor":
            v = __import__("functools").reduce(
                lambda a, b: a ^ b, [fr[i] for i in idx])
        else:
            v = sum(fr[i] for i in idx)
        return (sgn * v + kk) & 0xFF
    return fn


# Rules recovered from a 14074-frame capture (see PSCM_vehicle_test_plan.md).
# Both reproduce every frame in that corpus exactly.
DEFAULT_CS_A = "sum:3,6:-:0x75"      # LaActvStats_No_Cs -> byte 5
DEFAULT_CS_B = "nib:3,6:-:0x06"      # LaStePar_No_Cs    -> byte 2


# ------------------------------------------------------------------ modes --
# DBC scaling for the two steering-demand signals on 0x0A5:
#   LaRefAng_No_Req      (0.05,  -102.4)   -> raw = (mRad + 102.4) / 0.05
#   LaCurvature_No_Calc  (5e-6, -0.01024)  -> raw = (1/m + 0.01024) / 5e-6
# Both have raw 2048 == exactly 0.0, i.e. "steer by nothing".
REFANG_SCALE, REFANG_OFF = 0.05, -102.4
CURV_SCALE, CURV_OFF = 5e-6, -0.01024
NEUTRAL = 2048


def refang_raw(mrad):
    return max(0, min(4095, round((mrad - REFANG_OFF) / REFANG_SCALE)))


def refang_mrad(raw):
    return raw * REFANG_SCALE + REFANG_OFF


def curv_raw(inv_m):
    return max(0, min(4095, round((inv_m - CURV_OFF) / CURV_SCALE)))


def curv_inv_m(raw):
    return raw * CURV_SCALE + CURV_OFF


def mode_values(mode, ref_angle_raw, curvature_raw):
    """LkaActvStats values: 2=Interv Left, 4=Interv Right, 6=LCA in Progress.

    IMPORTANT -- the enum alone commands NOTHING.  LkaActvStats_D_Req only
    declares *what kind* of intervention is happening; the actual steering
    demand lives in LaRefAng_No_Req / LaCurvature_No_Calc.  Raw 2048 on both
    means 0.0 mRad and 0.0 1/m, so a run with LkaActvStats=2 and the defaults
    asks the PSCM to intervene by exactly zero: the dashboard lights up and the
    wheel correctly does not move.  Use --angle / --curvature to command a
    real demand.
    """
    lka = {"idle": 0, "lka-left": 2, "lka-right": 4, "lca": 6}[mode]
    return {
        "LkaActvStats_D_Req": lka,
        "LdwActvStats_D_Req": 0,
        "LdwActvIntns_D_Req": 0,
        "LaRampType_B_Req": 0,                 # 0 = Smooth
        "LaRefAng_No_Req": ref_angle_raw,
        "LaCurvature_No_Calc": curvature_raw,
        "LaRefAng_No_Req_UB": 1,
        "LaCurvature_No_Calc_UB": 1,
    }


def display_values(mode):
    """What the cluster should show.  Lines detected + in speed range."""
    active = mode != "idle"
    return {
        "LaLLineStats_D_Dsply": 2 if active else 0,   # Not overridable
        "LaRLineStats_D_Dsply": 2 if active else 0,
        "LkaVLvl_B_Dsply": 1, "LcaVLvl_B_Dsply": 1, "LdwVLvl_B_Dsply": 1,
        "LaMenuEnbl_B_Actl": 1,                       # LA enabled
        "LkaMenuStats_B_Actl": 1, "LcaMenuStats_B_Actl": 1,
        "LdwMenuStats_B_Actl": 1, "LaMenuSens_B_Actl": 1,
        "LaHandsOff_D_Dsply": 0, "LaDenyStats_B_Dsply": 0,
        "LdwMenuIntns_D_Actl": 2,
    }


def camera_values():
    return {"CamraStats_D_Dsply": 0,            # Front Camera OK
            "CamraStats_D_Dsply_UB": 1,
            "AhbcMenu_B_Actl_UB": 1, "AhbcSens_D_Actl_UB": 1,
            "DasSwtch_B_Actl_UB": 1, "DasStats_D_Dsply": 0}


def load_template(path):
    """Real captured frames -> {id: (payload, cycle_ms, n)}.

    Accepts either the .jsonl corpus from capture_lane.py or a raw candump
    log, so a log taken for another purpose can be reused as a template.

    Synthesising 0x1B5 / 0x298 from DBC defaults does NOT work: every TSR,
    AHB and _UB field comes out zero, the BCM decides the camera is faulty and
    reports an IPMA malfunction -- which also suppresses lane assist, making
    the experiment meaningless.  Measured against a real capture, the
    synthesised 0x1B5 had ALL EIGHT bytes wrong across 17 signals (e.g.
    TsrVLim1_D_Dsply 255 "unknown" became 0, a valid speed limit).  Replaying
    the camera's own bytes avoids inventing any field we do not understand.
    """
    import json
    import re
    from collections import Counter, defaultdict
    frames, times = defaultdict(list), defaultdict(list)
    candump = re.compile(
        r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")
    for ln in open(path, errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("{"):
            r = json.loads(ln)
            t, cid, d = r.get("t", 0.0), r["id"], r.get("d", "")
        else:
            m = candump.match(ln)
            if not m:
                continue
            t, cid, d = (float(m.group(1)), int(m.group(2), 16),
                         m.group(3).upper())
        if len(d) != 16:
            continue
        frames[cid].append(bytes.fromhex(d))
        times[cid].append(t)
    out = {}
    for cid, fl in frames.items():
        payload = Counter(fl).most_common(1)[0][0]
        t = sorted(times[cid])
        d = sorted(t[i + 1] - t[i] for i in range(len(t) - 1))
        cycle = d[len(d) // 2] * 1000 if d else None
        out[cid] = (payload, cycle, len(fl))
    return out


def frames_for(mode, rollcnt, ref_raw, curv_raw, cs_a=None, cs_b=None,
               template=None, drive_display=False):
    """Build the three IPMA frames.

    template: {id: (payload, cycle_ms, n)} from a real capture.  When present,
    each frame starts as the camera's OWN bytes and only the fields this
    experiment needs are overwritten -- so every field we do not understand
    keeps its real value.

    drive_display: also force the 0x1B5 lane indicators to "active".  Off by
    default: first prove the camera looks healthy (verbatim replay), only then
    start driving the dashboard.
    """
    # ---- pure replay: touch nothing at all (the invisibility control) --
    if mode == "replay":
        if not (template and 0x0A5 in template):
            raise ValueError("--mode replay requires --template")
        return {cid: bytes(template[cid][0]) for cid in (0x0A5, 0x1B5, 0x298)
                if cid in template}

    # ---- 0x0A5: the lane-assist command -------------------------------
    if template and 0x0A5 in template:
        f0a5 = bytearray(template[0x0A5][0])
        for nm, v in mode_values(mode, ref_raw, curv_raw).items():
            be_insert(f0a5, *S_0A5[nm], v)
    else:
        f0a5 = build(S_0A5, mode_values(mode, ref_raw, curv_raw))
    be_insert(f0a5, *S_0A5["LaActvReq_No_RollCnt"], rollcnt & 0xF)
    if cs_a:
        be_insert(f0a5, *S_0A5["LaActvStats_No_Cs"], cs_a(f0a5))
    if cs_b:
        be_insert(f0a5, *S_0A5["LaStePar_No_Cs"], cs_b(f0a5))

    # ---- 0x1B5 / 0x298: replay verbatim if we have them ---------------
    if template and 0x1B5 in template:
        f1b5 = bytearray(template[0x1B5][0])
        if drive_display:
            for nm, v in display_values(mode).items():
                be_insert(f1b5, *S_1B5[nm], v)
    else:
        f1b5 = build(S_1B5, display_values(mode))

    f298 = (bytearray(template[0x298][0]) if template and 0x298 in template
            else build(S_298, camera_values()))

    return {0x0A5: bytes(f0a5), 0x1B5: bytes(f1b5), 0x298: bytes(f298)}


# ---------------------------------------------------------------- selftest --
def selftest():
    ok = True

    def chk(n, cond, extra=""):
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL'}  {n}{extra}")

    print("-- bit packing round-trips --")
    for spec, nm, val in ((S_0A5, "LkaActvStats_D_Req", 6),
                          (S_0A5, "LaRefAng_No_Req", 0xABC),
                          (S_1B5, "LaLLineStats_D_Dsply", 2),
                          (S_298, "CamraStats_D_Dsply", 0)):
        d = build(spec, {nm: val})
        chk(f"{nm}={val}", be_extract(d, *spec[nm]) == val)

    print("-- no signal overlaps within a message --")
    for label, spec in (("0x0A5", S_0A5), ("0x1B5", S_1B5), ("0x298", S_298)):
        seen, clash = {}, []
        for nm, (st, ln) in spec.items():
            b, bit = st // 8, st % 8
            for _ in range(ln):
                if (b, bit) in seen:
                    clash.append((nm, seen[(b, bit)]))
                seen[(b, bit)] = nm
                bit -= 1
                if bit < 0:
                    bit, b = 7, b + 1
        chk(f"{label} non-overlapping", not clash, f"  {clash[:3]}")

    print("-- mode encoding is the ONLY difference between LKA and LCA --")
    a = frames_for("lka-left", 3, 2048, 2048)
    b = frames_for("lca", 3, 2048, 2048)
    d = [i for i in range(8) if a[0x0A5][i] != b[0x0A5][i]]
    chk("0x0A5 differs only where LkaActvStats lives (byte 3)", d == [3],
        f"  differing bytes {d}")
    chk("LKA left encodes 2", be_extract(a[0x0A5], 30, 3) == 2)
    chk("LCA encodes 6", be_extract(b[0x0A5], 30, 3) == 6)

    print("-- checksum rule parsing --")
    fn = parse_cs_rule("sum:1,3,6:+:0x00")
    fr = bytes([0, 0x10, 0, 0x20, 0, 0, 0x30, 0])
    chk("sum rule", fn(fr) == 0x60)
    fn2 = parse_cs_rule("sum:1,3,6:-:0xff")
    chk("negated rule", fn2(fr) == ((-0x60 + 0xFF) & 0xFF))
    chk("no rule -> None", parse_cs_rule(None) is None)

    print("-- rolling counter wraps 0..15 --")
    vals = [be_extract(frames_for("idle", i, 0, 0)[0x0A5], 55, 4)
            for i in range(18)]
    chk("wraps correctly", vals == [i & 0xF for i in range(18)])

    print("-- idle frame really is idle --")
    idle = frames_for("idle", 0, 0, 0)
    chk("LkaActvStats = 0", be_extract(idle[0x0A5], 30, 3) == 0)
    chk("lines report 'No line'", be_extract(idle[0x1B5], 1, 2) == 0)

    print("-- checksum rules reproduce the REAL captured corpus --")
    import json
    import os
    corpus = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "corpus.jsonl")
    if not os.path.exists(corpus):
        print("  SKIP: corpus.jsonl not found")
    else:
        fr = []
        for ln in open(corpus):
            ln = ln.strip()
            if not ln:
                continue
            r = json.loads(ln)
            if r.get("id") == 0x0A5 and len(r.get("d", "")) == 16:
                fr.append(bytes.fromhex(r["d"]))
        ca, cb = parse_cs_rule(DEFAULT_CS_A), parse_cs_rule(DEFAULT_CS_B)
        bad_a = sum(1 for f in fr if ca(f) != be_extract(f, 47, 8))
        bad_b = sum(1 for f in fr if cb(f) != be_extract(f, 23, 8))
        chk(f"LaActvStats_No_Cs '{DEFAULT_CS_A}' on {len(fr)} real frames",
            bad_a == 0, f"  ({bad_a} mismatches)")
        chk(f"LaStePar_No_Cs '{DEFAULT_CS_B}' on {len(fr)} real frames",
            bad_b == 0, f"  ({bad_b} mismatches)")
        # Independently: applying the rules to a REAL payload must regenerate
        # that payload's own checksum bytes.  (frames_for() cannot be used for
        # this: it rebuilds the whole payload from mode defaults, which
        # legitimately differ from the captured suppressed-state frame.)
        f0 = bytearray(fr[0])
        f0[5] = 0
        f0[2] = 0
        chk("rules regenerate a real frame's checksums from its payload",
            (ca(f0), cb(f0)) == (fr[0][5], fr[0][2]),
            f"  got {ca(f0):#04x}/{cb(f0):#04x} "
            f"want {fr[0][5]:#04x}/{fr[0][2]:#04x}")
        # and the checksum must depend on byte 3 -- the byte the experiment
        # changes -- or the whole test would be blind to the LKA/LCA switch.
        f1 = bytearray(fr[0])
        f1[3] ^= 0x50           # flip LkaActvStats bits
        chk("checksum A responds to byte 3", ca(f1) != ca(bytearray(fr[0])))
        chk("checksum B responds to byte 3", cb(f1) != cb(bytearray(fr[0])))

        print("-- template replay preserves fields we do not understand --")
        tpl = load_template(corpus)
        chk("template built from the corpus", 0x0A5 in tpl)
        real = tpl[0x0A5][0]
        out = frames_for("lka-left", 5, 2048, 2048, ca, cb, tpl)[0x0A5]
        # bytes 0,1,4,7 carry LdwActvIntns/LdwActvStats/LaRefAng low and the
        # curvature low byte; mode_values legitimately rewrites some of them,
        # but byte 0 and byte 7 are touched by NO signal this tool sets.
        untouched = set(range(8))
        for nm in list(mode_values("lka-left", 2048, 2048)) + \
                ["LaActvReq_No_RollCnt", "LaActvStats_No_Cs",
                 "LaStePar_No_Cs"]:
            st, ln = S_0A5[nm]
            b, bit = st // 8, st % 8
            for _ in range(ln):
                untouched.discard(b)
                bit -= 1
                if bit < 0:
                    bit, b = 7, b + 1
        chk(f"bytes {sorted(untouched)} kept from the real frame",
            all(out[i] == real[i] for i in untouched))
        chk("0x1B5/0x298 absent from this corpus (why the spoof failed)",
            0x1B5 not in tpl and 0x298 not in tpl)

        print("-- 'replay' mode is byte-exact (the invisibility control) --")
        fake = {0x0A5: (bytes.fromhex("007EE97800956800"), 20.0, 1),
                0x1B5: (bytes.fromhex("000002D7FAD0FFFC"), 40.1, 1),
                0x298: (bytes.fromhex("00000019D82B0000"), 40.1, 1)}
        rep = frames_for("replay", 7, 1234, 1234, ca, cb, fake, True)
        chk("every replayed frame equals its template",
            all(rep[c] == fake[c][0] for c in fake))
        chk("replay ignores mode/rollcnt/checksum arguments",
            rep[0x0A5] == fake[0x0A5][0])
        # and a synthesised 0x1B5 must NOT match the real one -- this is the
        # bug that caused the IPMA malfunction, kept as a regression guard.
        synth = bytes(build(S_1B5, display_values("lka-left")))
        chk("synthesised 0x1B5 differs from the real frame (the old bug)",
            synth != fake[0x1B5][0])
        print("-- physical units -> raw (the zero-demand bug) --")
        chk("raw 2048 is exactly 0.0 mRad", abs(refang_mrad(2048)) < 1e-9)
        chk("raw 2048 is exactly 0.0 1/m", abs(curv_inv_m(2048)) < 1e-12)
        chk("--angle 0 gives the neutral raw", refang_raw(0.0) == 2048)
        chk("--angle 40 is well away from neutral",
            refang_raw(40.0) == 2848, str(refang_raw(40.0)))
        chk("--angle round-trips", abs(refang_mrad(refang_raw(40.0)) - 40) < .05)
        chk("--angle clamps to the DBC range",
            refang_raw(9999) == 4095 and refang_raw(-9999) == 0)
        chk("--curvature 0.002 round-trips",
            abs(curv_inv_m(curv_raw(0.002)) - 0.002) < 5e-6)
        # the actual regression: mode alone must not be mistaken for a demand
        neutral = build(S_0A5, mode_values("lka-left", NEUTRAL, NEUTRAL))
        moving = build(S_0A5, mode_values("lka-left", refang_raw(40), NEUTRAL))
        chk("lka-left at neutral != lka-left with a real angle",
            bytes(neutral) != bytes(moving))
        chk("both still declare LKA Intervention Left",
            be_extract(neutral, 30, 3) == 2 and be_extract(moving, 30, 3) == 2)

    return ok


# --------------------------------------------------------------- transmit --
def raw_can_socket(iface):
    """Raw SocketCAN, as used by BCM/Research/work/flash/bcmflash.py.

    python-can is not installed on this machine and is not needed: the kernel
    exposes CAN directly through AF_CAN sockets.
    """
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    return s


def can_frame(can_id, payload):
    """struct can_frame { u32 can_id; u8 can_dlc; u8 pad,res0,res1; u8 data[8] }"""
    data = bytes(payload)
    assert len(data) <= 8
    return struct.pack("=IB3x8s", can_id, 8, data.ljust(8, b"\x00"))


def run(args, cs_a, cs_b, template, cycles):
    sock = raw_can_socket(args.iface)
    ids = sorted(cycles)
    print(f"ARMED on {args.iface}: mode={args.mode} for {args.seconds}s")
    for cid in ids:
        print(f"   {cid:#05x} every {cycles[cid]:.0f}ms")
    if args.mode != "replay":
        print("\nhands clear of the steering wheel; Ctrl-C stops and replays "
              "the camera's idle frames")
    print()
    t0 = time.time()
    next_tx = {i: 0.0 for i in cycles}
    roll = 0
    sent = 0
    try:
        while time.time() - t0 < args.seconds:
            now = time.time()
            # Ramp the demand from neutral to the target over --ramp seconds
            # so the wheel builds torque gradually instead of stepping.
            if args.ramp > 0 and args.mode not in ("idle", "replay"):
                k = min(1.0, (now - t0) / args.ramp)
                ref = int(round(NEUTRAL + (args.ref - NEUTRAL) * k))
                cur = int(round(NEUTRAL + (args.curv - NEUTRAL) * k))
            else:
                ref, cur = args.ref, args.curv
            fr = frames_for(args.mode, roll, ref, cur, cs_a,
                            cs_b, template, args.drive_display)
            for cid in ids:
                if cid in fr and now >= next_tx[cid]:
                    sock.send(can_frame(cid, fr[cid]))
                    sent += 1
                    next_tx[cid] = now + cycles[cid] / 1000.0
                    if cid == 0x0A5 and args.mode != "replay":
                        roll = (roll + 1) & 0xF
            print(f"\r  {sent} frames sent   elapsed {time.time()-t0:6.1f}s"
                  f"   demand {refang_mrad(ref):+7.2f} mRad",
                  end="", flush=True)
            time.sleep(0.002)
    except KeyboardInterrupt:
        print("\n  interrupted")
    finally:
        # Hand the bus back in the camera's own resting state.
        quiet = frames_for("replay" if template else "idle", 0, 0, 0,
                           cs_a, cs_b, template, False)
        for _ in range(10):
            for cid in ids:
                if cid in quiet:
                    sock.send(can_frame(cid, quiet[cid]))
            time.sleep(0.02)
        sock.close()
        print("\n  stopped; released the bus (stop silence_ipma.py to restore "
              "the real camera)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="idle",
                    choices=["replay", "idle", "lka-left", "lka-right", "lca"],
                    help="replay = verbatim, changes nothing (control)")
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--bustype", default="socketcan")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--ref", type=lambda s: int(s, 0), default=None,
                    help="LaRefAng_No_Req RAW (2048 = 0 mRad); prefer --angle")
    ap.add_argument("--curv", type=lambda s: int(s, 0), default=None,
                    help="LaCurvature_No_Calc RAW (2048 = 0); prefer "
                         "--curvature")
    ap.add_argument("--angle", type=float, default=None,
                    help="steering demand in mRad (e.g. 40). THIS is what "
                         "moves the wheel -- the mode enum alone commands "
                         "nothing. Range -102.4..102.35")
    ap.add_argument("--curvature", type=float, default=None,
                    help="path curvature in 1/m (e.g. 0.002)")
    ap.add_argument("--ramp", type=float, default=0.0,
                    help="seconds to ramp the demand 0 -> --angle "
                         "(0 = step; use 3-5 on a first attempt)")
    ap.add_argument("--cs-a", default=DEFAULT_CS_A,
                    help="LaActvStats_No_Cs rule (byte 5)")
    ap.add_argument("--cs-b", default=DEFAULT_CS_B,
                    help="LaStePar_No_Cs rule (byte 2)")
    ap.add_argument("--template", help="corpus .jsonl of REAL IPMA frames")
    ap.add_argument("--drive-display", action="store_true",
                    help="also force 0x1B5 lane indicators active")
    ap.add_argument("--synthesize", action="store_true",
                    help="build 0x1B5/0x298 from DBC defaults (causes IPMA "
                         "malfunction; for comparison only)")
    ap.add_argument("--arm", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest or len(sys.argv) == 1:
        sys.exit(0 if selftest() else 1)

    # Resolve the steering demand.  --angle/--curvature (physical units) win
    # over the raw forms; if neither is given we fall back to NEUTRAL, which
    # commands exactly zero steering -- and we say so loudly.
    if a.angle is not None:
        a.ref = refang_raw(a.angle)
    elif a.ref is None:
        a.ref = NEUTRAL
    if a.curvature is not None:
        a.curv = curv_raw(a.curvature)
    elif a.curv is None:
        a.curv = NEUTRAL

    cs_a = parse_cs_rule(a.cs_a)
    cs_b = parse_cs_rule(a.cs_b)

    template, cycles = None, dict(CYCLE_MS)
    if a.template:
        template = load_template(a.template)
        print(f"template: {a.template}")
        for cid in (0x0A5, 0x1B5, 0x298):
            if cid in template:
                p, cyc, n = template[cid]
                cycles[cid] = cyc or CYCLE_MS[cid]
                print(f"   {cid:#05x}  {p.hex().upper()}  {cyc:.1f}ms  "
                      f"({n} frames captured)")
            else:
                print(f"   {cid:#05x}  MISSING from template")
        missing = [c for c in (0x1B5, 0x298) if c not in template]
        if missing and not a.synthesize:
            print("\nREFUSING: no real samples of "
                  + ", ".join(f"{c:#05x}" for c in missing) + ".\n"
                  "Synthesising them from DBC defaults zeroes every TSR/AHB/UB\n"
                  "field; the BCM then reports an IPMA MALFUNCTION and lane\n"
                  "assist is suppressed -- the experiment cannot succeed.\n"
                  "Capture them first:\n"
                  "  python3 capture_lane.py --iface can0 --seconds 60 \\\n"
                  "      --ids 0x0A5,0x1B5,0x298,0x140 --out full.jsonl\n"
                  "(--synthesize overrides, but expect the malfunction.)",
                  file=sys.stderr)
            sys.exit(5)
    fr = frames_for(a.mode, 0, a.ref, a.curv, cs_a, cs_b, template,
                    a.drive_display)
    print(f"mode={a.mode}")
    print(f"  steering demand: LaRefAng={a.ref} raw = "
          f"{refang_mrad(a.ref):+.2f} mRad"
          + (f", ramped over {a.ramp:.1f}s" if a.ramp else "")
          + f"   LaCurvature={a.curv} raw = {curv_inv_m(a.curv):+.6f} 1/m")
    if a.mode not in ("idle", "replay") and a.ref == NEUTRAL \
            and a.curv == NEUTRAL:
        print("  *** WARNING: demand is ZERO -- the mode enum alone does not")
        print("      move the wheel. The dashboard will light up and nothing")
        print("      else will happen. Pass --angle (mRad), e.g. --angle 40.")
    print(f"  LaActvStats_No_Cs (b5): {a.cs_a or 'NOT SET'}")
    print(f"  LaStePar_No_Cs    (b2): {a.cs_b or 'NOT SET'}")
    for cid in sorted(fr):
        src = ("replay" if template and cid in template and
               not (cid == 0x0A5 or (cid == 0x1B5 and a.drive_display))
               else "built")
        print(f"  {cid:#05x}  {fr[cid].hex().upper()}  "
              f"every {cycles[cid]:.0f}ms  [{src}]")

    if a.dry_run or not a.arm:
        print("\nnot transmitting (use --arm).")
        return
    if not (cs_a and cs_b):
        print("\nREFUSING TO ARM: no --cs-rule supplied.\n"
              "An invalid checksum is silently rejected by the PSCM, which is\n"
              "indistinguishable from 'the feature does not exist'.  Solve the\n"
              "checksum first:  solve_checksum.py corpus.jsonl", file=sys.stderr)
        sys.exit(3)
    sys.exit(run(a, cs_a, cs_b, template, cycles))


if __name__ == "__main__":
    main()
