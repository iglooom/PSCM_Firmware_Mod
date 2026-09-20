#!/usr/bin/env python3
"""Analyze a joint11 demand-chain log: did the ramp fix unblock LCA torque?

Splits ONE log into LKA (code 1) and LCA (code 5) episodes by per_state_code,
then answers three questions in order:

  1. Does the ramp X:$2D47 now grow during sustained LCA? (the fix's target)
  2. Does the demand X:$2D49 follow (2D54 * 2D47) >> 10? (validates the model)
  3. Is LCA torque now comparable to LKA's? (the goal)

    python3 work/lca_resume/analyze_joint11_demand.py --selftest
    python3 work/lca_resume/analyze_joint11_demand.py fd22_joint11.csv
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import tempfile
from pathlib import Path

CODE_LABELS = {0: "idle", 1: "LKA-sustained", 2: "LKA-transient",
               3: "idle", 4: "LCA-entry", 5: "LCA-sustained"}
LKA_CODE, LCA_CODE = 1, 5
GUARD_S = 0.20
MIN_EPISODE_S = 0.50

MEASURES = ("ramp", "control_law", "demand", "rate_limit",
            "integrator", "torque_accumulator")

# Reference points from the joint10 drive, for a like-for-like comparison.
JOINT10_LCA_PEAK_ACC = 54
JOINT10_LKA_PEAK_ACC = 2732


def load(path):
    rows, skipped = [], 0
    with open(path, newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("status") != "ok":
                skipped += 1
                continue
            try:
                row = {"t": int(raw["epoch_ns"]) / 1e9,
                       "code": int(raw["per_state_code"])}
                for name in MEASURES:
                    row[name] = int(raw[name + "_signed"])
                rows.append(row)
            except (KeyError, ValueError, TypeError):
                skipped += 1
    rows.sort(key=lambda r: r["t"])
    return rows, skipped


def episodes(rows):
    out, run = [], []
    for row in rows:
        if run and row["code"] != run[-1]["code"]:
            out.append(run)
            run = []
        run.append(row)
    if run:
        out.append(run)
    return out


def body(episode, guard=GUARD_S):
    t0, t1 = episode[0]["t"], episode[-1]["t"]
    return [r for r in episode if t0 + guard <= r["t"] <= t1 - guard]


def collect(eps, code, guard, min_s):
    chosen = [e for e in eps if e[0]["code"] == code
              and (e[-1]["t"] - e[0]["t"]) >= min_s]
    pooled = []
    for episode in chosen:
        pooled.extend(body(episode, guard))
    return chosen, pooled


def stat(values):
    if not values:
        return None
    return {"n": len(values), "min": min(values), "max": max(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "peak_abs": max(abs(v) for v in values),
            "constant": len(set(values)) == 1}


def report(path, guard=GUARD_S, min_s=MIN_EPISODE_S, out=sys.stdout):
    rows, skipped = load(path)
    if not rows:
        print(f"no decodable rows in {path}", file=out)
        return 1

    eps = episodes(rows)
    print(f"file      : {path}", file=out)
    print(f"samples   : {len(rows)} decoded, {skipped} skipped", file=out)
    print(f"duration  : {rows[-1]['t'] - rows[0]['t']:.1f} s", file=out)
    print(f"episodes  : {len(eps)}\n", file=out)

    print("code census:", file=out)
    for code in sorted({r["code"] for r in rows}):
        n = sum(1 for r in rows if r["code"] == code)
        print(f"   code {code} {CODE_LABELS.get(code, '?'):<14} {n:6d} "
              f"({100 * n / len(rows):5.1f} %)", file=out)

    lka_eps, lka = collect(eps, LKA_CODE, guard, min_s)
    lca_eps, lca = collect(eps, LCA_CODE, guard, min_s)
    print(f"\nsustained episodes (>= {min_s:g} s, {guard:g} s guard):", file=out)
    print(f"   LKA code 1: {len(lka_eps)} episode(s), {len(lka)} samples", file=out)
    print(f"   LCA code 5: {len(lca_eps)} episode(s), {len(lca)} samples", file=out)

    if not lka or not lca:
        print("\nVERDICT: INCONCLUSIVE — need sustained episodes of BOTH codes.", file=out)
        return 2

    print(f"\n{'measure':<22}{'LKA code 1':>26}{'LCA code 5':>26}", file=out)
    print("-" * 74, file=out)
    stats = {}
    for name in MEASURES:
        a, b = stat([r[name] for r in lka]), stat([r[name] for r in lca])
        stats[name] = (a, b)
        fmt = lambda s: (f"{s['min']:>10d} (constant)" if s["constant"]
                         else f"{s['median']:>10.0f} [{s['min']}..{s['max']}]")
        print(f"{name:<22}{fmt(a):>26}{fmt(b):>26}", file=out)

    ramp_l, ramp_c = stats["ramp"]
    ctrl_l, ctrl_c = stats["control_law"]
    dmd_l, dmd_c = stats["demand"]
    acc_l, acc_c = stats["torque_accumulator"]
    rate_l, rate_c = stats["rate_limit"]

    print("\n" + "=" * 74, file=out)
    print("VERDICT", file=out)
    print("=" * 74, file=out)

    # Q1 -- did the ramp unblock?
    print(f"\n1. RAMP (the joint11 target)", file=out)
    if ramp_c["peak_abs"] == 0:
        print("   LCA ramp is STILL ZERO. The fix did not take effect —", file=out)
        print("   check the flash landed, or the arm is not reached in code 5.", file=out)
        ramp_ok = False
    elif ramp_c["constant"]:
        print(f"   LCA ramp is constant at {ramp_c['min']} — it is not ramping.", file=out)
        ramp_ok = False
    else:
        print(f"   LCA ramp now VARIES: peak |{ramp_c['peak_abs']}|, "
              f"median {ramp_c['median']:.0f}", file=out)
        print(f"   (LKA peak |{ramp_l['peak_abs']}| for scale)", file=out)
        ramp_ok = True

    # Q2 -- does the demand model hold? Validates our reading of P:$2B054.
    print(f"\n2. DEMAND MODEL  demand == (control_law * ramp) >> 10", file=out)
    checked = agree = 0
    for r in lka + lca:
        predicted = (r["control_law"] * r["ramp"]) >> 10
        checked += 1
        # Allow +-1 for the exact rounding of the >>10 on negatives.
        if abs(predicted - r["demand"]) <= 1:
            agree += 1
    pct = 100 * agree / checked if checked else 0
    print(f"   {agree}/{checked} samples agree within +-1 ({pct:.1f} %)", file=out)
    if pct < 80:
        print("   -> MODEL REFUTED. The demand is not this product; re-read", file=out)
        print("      P:$2B054 before drawing conclusions from these columns.", file=out)
    else:
        print("   -> model holds; the columns mean what we think they mean.", file=out)

    # Q3 -- the goal.
    print(f"\n3. TORQUE vs LKA", file=out)
    print(f"   peak |accumulator|   LKA {acc_l['peak_abs']:>6}   LCA {acc_c['peak_abs']:>6}", file=out)
    ratio = acc_c["peak_abs"] / acc_l["peak_abs"] if acc_l["peak_abs"] else 0
    print(f"   LCA / LKA = {ratio:.2f}", file=out)
    print(f"   joint10 was LCA {JOINT10_LCA_PEAK_ACC} / LKA {JOINT10_LKA_PEAK_ACC} "
          f"= {JOINT10_LCA_PEAK_ACC / JOINT10_LKA_PEAK_ACC:.3f}", file=out)
    gain = acc_c["peak_abs"] / JOINT10_LCA_PEAK_ACC if JOINT10_LCA_PEAK_ACC else 0
    print(f"   vs joint10 LCA peak: {gain:.1f}x", file=out)

    if not ramp_ok:
        print("\n  -> FIX DID NOT ENGAGE. Investigate before further patching.", file=out)
        return 3
    if ratio >= 0.5:
        print("\n  -> LCA authority is now COMPARABLE to LKA. Judge by feel;", file=out)
        print("     if it over-steers, the ramp is the knob to reduce.", file=out)
    elif gain >= 3:
        print("\n  -> SUBSTANTIAL IMPROVEMENT but still below LKA. Likely", file=out)
        print("     perceptible. Next constraint is control_law X:$2D54.", file=out)
    else:
        print("\n  -> Ramp moved but torque did not follow. The remaining", file=out)
        print("     constraint is control_law X:$2D54 (LCA "
              f"{ctrl_c['peak_abs']} vs LKA {ctrl_l['peak_abs']}).", file=out)

    # Control: the clamp must NOT have changed. Joint11 touched no constant.
    if not (rate_l["constant"] and rate_c["constant"]
            and rate_l["min"] == rate_c["min"]):
        print(f"\n  NOTE: rate_limit differs (LKA {rate_l['median']:.0f} / "
              f"LCA {rate_c['median']:.0f}); expected identical.", file=out)
    return 0


def _write(path, rows):
    fields = ["epoch_ns", "status", "per_state_code"] + [m + "_signed" for m in MEASURES]
    with open(path, "w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _s(t, code, ramp, ctrl, integ, acc, rate=1024, demand=None):
    if demand is None:
        demand = (ctrl * ramp) >> 10
    return {"epoch_ns": int(t * 1e9), "status": "ok", "per_state_code": code,
            "ramp_signed": ramp, "control_law_signed": ctrl,
            "demand_signed": demand, "rate_limit_signed": rate,
            "integrator_signed": integ, "torque_accumulator_signed": acc}


def selftest():
    import io
    tmp = Path(tempfile.mkdtemp())

    # Success: ramp grows, torque follows to a comparable level.
    rows, t = [], 0.0
    for i in range(40):
        rows.append(_s(t, 1, 900 + i, 2000, 1500 + 10 * i, 1500 + 10 * i)); t += 0.1
    for i in range(40):
        rows.append(_s(t, 5, 100 + 20 * i, 1800, 700 + 20 * i, 700 + 20 * i)); t += 0.1
    p = tmp / "ok.csv"; _write(p, rows)
    buf = io.StringIO(); assert report(p, out=buf) == 0
    text = buf.getvalue()
    assert "ramp now VARIES" in text, text
    assert "model holds" in text, text

    # Fix did not engage: ramp still pinned at zero.
    rows2, t = [], 0.0
    for i in range(40):
        rows2.append(_s(t, 1, 900, 2000, 1500, 1500)); t += 0.1
    for i in range(40):
        rows2.append(_s(t, 5, 0, 1800, 10, 10)); t += 0.1
    p2 = tmp / "dead.csv"; _write(p2, rows2)
    buf2 = io.StringIO(); assert report(p2, out=buf2) == 3
    assert "STILL ZERO" in buf2.getvalue()

    # Model refuted: demand unrelated to the product.
    rows3, t = [], 0.0
    for i in range(40):
        rows3.append(_s(t, 1, 900 + i, 2000, 1500, 1500, demand=12345)); t += 0.1
    for i in range(40):
        rows3.append(_s(t, 5, 100 + i, 1800, 50, 50, demand=999)); t += 0.1
    p3 = tmp / "model.csv"; _write(p3, rows3)
    buf3 = io.StringIO(); report(p3, out=buf3)
    assert "MODEL REFUTED" in buf3.getvalue(), buf3.getvalue()

    # One-sided drive is inconclusive.
    rows4 = [_s(i * 0.1, 1, 900, 2000, 1500, 1500) for i in range(40)]
    p4 = tmp / "one.csv"; _write(p4, rows4)
    buf4 = io.StringIO(); assert report(p4, out=buf4) == 2

    # Ramp moves but torque stays small -> blame control_law.
    rows5, t = [], 0.0
    for i in range(40):
        rows5.append(_s(t, 1, 900 + i, 4000, 2500, 2500)); t += 0.1
    for i in range(40):
        rows5.append(_s(t, 5, 100 + i, 30, 60, 60)); t += 0.1
    p5 = tmp / "ctrl.csv"; _write(p5, rows5)
    buf5 = io.StringIO(); assert report(p5, out=buf5) == 0
    assert "control_law" in buf5.getvalue()

    print("SELFTEST PASS: 5 scenarios "
          "(success, fix-dead, model-refuted, one-sided, control-law-limited)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--guard", type=float, default=GUARD_S)
    ap.add_argument("--min-episode", type=float, default=MIN_EPISODE_S)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.csv:
        ap.error("give a CSV, or --selftest")
    return report(Path(args.csv), args.guard, args.min_episode)


if __name__ == "__main__":
    raise SystemExit(main())
