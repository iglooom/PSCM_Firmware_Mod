#!/usr/bin/env python3
"""Map which address ranges the OEM SBL will let us read.

Boot flash at byte 0x00080000 was refused with NRC 31 requestOutOfRange -- an
address-policy refusal, not a transport fault (the same session read 512 KB of
program flash and 32 KB of data flash byte-exact).

This probes small reads across candidate windows to find:
  * whether boot flash / the PBL is reachable at some other encoding,
  * the true bounds of what the SBL permits,
  * whether X: data RAM (live variables) is readable.

Every probe is a 16-byte RequestUpload. Read-only, no writes of any kind.
A refusal is a perfectly good result and is recorded as such.
"""
import argparse
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from load_and_probe_sbl import open_isotp, req, fmt           # noqa: E402
from pscm_dump import load_sbl, upload, SBL_VBF               # noqa: E402

# (label, byte address).  Rationale in the comments.
PROBES = [
    # --- controls: these are PROVEN readable, they validate the probe -------
    ("CONTROL pflash start", 0x00000000),
    ("CONTROL pflash end-16", 0x0007FFF0),
    ("CONTROL dflash start", 0x04008000),
    # --- boot flash / PBL, P:$40000..$43FFF, several encodings -------------
    ("bootflash byte=2*word", 0x00080000),
    ("bootflash +0x100", 0x00080100),
    ("bootflash mid", 0x00084000),
    ("bootflash last page", 0x00087F00),
    ("bootflash as word addr", 0x00040000),
    ("bootflash X:-prefixed", 0x04040000),
    ("bootflash P: prefix 0x02", 0x02080000),
    # --- the running SBL in program RAM ------------------------------------
    ("program RAM (SBL itself)", 0x0009F000),
    ("program RAM start", 0x00098000),
    # --- gaps and edges ----------------------------------------------------
    ("just past pflash", 0x00080000 - 0x10),
    ("reserved gap 0x90000", 0x00090000),
    ("past bootflash 0x88000", 0x00088000),
    # --- X: space ----------------------------------------------------------
    ("X: base 0x04000000", 0x04000000),
    ("X: data RAM 0x04000000+", 0x04002000),
    ("X: dflash end-16", 0x0400FFF0),
    ("X: past dflash", 0x04010000),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--len", type=lambda v: int(v, 0), default=0x10)
    a = ap.parse_args()

    print("=" * 76)
    print("PSCM readable-address map  (RequestUpload probes, read-only)")
    print("=" * 76)
    print(f"  {len(PROBES)} probes x {a.len} bytes. No writes. NRC = fine.")
    if not a.execute:
        for lbl, addr in PROBES:
            print(f"    0x{addr:08X}  {lbl}")
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

        print("\n--- probing --------------------------------------------------")
        ok, bad = [], []
        for lbl, addr in PROBES:
            try:
                d = upload(s, addr, a.len)
                print(f"  0x{addr:08X} {lbl:<28} OK   {d.hex().upper()[:32]}")
                ok.append((lbl, addr, d))
            except RuntimeError as e:
                msg = str(e).split(': ')[-1]
                print(f"  0x{addr:08X} {lbl:<28} --   {msg}")
                bad.append((lbl, addr, msg))
            time.sleep(0.05)

        print("\n" + "=" * 76)
        print("RESULT")
        print("=" * 76)
        ctrl = [x for x in ok if x[0].startswith("CONTROL")]
        print(f"  controls readable : {len(ctrl)}/3  "
              f"{'(probe is valid)' if len(ctrl) == 3 else '*** PROBE SUSPECT ***'}")
        if len(ctrl) < 3:
            print("  A control failed -- do not trust the negatives below.")
        print(f"  readable windows  : {len(ok)}")
        print(f"  refused           : {len(bad)}")

        # A readable address proves NOTHING until we show its CONTENT is not
        # something we already hold. 0x00040000 reads fine but is simply an
        # offset inside program flash -- the label said "bootflash", the bytes
        # said otherwise. Always compare against the existing dump.
        print("\n  --- novelty check: is any 'new' window actually new? ---")
        import glob as _glob
        pf = sorted(_glob.glob(os.path.join(ROOT, "dumps",
                                            "PSCM_pflash_00000000_*.bin")))
        df = sorted(_glob.glob(os.path.join(ROOT, "dumps",
                                            "PSCM_dflash_04008000_*.bin")))
        known = []
        if pf:
            known.append((0x00000000, open(pf[-1], 'rb').read()))
        if df:
            known.append((0x04008000, open(df[-1], 'rb').read()))
        novel = []
        for lbl, addr, data in ok:
            if lbl.startswith("CONTROL"):
                continue
            explained = None
            for base, blob in known:
                if base <= addr < base + len(blob):
                    off = addr - base
                    if blob[off:off + len(data)] == data:
                        explained = f"== our dump 0x{base:08X}+0x{off:X}"
                        break
                if data in blob:
                    explained = explained or "content already present in dump"
            if explained:
                print(f"    0x{addr:08X} {lbl:<26} NOT NEW ({explained})")
            else:
                print(f"    0x{addr:08X} {lbl:<26} *** GENUINELY NEW ***")
                novel.append((lbl, addr, data))

        boot = [x for x in novel if 'bootflash' in x[0]]
        if boot:
            print(f"\n  *** BOOT FLASH IS READABLE at: "
                  + ', '.join(f"0x{a2:08X}" for _, a2, _ in boot))
            print("  => extend pscm_dump.py REGIONS with the working encoding.")
        else:
            print("\n  Boot flash not reachable at any probed encoding.")
            print("  (0x00040000 reads, but it is program flash we already have.)")
            print("  The SBL's address policy excludes the PBL. Expected: the")
            print("  PBL launched the SBL, and Ford has no reason to expose it.")
            print("  The PBL is NOT part of a normal backup -- no VBF we hold")
            print("  erases or writes it, so it cannot be lost by flashing.")
        if novel:
            print(f"\n  {len(novel)} genuinely new window(s) worth dumping:")
            for lbl, addr, _ in novel:
                print(f"    0x{addr:08X}  {lbl}")
    finally:
        stop.set()
        time.sleep(0.1)
        print("\n  POWER CYCLE the module to clear the SBL from RAM.")


if __name__ == "__main__":
    main()
