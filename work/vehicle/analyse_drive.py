#!/usr/bin/env python3
"""Analyse a la_monitor drive log: who gates LKA/LCA, and at what speed?

Consumes the .jsonl produced by la_monitor.py (every decoded sample) and
answers, from data rather than assumption:

  1. What is the LOWEST speed at which the PSCM declares lane assist
     available (LaActAvail_D_Actl in {2,3})?
  2. What is the LOWEST speed at which the IPMA actually commands an
     intervention (LkaActvStats_D_Req in {2,4} = LKA, 6 = LCA)?
  3. Therefore which module is the binding gate.
  4. What are the real engage/disengage speeds and hysteresis for each?

Usage:
    python3 analyse_drive.py drive1.jsonl
    python3 analyse_drive.py --selftest
"""
import argparse
import json
import sys
from collections import Counter, defaultdict

AVAIL = {3: "LKA/LCA+LDW available", 2: "LKA/LCA avail, LDW suppressed",
         1: "LKA/LCA suppressed, LDW available", 0: "all suppressed"}
LKA = {0: "Idle", 1: "Idle/LCA suppressed", 2: "LKA intervention LEFT",
       3: "LKA suppressed left", 4: "LKA intervention RIGHT",
       5: "LKA suppressed right", 6: "LCA in progress",
       7: "LKA/LCA suppressed L+R"}
LDW = {0: "Idle", 2: "LDW warning LEFT", 3: "LDW suppressed left",
       4: "LDW warning RIGHT", 5: "LDW suppressed right",
       7: "LDW suppressed L+R"}


def load(path):
    rows = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def speed_stats(rows, key, match, label):
    """Min/max speed at which `key` takes a value in `match`."""
    sp = [r["speed"] for r in rows
          if key in r and r[key] in match and r.get("speed") is not None]
    if not sp:
        print(f"  {label}: NEVER observed")
        return None
    print(f"  {label}: {len(sp)} samples, "
          f"speed {min(sp):.2f} .. {max(sp):.2f} kph")
    return min(sp)


def transitions(rows, key, active):
    """Return (engage_speeds, disengage_speeds) for key entering/leaving active.

    A signal's FIRST observed sample counts as an engage if it is already
    active -- otherwise an intervention that is under way when logging starts
    (or the first sample of a signal that only appears mid-log) is silently
    dropped, which is exactly the event we care about.
    """
    on, off = [], []
    prev = None
    for r in rows:
        if key not in r:
            continue
        v = r[key]
        sp = r.get("speed")
        if sp is not None:
            if prev is None:
                if v in active:
                    on.append(sp)
            else:
                was, now = prev in active, v in active
                if not was and now:
                    on.append(sp)
                elif was and not now:
                    off.append(sp)
        prev = v
    return on, off


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not a.path:
        ap.error("give a .jsonl from la_monitor.py")

    rows = load(a.path)
    sp = [r["speed"] for r in rows if r.get("speed") is not None]
    print(f"{len(rows)} samples, speed {min(sp):.1f} .. {max(sp):.1f} kph\n")

    print("=== PSCM: when does it ALLOW lane assist? ===")
    pscm_min = speed_stats(rows, "LaActAvail_D_Actl", (2, 3),
                           "LaActAvail in {2,3} (available)")
    c = Counter(r["LaActAvail_D_Actl"] for r in rows
                if "LaActAvail_D_Actl" in r)
    for k, n in c.most_common():
        print(f"      value {k} ({AVAIL.get(k,'?')}): {n} samples")

    print("\n=== IPMA: when does it COMMAND? ===")
    lka_min = speed_stats(rows, "LkaActvStats_D_Req", (2, 4),
                          "LKA intervention (2/4)")
    lca_min = speed_stats(rows, "LkaActvStats_D_Req", (6,),
                          "LCA in progress (6)")
    ldw_min = speed_stats(rows, "LdwActvStats_D_Req", (2, 4),
                          "LDW warning (2/4)")
    c = Counter(r["LkaActvStats_D_Req"] for r in rows
                if "LkaActvStats_D_Req" in r)
    for k, n in c.most_common():
        print(f"      LkaActvStats {k} ({LKA.get(k,'?')}): {n} samples")

    print("\n=== engage / disengage speeds ===")
    for label, key, active in (
            ("PSCM available", "LaActAvail_D_Actl", (2, 3)),
            ("LKA intervention", "LkaActvStats_D_Req", (2, 4)),
            ("LCA in progress", "LkaActvStats_D_Req", (6,)),
            ("LDW warning", "LdwActvStats_D_Req", (2, 4))):
        on, off = transitions(rows, key, active)
        if not on and not off:
            print(f"  {label:<18}: never toggled")
            continue
        s = f"  {label:<18}: {len(on)} engage"
        if on:
            s += f" (min {min(on):.2f}, max {max(on):.2f} kph)"
        s += f", {len(off)} disengage"
        if off:
            s += f" (min {min(off):.2f}, max {max(off):.2f} kph)"
        print(s)

    print("\n" + "=" * 70)
    print("VERDICT")
    if pscm_min is None:
        print("  PSCM never allowed lane assist -- it is the binding gate.")
    elif lka_min is None:
        print(f"  PSCM allowed from {pscm_min:.2f} kph, but the IPMA NEVER "
              f"commanded LKA.\n  => the IPMA is the binding gate.")
    else:
        print(f"  PSCM allows from      {pscm_min:.2f} kph")
        print(f"  IPMA commands LKA from {lka_min:.2f} kph")
        if lca_min:
            print(f"  IPMA shows LCA from    {lca_min:.2f} kph")
        if lka_min > pscm_min + 1.0:
            print(f"\n  => The PSCM is NOT the gate: it allows "
                  f"{lka_min - pscm_min:.1f} kph earlier than the camera acts.")
            print("     The speed threshold lives in the IPMA.")
        else:
            print("\n  => The PSCM and IPMA unlock at similar speeds; "
                  "cannot separate them from this run.")


def selftest():
    ok = True

    def chk(n, c, d=""):
        nonlocal ok
        ok = ok and bool(c)
        print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f"  {d}" if d else ""))

    rows = [
        {"speed": 30.0, "LaActAvail_D_Actl": 1},
        {"speed": 41.0, "LaActAvail_D_Actl": 3},
        {"speed": 50.0, "LaActAvail_D_Actl": 3},
        {"speed": 66.0, "LkaActvStats_D_Req": 2},
        {"speed": 70.0, "LkaActvStats_D_Req": 0},
    ]
    on, off = transitions(rows, "LaActAvail_D_Actl", (2, 3))
    chk("engage detected at 41", on == [41.0], str(on))
    on2, off2 = transitions(rows, "LkaActvStats_D_Req", (2, 4))
    chk("LKA engage at 66", on2 == [66.0], str(on2))
    chk("LKA disengage at 70", off2 == [70.0], str(off2))
    return ok


if __name__ == "__main__":
    main()
