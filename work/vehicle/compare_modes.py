#!/usr/bin/env python3
"""What ACTUALLY differs on the bus between LKA mode and LCA mode?

Decodes every field of 0x0A5 (IPMA -> PSCM) and 0x140 (PSCM readback) and
reports them per lane-state, from real driving captures.
"""
import re, sys, os, collections, statistics

ROOT = '/home/gl/Projects/ford/PSCM/Research'
L = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")

LOGS = [f for f in ('drive1.log', 'drive3.log', 'drivemod.log', 'drivemod2.log')
        if os.path.exists(os.path.join(ROOT, f))]


def be(d, start, length):
    """Motorola/big-endian bit extraction, DBC start-bit convention."""
    v = 0
    b, bit = start // 8, start % 8
    for _ in range(length):
        if b >= len(d):
            return None
        v = (v << 1) | ((d[b] >> bit) & 1)
        bit -= 1
        if bit < 0:
            bit = 7
            b += 1
    return v


STATE = {0: 'idle/armed', 1: 'LKAidle/LCAsuppr', 2: 'LKA-LEFT',
         4: 'LKA-RIGHT', 5: 'suppr-right', 6: 'LCA', 7: 'suppr-L+R'}

# ---- 0x0A5 fields ----------------------------------------------------
F_A5 = {
    'LkaActvStats_D_Req': (30, 3),
    'LaRefAng_No_Req':    (19, 12),
    'LaCurvature_No_Calc': (51, 12),
    'LaActvReq_No_RollCnt': (55, 4),
    'LdwActvStats_D_Req': (14, 3),
    'LaRefAng_No_Req_UB': (10, 1),
    'LaHandsOffDet':      (12, 1),
}
# ---- 0x140 fields (PSCM readback) ------------------------------------
# Start bits per CAN-HS.dbc BO_ 320 PSCM_h_FrP01 (Motorola, @0+).
# Corrected: LaActDeny was 57 and LaHandsOff was 56 here, both wrong.
# The DBC gives 60 and 61; the byte-7 mapping is confirmed independently by
# LaActAvail == (byte[7] >> 2) & 3 agreeing on 11,449/11,449 raw frames
# (PSCM_LCA_investigation.md). Any earlier run of this script decoded those
# two bits from the wrong positions.
F_140 = {
    'LaActAvail_D_Actl': (59, 2),
    'LaActDeny_B_Actl':  (60, 1),
    'LaHandsOff_B_Actl': (61, 1),
}


def main():
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    pscm = collections.defaultdict(lambda: collections.defaultdict(list))
    last_state = None
    n = 0
    for fn in LOGS:
        for ln in open(os.path.join(ROOT, fn), errors='replace'):
            m = L.match(ln)
            if not m:
                continue
            cid = int(m.group(2), 16)
            try:
                d = bytes.fromhex(m.group(3))
            except ValueError:
                continue
            if cid == 0x0A5 and len(d) >= 8:
                st = be(d, *F_A5['LkaActvStats_D_Req'])
                last_state = st
                n += 1
                for name, (s, l) in F_A5.items():
                    per[st][name].append(be(d, s, l))
            elif cid == 0x140 and len(d) >= 8 and last_state is not None:
                for name, (s, l) in F_140.items():
                    pscm[last_state][name].append(be(d, s, l))

    print(f"decoded {n} 0x0A5 frames from {', '.join(LOGS)}\n")

    def ang(raw):
        return raw * 0.05 - 102.4

    def curv(raw):
        return raw * 5e-6 - 0.01024

    print("=" * 78)
    print("WHAT THE IPMA SENDS  (0x0A5)")
    print("=" * 78)
    hdr = (f"{'state':<18}{'n':>7}{'demand mRad (mean/absmax)':>28}"
           f"{'curvature 1/m':>20}")
    print(hdr)
    for st in sorted(per):
        v = per[st]
        raw = [x for x in v['LaRefAng_No_Req'] if x is not None]
        dem = [ang(x) for x in raw]
        cv = [curv(x) for x in v['LaCurvature_No_Calc'] if x is not None]
        nz = sum(1 for x in dem if abs(x) > 0.01)
        print(f"{STATE.get(st, st):<18}{len(raw):>7}"
              f"{statistics.mean(dem):>14.2f}{max(abs(x) for x in dem):>14.2f}"
              f"{statistics.mean(cv):>20.6f}")
    print()
    print(f"{'state':<18}{'nonzero demand':>16}{'LdwActvStats':>26}"
          f"{'RefAng_UB':>14}")
    for st in sorted(per):
        v = per[st]
        dem = [ang(x) for x in v['LaRefAng_No_Req'] if x is not None]
        nz = 100.0 * sum(1 for x in dem if abs(x) > 0.01) / max(1, len(dem))
        ldw = collections.Counter(v['LdwActvStats_D_Req'])
        ub = collections.Counter(v['LaRefAng_No_Req_UB'])
        print(f"{STATE.get(st, st):<18}{nz:>15.1f}%"
              f"{str(dict(ldw.most_common(3))):>26}{str(dict(ub)):>14}")

    print()
    print("=" * 78)
    print("WHAT THE PSCM REPORTS BACK  (0x140), keyed by the state it was sent")
    print("=" * 78)
    print(f"{'state':<18}{'n':>7}{'LaActAvail distribution':>34}"
          f"{'Deny':>12}")
    for st in sorted(pscm):
        v = pscm[st]
        av = collections.Counter(v['LaActAvail_D_Actl'])
        dn = collections.Counter(v['LaActDeny_B_Actl'])
        tot = sum(av.values())
        print(f"{STATE.get(st, st):<18}{tot:>7}"
              f"{str(dict(sorted(av.items()))):>34}{str(dict(dn)):>12}")


if __name__ == '__main__':
    main()
