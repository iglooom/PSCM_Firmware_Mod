#!/usr/bin/env python3
"""Separate and compare LKA vs LCA episodes from ONE joint9 log.

The drive can be recorded as a single CSV: every row carries `per_state_code`,
so the mode is recoverable from the data itself and the `label` column is
ignored entirely. This splits the file into episodes, drops transition
samples, and compares the limiter words between sustained LKA (code 1) and
sustained LCA (code 5).

    python3 work/lca_resume/analyze_joint9_limiter.py --selftest
    python3 work/lca_resume/analyze_joint9_limiter.py fd22_joint9.csv
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import tempfile
from pathlib import Path

# Live code mapping established by joint3..joint8.
CODE_LABELS = {0: "idle", 1: "LKA-sustained", 2: "LKA-transient",
               3: "idle", 4: "LCA-entry", 5: "LCA-sustained"}

LKA_CODE = 1
LCA_CODE = 5

# A mode change takes effect over a few samples; discard this much wall-clock
# at each end of an episode so transition blending cannot pollute a mean.
GUARD_S = 0.20
# Below this an "episode" is a transition artefact, not a sustained state.
MIN_EPISODE_S = 0.50

MEASURES = ("rate_limit_source", "rate_limit", "authority_increment",
            "schedule_input", "integrator", "torque_accumulator")


class Episode:
    __slots__ = ("code", "rows", "t0", "t1")

    def __init__(self, code, rows):
        self.code = code
        self.rows = rows
        self.t0 = rows[0]["t"]
        self.t1 = rows[-1]["t"]

    @property
    def duration(self):
        return self.t1 - self.t0

    def body(self, guard=GUARD_S):
        """Rows at least `guard` seconds inside both ends."""
        return [r for r in self.rows if self.t0 + guard <= r["t"] <= self.t1 - guard]


def load(path):
    """Read the CSV into typed rows, keeping only decoded samples."""
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
                    # Signed reading is the meaningful one for the accumulator;
                    # the limiter words are positive so both agree there.
                    row[name] = int(raw[name + "_signed"])
                rows.append(row)
            except (KeyError, ValueError, TypeError):
                skipped += 1
    rows.sort(key=lambda r: r["t"])
    return rows, skipped


def episodes(rows):
    """Group consecutive samples sharing one code."""
    out = []
    run = []
    for row in rows:
        if run and row["code"] != run[-1]["code"]:
            out.append(Episode(run[0]["code"], run))
            run = []
        run.append(row)
    if run:
        out.append(Episode(run[0]["code"], run))
    return out


def summarize(values):
    if not values:
        return None
    uniq = sorted(set(values))
    return {
        "n": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "distinct": len(uniq),
        "constant": len(uniq) == 1,
        "values": uniq[:8],
    }


def collect(eps, code, guard, min_s):
    """Pool the guarded bodies of every sustained episode with `code`."""
    chosen = [e for e in eps if e.code == code and e.duration >= min_s]
    pooled = []
    for episode in chosen:
        pooled.extend(episode.body(guard))
    return chosen, pooled


def report(path, guard=GUARD_S, min_s=MIN_EPISODE_S, out=sys.stdout):
    rows, skipped = load(path)
    if not rows:
        print(f"no decodable rows in {path}", file=out)
        return 1

    span = rows[-1]["t"] - rows[0]["t"]
    eps = episodes(rows)
    print(f"file      : {path}", file=out)
    print(f"samples   : {len(rows)} decoded, {skipped} skipped", file=out)
    print(f"duration  : {span:.1f} s", file=out)
    print(f"episodes  : {len(eps)}\n", file=out)

    print("code census (all samples):", file=out)
    for code in sorted({r["code"] for r in rows}):
        n = sum(1 for r in rows if r["code"] == code)
        label = CODE_LABELS.get(code, "?")
        print(f"   code {code} {label:<14} {n:6d} samples "
              f"({100 * n / len(rows):5.1f} %)", file=out)

    lka_eps, lka = collect(eps, LKA_CODE, guard, min_s)
    lca_eps, lca = collect(eps, LCA_CODE, guard, min_s)

    print(f"\nsustained episodes (>= {min_s:g} s, {guard:g} s guard each end):", file=out)
    for name, chosen, pooled in (("LKA code 1", lka_eps, lka),
                                 ("LCA code 5", lca_eps, lca)):
        durations = ", ".join(f"{e.duration:.2f}" for e in chosen[:8])
        print(f"   {name}: {len(chosen)} episode(s), {len(pooled)} usable samples"
              + (f"  [{durations}]" if durations else ""), file=out)

    # Both sides are required. A missing side is an inconclusive drive, not a
    # result -- say so rather than reporting half a comparison.
    if not lka or not lca:
        print("\nVERDICT: INCONCLUSIVE — need sustained episodes of BOTH codes.", file=out)
        if not lka:
            print("  no sustained code-1 (LKA) episode: the positive control is missing.", file=out)
        if not lca:
            print("  no sustained code-5 (LCA) episode: drive longer with centering on.", file=out)
        return 2

    print(f"\n{'measure':<22}{'LKA code 1':>26}{'LCA code 5':>26}", file=out)
    print("-" * 74, file=out)
    stats = {}
    for name in MEASURES:
        a = summarize([r[name] for r in lka])
        b = summarize([r[name] for r in lca])
        stats[name] = (a, b)

        def cell(s):
            if s["constant"]:
                return f"{s['min']:>10d} (constant)"
            return f"{s['median']:>10.0f} [{s['min']}..{s['max']}]"

        print(f"{name:<22}{cell(a):>26}{cell(b):>26}", file=out)

    src_lka, src_lca = stats["rate_limit_source"]
    rate_lka, rate_lca = stats["rate_limit"]
    auth_lka, auth_lca = stats["authority_increment"]
    sched_lka, _ = stats["schedule_input"]

    print("\n" + "=" * 74, file=out)
    print("VERDICT", file=out)
    print("=" * 74, file=out)

    # 1. Is the source cell actually constant, as the static analysis claims?
    if not (src_lka["constant"] and src_lca["constant"]
            and src_lka["min"] == src_lca["min"]):
        print("  rate_limit_source VARIES across the drive.", file=out)
        print("  -> The static 'X:$2D50 is a literal constant' claim is REFUTED.", file=out)
        print(f"     LKA {src_lka['values']}  LCA {src_lca['values']}", file=out)
        print("     Retract it and hunt the indirect writer.", file=out)
        return 3

    print(f"  rate_limit_source constant at {src_lka['min']} "
          f"(0x{src_lka['min']:04X}) in both modes — as predicted.", file=out)

    # 2. The decisive comparison. A SIGN FLIP is categorically different from
    # a smaller positive limit: a negative increment bleeds the integrator
    # toward zero instead of merely capping how fast it builds. Report it as
    # its own finding rather than as a misleading "0.2x" ratio.
    lka_builds = auth_lka["median"] > 0
    lca_bleeds = auth_lca["median"] < 0

    if lka_builds and lca_bleeds:
        print(f"  authority_increment SIGN FLIP: LKA {auth_lka['median']:+.0f}"
              f" (builds torque) vs LCA {auth_lca['median']:+.0f} (bleeds torque).", file=out)
        print("  -> This is not a smaller limit; it is a DECAY term. Sustained LCA", file=out)
        print("     cannot hold torque: the integrator is driven toward zero.", file=out)
        print("  -> HYPOTHESIS CONFIRMED, in a stronger form than predicted.", file=out)
    elif auth_lca["median"] < auth_lka["median"] or rate_lca["median"] < rate_lka["median"]:
        print("  LCA is granted LESS authority than LKA:", file=out)
        if rate_lca["median"] < rate_lka["median"]:
            print(f"     rate_limit          {rate_lka['median']:.0f} -> {rate_lca['median']:.0f}", file=out)
        if auth_lca["median"] < auth_lka["median"]:
            print(f"     authority_increment {auth_lka['median']:.0f} -> {auth_lca['median']:.0f}", file=out)
        print("  -> HYPOTHESIS CONFIRMED. The limiter is a real candidate cause.", file=out)
    else:
        print("  LCA is NOT limited below LKA on these words:", file=out)
        print(f"     rate_limit          LKA {rate_lka['median']:.0f}  LCA {rate_lca['median']:.0f}", file=out)
        print(f"     authority_increment LKA {auth_lka['median']:.0f}  LCA {auth_lca['median']:.0f}", file=out)
        print("  -> HYPOTHESIS REFUTED. Look downstream of X:$2D53.", file=out)

    # 2b. Report the rate limit separately: it was predicted to differ and,
    # on the measured drive, it does NOT. Say so explicitly.
    if rate_lka["constant"] and rate_lca["constant"] and rate_lka["min"] == rate_lca["min"]:
        print(f"\n  NOTE: rate_limit is IDENTICAL ({rate_lka['min']}) in both modes.", file=out)
        print("  The dispatcher-1 scheduled-vs-flat difference is numerically", file=out)
        print("  NEUTRAL here; only the dispatcher-2 increment differentiates.", file=out)

    # 3. Control: the code-1 arm must actually be scheduling.
    if sched_lka["constant"] and sched_lka["min"] == 0:
        print("\n  CONTROL FAILED: schedule_input (X:$2DA0) is 0 throughout LKA.", file=out)
        print("  The code-1 arm is not scheduling; re-examine the analysis §2.", file=out)
    return 0


def _write_csv(path, rows):
    fields = ["epoch_ns", "status", "per_state_code"]
    for name in MEASURES:
        fields.append(name + "_signed")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _sample(t, code, src=1024, rate=1024, auth=20, sched=0, integ=0, acc=0):
    return {"epoch_ns": int(t * 1e9), "status": "ok", "per_state_code": code,
            "rate_limit_source_signed": src, "rate_limit_signed": rate,
            "authority_increment_signed": auth, "schedule_input_signed": sched,
            "integrator_signed": integ, "torque_accumulator_signed": acc}


def selftest():
    import io
    tmp = Path(tempfile.mkdtemp())

    # Interleaved single-file drive: idle, LKA, idle, LCA, idle, LKA, LCA.
    rows, t = [], 0.0
    def burst(code, seconds, **kw):
        nonlocal t
        for _ in range(int(seconds * 10)):
            rows.append(_sample(t, code, **kw))
            t += 0.1

    burst(0, 2)
    burst(1, 4, rate=4096, auth=80, sched=300, acc=120)
    burst(0, 1)
    burst(5, 5, rate=1024, auth=20, sched=0, acc=30)
    burst(2, 0.2, rate=4096, auth=80)        # transient, must be excluded
    burst(1, 3, rate=4096, auth=80, sched=300, acc=110)
    burst(5, 6, rate=1024, auth=20, sched=0, acc=25)

    path = tmp / "single.csv"
    _write_csv(path, rows)
    buf = io.StringIO()
    code = report(path, out=buf)
    text = buf.getvalue()
    assert code == 0, text
    assert "HYPOTHESIS CONFIRMED" in text, text
    # Two sustained episodes recovered per mode from one interleaved file.
    assert "LKA code 1: 2 episode(s)" in text, text
    assert "LCA code 5: 2 episode(s)" in text, text
    assert "constant at 1024" in text, text

    # Refutation path: LCA limits equal to LKA.
    rows2, t = [], 0.0
    def burst2(code, seconds, **kw):
        nonlocal t
        for _ in range(int(seconds * 10)):
            rows2.append(_sample(t, code, **kw))
            t += 0.1
    burst2(1, 4, rate=1024, auth=20, sched=300)
    burst2(5, 4, rate=1024, auth=20, sched=0)
    p2 = tmp / "equal.csv"
    _write_csv(p2, rows2)
    buf2 = io.StringIO()
    assert report(p2, out=buf2) == 0
    assert "HYPOTHESIS REFUTED" in buf2.getvalue(), buf2.getvalue()

    # Varying source must refute the constant claim.
    rows3, t = [], 0.0
    for i in range(80):
        rows3.append(_sample(t, 1 if i < 40 else 5, src=1024 + (i % 7)))
        t += 0.1
    p3 = tmp / "vary.csv"
    _write_csv(p3, rows3)
    buf3 = io.StringIO()
    assert report(p3, out=buf3) == 3
    assert "REFUTED" in buf3.getvalue()

    # Missing one mode is inconclusive, never a half-comparison.
    rows4 = [_sample(i * 0.1, 1, rate=4096) for i in range(60)]
    p4 = tmp / "lka_only.csv"
    _write_csv(p4, rows4)
    buf4 = io.StringIO()
    assert report(p4, out=buf4) == 2
    assert "INCONCLUSIVE" in buf4.getvalue()

    # Short bursts alone must not qualify as sustained.
    rows5, t = [], 0.0
    for _ in range(3):
        for _ in range(3):
            rows5.append(_sample(t, 1)); t += 0.1
        for _ in range(3):
            rows5.append(_sample(t, 5)); t += 0.1
    p5 = tmp / "short.csv"
    _write_csv(p5, rows5)
    buf5 = io.StringIO()
    assert report(p5, out=buf5) == 2, buf5.getvalue()

    # Sign flip: LKA builds, LCA bleeds. Must be reported as a decay term,
    # not as a "smaller limit" with a nonsense negative ratio.
    rows6, t = [], 0.0
    def burst6(code, seconds, **kw):
        nonlocal t
        for _ in range(int(seconds * 10)):
            rows6.append(_sample(t, code, **kw))
            t += 0.1
    burst6(1, 4, auth=40, sched=256)
    burst6(5, 4, auth=-9, sched=256)
    p6 = tmp / "signflip.csv"
    _write_csv(p6, rows6)
    buf6 = io.StringIO()
    assert report(p6, out=buf6) == 0
    text6 = buf6.getvalue()
    assert "SIGN FLIP" in text6, text6
    assert "DECAY term" in text6, text6
    assert "stronger form than predicted" in text6, text6
    # Equal rate limits must be called out explicitly.
    assert "rate_limit is IDENTICAL" in text6, text6

    print("SELFTEST PASS: 6 scenarios "
          "(interleaved confirm, refute, varying source, one-sided, all-short, sign flip)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", nargs="?", help="single joint9 log")
    ap.add_argument("--guard", type=float, default=GUARD_S,
                    help="seconds discarded at each episode end")
    ap.add_argument("--min-episode", type=float, default=MIN_EPISODE_S,
                    help="minimum episode length to count as sustained")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.csv:
        ap.error("give a CSV, or --selftest")
    return report(Path(args.csv), args.guard, args.min_episode)


if __name__ == "__main__":
    raise SystemExit(main())
