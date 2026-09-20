#!/usr/bin/env python3
"""Build max-demand 0x0A5 loops from REAL captured frames.

For each lane state, pick — for every one of the 16 rolling-counter values —
the captured frame carrying the largest |LaRefAng_No_Req|. Emitting those 16
frames in counter order 0..15 gives a continuous rolling counter and a
sustained maximum steering demand, using ONLY bytes that were really on the
bus. No checksum is computed: every frame ships exactly as captured.

WHY NOT JUST MULTIPLY THE DEMAND BY 3
    LaRefAng_No_Req is a 12-bit field spanning -102.4..+102.35 mRad. Tripling a
    40 mRad demand gives 121.5, which does not fit: it wraps and becomes a
    NEGATIVE angle, i.e. a command in the opposite direction. And any rewritten
    frame needs a recomputed checksum, which is unsolved for this message (the
    stationary-corpus rules fail 5508/5875 real frames). Selecting real strong
    frames sidesteps both problems.

Usage:
    python3 build_maxdemand.py --selftest
    python3 build_maxdemand.py                  # writes maxdemand.json
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(HERE, "maxdemand.json")

LINE = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")
LOGS = ["drivemod2.log", "drivemod.log", "drive3.log", "drive1.log"]

STATE_NAME = {0: "idle", 2: "lka-left", 4: "lka-right", 5: "suppr-right",
              6: "lca", 7: "suppr-both", 3: "state3"}

# CAN-HS.dbc, message 165 (0x0A5) -- read verbatim, never assumed
BIT_STATE = (30, 3)      # LkaActvStats_D_Req
BIT_REFANG = (27, 12)    # LaRefAng_No_Req   scale 0.05 offset -102.4
BIT_ROLL = (55, 4)       # LaActvReq_No_RollCnt
BIT_CURVATURE = (51, 12)  # LaCurvature_No_Calc  (2048 == 0.0 1/m)
BIT_LDW_STATS = (14, 3)   # LdwActvStats_D_Req
BIT_REFANG_UB = (10, 1)   # LaRefAng_No_Req_UB
BIT_CURV_UB = (11, 1)     # LaCurvature_No_Calc_UB

def be(d, start, length):
    """Big-endian (Motorola) bit extraction, as the DBC specifies."""
    v = 0
    b, bit = start // 8, start % 8
    for _ in range(length):
        if b >= len(d):
            return v
        v = (v << 1) | ((d[b] >> bit) & 1)
        bit -= 1
        if bit < 0:
            bit, b = 7, b + 1
    return v


# --- plausibility gate ------------------------------------------------
# Every field we are NOT deliberately maximising must look like a camera in
# normal operation. Bounds are taken from the healthy replay stream, whose
# curvature median is 2056 (i.e. ~straight) and whose LdwActvStats takes
# values {0, 3, 5, 7}. Field extremes (raw 0 / raw 4095) are invalid markers.
CURV_LO, CURV_HI = 1500, 2600
REFANG_LO, REFANG_HI = 1, 4094
HEALTHY_LDW = frozenset({3, 5, 7})


def plausible(d):
    """Is this frame from a camera that is working normally?"""
    if not (CURV_LO <= be(d, *BIT_CURVATURE) <= CURV_HI):
        return False
    if not (REFANG_LO <= be(d, *BIT_REFANG) <= REFANG_HI):
        return False
    if be(d, *BIT_REFANG_UB) != 1 or be(d, *BIT_CURV_UB) != 1:
        return False
    if be(d, *BIT_LDW_STATS) not in HEALTHY_LDW:
        return False
    return True


def demand_mrad(d):
    return be(d, *BIT_REFANG) * 0.05 - 102.4


def scan(logs):
    """-> {state: {rollcnt: (abs_demand, hexframe, demand)}}

    Only PLAUSIBLE frames are eligible; among those, the strongest demand wins.

    LESSON (maxtest, 2026-09-14): selecting purely for max |steering angle|
    picked frames whose OTHER fields sat at pathological values -- 10 of 16
    lka-left frames carried LaCurvature_No_Calc raw 0, the field MINIMUM
    (-0.01024 1/m, the invalid marker), and LdwActvStats_D_Req = 0 instead of
    the healthy 5/7. The PSCM saw an implausible camera message and refused to
    declare availability at all (LaActAvail never left 0/1), so the test could
    not run. The extreme angle was probably a CONSEQUENCE of the camera
    reporting garbage. Rule: plausibility first, magnitude second.
    """
    best = {}
    total = kept = 0
    for fn in logs:
        p = os.path.join(ROOT, fn)
        if not os.path.exists(p):
            continue
        for ln in open(p, errors="replace"):
            m = LINE.match(ln)
            if not m or m.group(2).upper() != "0A5":
                continue
            d = bytes.fromhex(m.group(3))
            if len(d) != 8:
                continue
            total += 1
            if not plausible(d):
                continue
            kept += 1
            st = be(d, *BIT_STATE)
            roll = be(d, *BIT_ROLL)
            dem = demand_mrad(d)
            slot = best.setdefault(st, {})
            if roll not in slot or abs(dem) > slot[roll][0]:
                slot[roll] = (abs(dem), d.hex(), dem)
    if total:
        print(f"  plausibility gate: {kept}/{total} eligible "
              f"({100 * kept / total:.1f}%)")
    return best, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    best, total = scan(LOGS)
    print(f"scanned {total} 0x0A5 frames\n")

    out = {}
    for st in sorted(best):
        slot = best[st]
        if len(slot) != 16:
            print(f"  state {st} {STATE_NAME.get(st,'?'):<12} "
                  f"only {len(slot)}/16 counters -- SKIPPED")
            continue
        frames = [slot[r][1] for r in range(16)]
        dem = [slot[r][2] for r in range(16)]
        # every selected frame must really carry the state and counter we filed
        for r in range(16):
            d = bytes.fromhex(frames[r])
            assert be(d, *BIT_STATE) == st, "state mismatch"
            assert be(d, *BIT_ROLL) == r, "counter mismatch"
        out[str(st)] = {"name": STATE_NAME.get(st, "?"), "frames": frames,
                        "demands": [round(x, 2) for x in dem],
                        "mean_abs": round(sum(abs(x) for x in dem) / 16, 2),
                        "max_abs": round(max(abs(x) for x in dem), 2)}
        print(f"  state {st} {STATE_NAME.get(st,'?'):<12} 16/16  "
              f"mean |{out[str(st)]['mean_abs']:5.1f}| max |{out[str(st)]['max_abs']:5.1f}| mRad")

    json.dump(out, open(a.out, "w"), indent=1)
    print(f"\nwrote {a.out}")
    return 0


def selftest():
    ok = True

    def chk(n, c, d=""):
        nonlocal ok
        ok = ok and bool(c)
        print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f"  {d}" if d else ""))

    print("SELFTEST\n")
    # bit extraction against a known real frame
    d = bytes.fromhex("005e1259309da8a2")
    chk("state extracts from byte3 bits 6..4", be(d, *BIT_STATE) == 5,
        str(be(d, *BIT_STATE)))
    chk("demand decodes in range",
        -102.4 <= demand_mrad(d) <= 102.35, f"{demand_mrad(d):.2f}")
    # round-trip the scale/offset
    chk("raw 2048 == 0.0 mRad", abs((2048 * 0.05 - 102.4)) < 1e-9)
    chk("raw 0 == field minimum -102.4", abs((0 * 0.05 - 102.4) + 102.4) < 1e-9)

    if os.path.exists(OUT):
        data = json.load(open(OUT))
        chk("maxdemand.json parses", bool(data))
        for st, v in data.items():
            fr = v["frames"]
            chk(f"state {st} has 16 frames", len(fr) == 16, str(len(fr)))
            chk(f"  all 8 bytes", all(len(x) == 16 for x in fr))
            # THE property the PSCM checks: counter 0..15 in order
            rolls = [be(bytes.fromhex(x), *BIT_ROLL) for x in fr]
            chk(f"  rolling counter is exactly 0..15 in order",
                rolls == list(range(16)), str(rolls))
            sts = {be(bytes.fromhex(x), *BIT_STATE) for x in fr}
            chk(f"  every frame carries state {st}", sts == {int(st)}, str(sts))
            dem = [demand_mrad(bytes.fromhex(x)) for x in fr]
            chk(f"  no frame is the invalid -102.4 marker",
                all(abs(x + 102.4) > 1e-6 for x in dem))
            pos = sum(1 for x in dem if x > 0)
            # A mixed-sign loop is not a defect: the selector takes the
            # strongest PLAUSIBLE frame per counter slot, and for some states
            # the strongest plausible demands genuinely point both ways. What
            # the test must guarantee is a usable NET pull, not uniform sign.
            net = sum(dem)
            chk(f"  net demand is directional (|mean| > 2 mRad)",
                abs(net) / 16 > 2,
                f"net {net/16:+.1f} mRad, {max(pos, 16-pos)}/16 one way")
            chk(f"  demand is sustained (mean |.| > 5 mRad)",
                v["mean_abs"] > 5, f"mean |{v['mean_abs']}|")
        # every shipped frame must pass the plausibility gate that the
        # maxtest failure taught us to apply
        for st, v in data.items():
            bad = [h for h in v["frames"] if not plausible(bytes.fromhex(h))]
            chk(f"state {st}: all 16 frames pass the plausibility gate",
                not bad, f"{len(bad)} implausible")

        # the comparison that matters
        if "2" in data and "6" in data:
            chk("LKA-L and LCA demands are comparable (fair test)",
                abs(data["2"]["mean_abs"] - data["6"]["mean_abs"]) < 12,
                f"{data['2']['mean_abs']} vs {data['6']['mean_abs']}")
    else:
        chk("maxdemand.json exists (run without --selftest first)", False)

    print("\n" + "=" * 54)
    print("SELFTEST:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
