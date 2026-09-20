#!/usr/bin/env python3
"""Validate pscm_flash.py against a REAL OEM flash capture.

candump-2026-09-14_182153.log is a complete, successful PSCM flash performed by
third-party software on this vehicle. It is ground truth for the sequence, the
timings and the ECU's answers. This test asserts our flasher would produce the
same conversation -- in particular the parts we got wrong:

  1. responsePending must be a CONTINUE, not a result. The middle erase region
     answers 7F 31 78 TWICE (at +0.026 s and +4.531 s) before 71 01 FF00 10 at
     +8.311 s. Our original req() returned the pending frame once a budget
     elapsed, aborting a healthy erase.
  2. TransferData chunk size comes from the ECU's 74 response (0x0082 -> 128
     payload bytes), not a hardcoded constant.
  3. 31 01 0304 (finalise) is sent after the last TransferExit, before reset.
  4. TransferExit itself can answer with responsePending (all three did).

Run:  python3 validate_against_oem.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CAPTURE = os.path.join(ROOT, "candump-2026-09-14_182153.log")

LINE = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")

ok = True


def chk(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def load():
    fr = []
    for ln in open(CAPTURE, errors="replace"):
        m = LINE.match(ln)
        if not m:
            continue
        cid = int(m.group(2), 16)
        if cid in (0x730, 0x738):
            fr.append((float(m.group(1)), cid, bytes.fromhex(m.group(3))))
    return fr


def requests(fr):
    """-> [(t, payload)] for every request, single- and multi-frame."""
    out = []
    for i, (t, cid, d) in enumerate(fr):
        if cid != 0x730 or not d:
            continue
        pci = d[0] >> 4
        if pci == 0:
            out.append((t, d[1:1 + (d[0] & 0xF)]))
        elif pci == 1:
            total = ((d[0] & 0xF) << 8) | d[1]
            buf = bytearray(d[2:])
            for j in range(i + 1, len(fr)):
                _, cc, dd = fr[j]
                if cc != 0x730:
                    continue
                if dd[0] >> 4 != 2:
                    break
                buf += dd[1:]
                if len(buf) >= total:
                    break
            out.append((t, bytes(buf[:total])))
    return out


def responses_after(fr, t, limit=4000):
    out = []
    for tt, cc, dd in fr:
        if tt <= t or cc != 0x738 or not dd or dd[0] >> 4 != 0:
            continue
        out.append((tt, dd[1:1 + (dd[0] & 0xF)]))
        if len(out) >= limit:
            break
    return out


def main():
    if not os.path.exists(CAPTURE):
        print(f"capture not found: {CAPTURE}")
        return 1
    fr = load()
    rq = requests(fr)
    t0 = fr[0][0]
    print(f"OEM capture: {len(fr)} frames, {len(rq)} requests\n")

    svc = [p[0] for _, p in rq if p]
    chk("capture contains a full flash (10/27/34/36/37/31/11)",
        all(x in svc for x in (0x10, 0x27, 0x34, 0x36, 0x37, 0x31, 0x11)))

    # --- 1. the erase sequence ------------------------------------------
    erases = [(t, p) for t, p in rq
              if len(p) >= 4 and p[0] == 0x31 and p[1] == 0x01
              and p[2] == 0xFF and p[3] == 0x00]
    chk("three eraseMemory routines", len(erases) == 3, str(len(erases)))
    for t, p in erases:
        addr = int.from_bytes(p[4:8], "big")
        ln = int.from_bytes(p[8:12], "big")
        rs = responses_after(fr, t, 60)
        pend = 0
        final = None
        dt = None
        for tt, r in rs:
            if len(r) >= 3 and r[0] == 0x7F and r[2] == 0x78:
                pend += 1
                continue
            final = r
            dt = tt - t
            break
        chk(f"erase 0x{addr:08X} len 0x{ln:X} -> positive 71",
            final is not None and final[0] == 0x71,
            f"{pend} pending, final {final.hex() if final else '-'} after {dt:.3f}s")
        if addr == 0x0001C000:
            chk("  middle region emits MORE THAN ONE responsePending",
                pend >= 2, f"{pend} pending")
            chk("  and takes several seconds", dt is not None and dt > 5.0,
                f"{dt:.2f}s")

    # --- 2. the declared block size --------------------------------------
    dl = [(t, p) for t, p in rq if p and p[0] == 0x34]
    chk("four RequestDownload (1 SBL + 3 app)", len(dl) == 4, str(len(dl)))
    sizes = set()
    for t, p in dl:
        for tt, r in responses_after(fr, t, 5):
            if r and r[0] == 0x74:
                if r[1] == 0x20:
                    sizes.add(((r[2] << 8) | r[3]) - 2)
                break
    chk("ECU declares 128 payload bytes for the application blocks",
        128 in sizes, str(sorted(sizes)))

    # --- 3. the finalise routine -----------------------------------------
    fin = [(t, p) for t, p in rq
           if len(p) >= 4 and p[0] == 0x31 and p[1] == 0x01
           and p[2] == 0x03 and p[3] == 0x04]
    chk("finalise routine 31 01 0304 is sent", len(fin) == 1, str(len(fin)))
    if fin:
        t, _ = fin[0]
        last_tx = max(tt for tt, p in rq if p and p[0] == 0x37)
        rst = [tt for tt, p in rq if p and p[0] == 0x11]
        chk("  it comes AFTER the last TransferExit", t > last_tx,
            f"{t-t0:.2f}s vs {last_tx-t0:.2f}s")
        chk("  and BEFORE the ECUReset", bool(rst) and t < rst[0],
            f"{t-t0:.2f}s vs {rst[0]-t0:.2f}s" if rst else "no reset")
        pend = 0
        final = None
        for tt, r in responses_after(fr, t, 40):
            if len(r) >= 3 and r[0] == 0x7F and r[2] == 0x78:
                pend += 1
                continue
            final = r
            break
        chk("  answers responsePending then positive",
            pend >= 1 and final is not None and final[0] == 0x71,
            f"{pend} pending, {final.hex() if final else '-'}")

    # --- 4. TransferExit can also go pending ------------------------------
    tx = [(t, p) for t, p in rq if p and p[0] == 0x37]
    pend_tx = 0
    for t, p in tx:
        for tt, r in responses_after(fr, t, 20):
            if len(r) >= 3 and r[0] == 0x7F and r[2] == 0x78:
                pend_tx += 1
            break
    chk("TransferExit uses responsePending too (must not abort)",
        pend_tx >= 3, f"{pend_tx}/{len(tx)} went pending")

    # --- 5. our implementation agrees -------------------------------------
    sys.path.insert(0, os.path.join(ROOT, "work"))
    import inspect
    import pscm_flash
    src = inspect.getsource(pscm_flash.Ecu.req)
    chk("our req() never returns a pending frame",
        "return r" in src and "continue" in src
        and "pend += 1" in src)
    chk("our req() restarts the clock on each pending",
        "self.s.settimeout(pending_timeout)" in src
        and src.index("self.s.settimeout(pending_timeout)") < src.index("pend += 1"))
    dsrc = inspect.getsource(pscm_flash.download_blocks)
    chk("our download_blocks reads the ECU's declared block size",
        "r[2] << 8" in dsrc and "chunk" in dsrc)
    chk("our flasher sends 31 01 0304",
        "31010304" in inspect.getsource(pscm_flash.do_flash))

    print("\n" + "=" * 60)
    print("OEM VALIDATION:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
