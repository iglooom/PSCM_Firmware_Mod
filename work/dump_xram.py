#!/usr/bin/env python3
"""Dump X: data RAM via the SBL and search it for the EEPROM's content.

THE IDEA
--------
The probe showed byte 0x04002000 is readable while 0x04000000 is refused.
Byte = 0x04000000 + 2*word, so 0x04002000 = X:$1000 -- inside the 56F8367's
32 KB Data RAM (X:$0000..$3FFF). Data Flash is X:$4000..$7FFF, which is exactly
the 0x04008000..0x04010000 we already dumped. The mapping is consistent.

Why this may hand us the EEPROM for free: the APPLICATION runs first and, if it
shadows the external EEPROM into RAM at startup (the usual design -- you do not
re-read a slow serial part every loop), those bytes are still sitting in Data
RAM when we switch to the programming session. The SBL only touches a handful
of LOW X: addresses (X:$8, $53, $9C... found in its own code), so a shadow at a
higher address should survive.

If the VIN / part-number strings turn up in RAM, the EEPROM is readable TODAY
with no custom SBL and no SPI work.

Read-only: RequestUpload only. No writes anywhere.
"""
import argparse
import glob
import hashlib
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
PSCM = os.path.abspath(os.path.join(ROOT, ".."))
sys.path.insert(0, HERE)
from load_and_probe_sbl import open_isotp, req                 # noqa: E402
from pscm_dump import load_sbl, upload, upload_segmented, SBL_VBF  # noqa: E402

XBASE = 0x04000000          # byte address of X:$0000
XRAM_LO, XRAM_HI = 0x0000, 0x4000      # X: word addresses of Data RAM


def b(word):
    return XBASE + 2 * word


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--segment", type=lambda v: int(v, 0), default=0x1000)
    a = ap.parse_args()

    print("=" * 76)
    print("X: DATA RAM DUMP + EEPROM SIGNATURE SEARCH")
    print("=" * 76)
    print(f"  X:$0000..$3FFF  =  byte 0x{b(XRAM_LO):08X}..0x{b(XRAM_HI):08X}"
          f"  ({(XRAM_HI-XRAM_LO)*2} bytes)")
    print("  known: 0x04002000 READS, 0x04000000 REFUSED -> window starts between")
    if not a.execute:
        print("\n  DRY RUN -- nothing sent. Re-run with --execute.")
        return

    s = open_isotp(a.iface)
    stop = threading.Event()

    def ka():
        while not stop.wait(1.5):
            try:
                s.send(bytes.fromhex("3E80"))
            except OSError:
                return

    try:
        print("\n--- loading OEM SBL ------------------------------------------")
        req(s, "3E00", timeout=1.5)
        load_sbl(s, SBL_VBF)
        threading.Thread(target=ka, daemon=True).start()

        # ---- find the lowest readable X: address -------------------------
        print("\n--- locating the readable window ------------------------------")
        lo, hi = XRAM_LO, 0x1000          # 0x1000 known good, 0x0 known bad
        while lo < hi:
            mid = (lo + hi) // 2
            try:
                upload(s, b(mid), 0x10)
                hi = mid
            except RuntimeError:
                lo = mid + 1
            time.sleep(0.02)
        first = lo
        print(f"  lowest readable X: word = $%04X (byte 0x%08X)" % (first, b(first)))

        # ---- find the highest readable X: address ------------------------
        lo2, hi2 = first, XRAM_HI
        while lo2 < hi2:
            mid = (lo2 + hi2 + 1) // 2
            try:
                upload(s, b(mid) - 0x10, 0x10)
                lo2 = mid
            except RuntimeError:
                hi2 = mid - 1
            time.sleep(0.02)
        last = lo2
        print(f"  highest readable X: word = $%04X (byte 0x%08X)" % (last, b(last)))

        n = (last - first) * 2
        if n <= 0:
            raise SystemExit("no readable X: RAM window found")

        print(f"\n--- dumping X:${first:04X}..${last:04X} ({n} bytes) ----------")

        def prog(done, total, rate):
            print(f"\r    {done:6d}/{total} ({done/total*100:5.1f}%) "
                  f"{rate/1024:5.1f} KiB/s", end="", flush=True)

        data = upload_segmented(s, b(first), n, seg=a.segment, progress=prog)
        print()
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        outdir = os.path.join(ROOT, "dumps")
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, f"PSCM_xram_{b(first):08X}_{stamp}.bin")
        with open(path, "xb") as fh:
            fh.write(data)
        print(f"    wrote {path}")
        print(f"    sha256 {hashlib.sha256(data).hexdigest()}")

        # ---- the actual question -----------------------------------------
        print("\n" + "=" * 76)
        print("EEPROM SIGNATURE SEARCH IN X: RAM")
        print("=" * 76)
        ee = sorted(glob.glob(os.path.join(PSCM, "*EEPROM*.bin")))
        ref = open(ee[-1], 'rb').read() if ee else b""
        sigs = [b"WF0AXXWPMAEL32600", b"CV61-3C579-AL", b"CV6C-3D070-LF",
                b"3C579", b"3D070"]
        found = False
        for sig in sigs:
            p = data.find(sig)
            if p >= 0:
                found = True
                print(f"  *** {sig!r} FOUND at +0x{p:X} "
                      f"(X:${first + p//2:04X}, byte 0x{b(first)+p:08X})")
            else:
                print(f"      {sig!r} not present")

        # raw window match against the known EEPROM image
        if ref:
            wins = 0
            for off in range(0, len(ref) - 16, 16):
                seg = ref[off:off + 16]
                if len(set(seg)) > 4 and seg.count(0) < 8 and seg in data:
                    wins += 1
                    if wins <= 8:
                        print(f"  *** EEPROM +0x{off:03X} matches X: RAM "
                              f"at +0x{data.find(seg):X}")
            print(f"\n  raw 16-byte window matches: {wins}")
            found = found or wins > 0

        print()
        if found:
            print("  => The EEPROM (or part of it) IS shadowed in X: RAM.")
            print("     It can be read TODAY via the OEM SBL. Narrow the exact")
            print("     range and add it to pscm_dump.py REGIONS.")
        else:
            print("  => No EEPROM content in the readable X: RAM window.")
            print("     Either the app does not shadow it, or the shadow sits in")
            print("     the part of RAM the SBL refuses, or it was overwritten.")
            print("     Next: capture the CAN traffic of the tool that already")
            print("     produces EEPROM_DUMP_*.bin -- that reveals the service")
            print("     directly and costs nothing to try.")
    finally:
        stop.set()
        time.sleep(0.1)
        print("\n  POWER CYCLE the module to clear the SBL from RAM.")


if __name__ == "__main__":
    main()
