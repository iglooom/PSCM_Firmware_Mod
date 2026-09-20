#!/usr/bin/env python3
"""Dump PSCM memory via the OEM SBL's RequestUpload (0x35). No custom SBL.

PROVEN ON HARDWARE 2026-09-13: after loading the OEM SBL BV6T-14C220-AA into
RAM, `35 00 44 <addr4> <len4>` returns `75 20 0082`, i.e. RequestUpload IS
implemented, maxNumberOfBlockLength = 0x0082 (130) => 128 payload bytes per
TransferData response.

ADDRESS MAP (UDS byte addresses; byte = 2 x DSP word)
    0x00000000..0x00080000   512 KB  Program Flash   P:$00000..$3FFFF
    0x00080000..0x00088000    32 KB  Boot Flash/PBL  P:$40000..$43FFF
    0x04008000..0x04010000    32 KB  Data Flash      X:$04000..$07FFF
The 0x04 prefix selects X: (data) space. Confirmed: the 14C217 blk2 VBF region
ends at 0x04010000 == X:$8000 == exactly the end of the 56F8367's 32 KB Data
Flash, and the 14C386 SIGCFG starts at X:$4000 == its start.

ACCEPTANCE GATE
    The module runs CV6T-14C217-AR + CV6T-14C218-AX and we hold both VBFs.
    Before any full dump, a bounded read is compared BYTE-EXACT against the
    OEM VBF content at the same address. --full refuses to run unless that
    gate has passed in the same invocation.

SAFETY
    Sends 10 02, 27, 34/36/37 (SBL into RAM), 31 01 0301 (start SBL), then
    35/36/37 for reading. Sends NO erase (31 01 FF00), NO flash write, NO 11
    reset. The SBL lives in volatile RAM; a power cycle restores the module.
"""
import argparse
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from pscm_seckey import key_from_seed, MAGIC_PSCM_L1            # noqa: E402
from load_and_probe_sbl import (open_isotp, req, fmt, parse_vbf,  # noqa: E402
                                NRC)

SBL_VBF = os.path.join(ROOT, "BV6T-14C220-AA.vbf")
APP_VBF = os.path.join(ROOT, "CV6T-14C217-AR.VBF")
CAL_VBF = os.path.join(ROOT, "CV6T-14C218-AX.VBF")

REGIONS = [
    ("pflash", 0x00000000, 0x00080000, "Program Flash 512KB  P:$00000-$3FFFF"),
    ("bootflash", 0x00080000, 0x00088000, "Boot Flash / PBL 32KB  P:$40000-$43FFF"),
    ("dflash", 0x04008000, 0x04010000, "Data Flash 32KB  X:$04000-$07FFF"),
]


def load_sbl(s, vbf, verbose=True):
    call, blks = parse_vbf(vbf)
    r = req(s, "1002", timeout=4.0)
    if not (r and r[0] == 0x50):
        raise SystemExit(f"programming session refused: {fmt(r)}")
    time.sleep(0.1)
    r = req(s, "2701", timeout=4.0)
    if not (r and r[0] == 0x67 and len(r) >= 5):
        raise SystemExit(f"no seed: {fmt(r)}")
    key = key_from_seed(list(r[2:5]), MAGIC_PSCM_L1)
    r = req(s, "2702" + key.hex(), timeout=4.0)
    if not (r and r[0] == 0x67):
        raise SystemExit(f"unlock refused: {fmt(r)}")
    if verbose:
        print("  unlocked")
    for addr, length, data, _ in blks:
        r = req(s, "340044" + struct.pack(">I", addr).hex()
                + struct.pack(">I", length).hex(), timeout=5.0)
        if not (r and r[0] == 0x74):
            raise SystemExit(f"RequestDownload refused: {fmt(r)}")
        chunk = ((r[2] << 8 | r[3]) if r[1] == 0x20 else r[2]) - 2
        bc, off = 1, 0
        while off < length:
            piece = data[off:off + chunk]
            rr = req(s, (bytes([0x36, bc & 0xFF]) + piece).hex(), timeout=6.0)
            if not (rr and rr[0] == 0x76):
                raise SystemExit(f"TransferData bc={bc}: {fmt(rr)}")
            off += len(piece)
            bc = (bc + 1) & 0xFF
        r = req(s, "37", timeout=5.0)
        if not (r and r[0] == 0x77):
            raise SystemExit(f"TransferExit refused: {fmt(r)}")
    r = req(s, "3101" + "0301" + struct.pack(">I", call).hex(), timeout=5.0)
    if not (r and r[0] == 0x71):
        raise SystemExit(f"SBL start refused: {fmt(r)}")
    if verbose:
        print(f"  SBL running at 0x{call:08X}")
    return True


def upload(s, addr, length, progress=None, on_partial=None):
    """RequestUpload + TransferData loop. Returns bytes (exactly `length`).

    blockSequenceCounter follows ISO 14229-1: starts at 0x01 and wraps
    0xFF -> 0x00 -> 0x01... Skipping 0x00 earns NRC 73 at exactly block 255,
    which is how the first full-dump attempt failed at +0x7F80.

    On failure this ALWAYS attempts a `37` RequestTransferExit so the SBL does
    not stay stuck in "upload active" -- otherwise the next RequestUpload gets
    NRC 22 conditionsNotCorrect and the fault looks like a bad address.
    """
    r = req(s, "350044" + struct.pack(">I", addr).hex()
            + struct.pack(">I", length).hex(), timeout=5.0)
    if not (r and r[0] == 0x75):
        raise RuntimeError(f"RequestUpload @0x{addr:08X} len 0x{length:X}: {fmt(r)}")
    if r[1] == 0x20:
        maxblk = (r[2] << 8) | r[3]
    elif r[1] == 0x10:
        maxblk = r[2]
    else:
        maxblk = int.from_bytes(r[2:2 + (r[1] >> 4)], 'big')
    payload = maxblk - 2
    if payload <= 0:
        raise RuntimeError(f"nonsensical maxNumberOfBlockLength {maxblk}")

    out = bytearray()
    bc = 1
    t0 = time.monotonic()
    try:
        while len(out) < length:
            rr = req(s, f"36{bc & 0xFF:02X}", timeout=6.0)
            if not (rr and rr[0] == 0x76):
                raise RuntimeError(f"TransferData bc={bc:02X} at +0x{len(out):X}: "
                                   f"{fmt(rr)}")
            if rr[1] != (bc & 0xFF):
                raise RuntimeError(f"block counter mismatch: want {bc & 0xFF:02X} "
                                   f"got {rr[1]:02X}")
            out += rr[2:]
            if len(rr) <= 2:                  # empty payload => no progress
                raise RuntimeError(f"ECU returned 0 bytes at +0x{len(out):X} "
                                   f"(bc={bc:02X}); refusing to spin")
            bc = (bc + 1) & 0xFF          # ISO 14229-1: wraps 0xFF -> 0x00
            if progress and len(out) % (payload * 64) < payload:
                el = time.monotonic() - t0
                rate = len(out) / el if el else 0
                progress(len(out), length, rate)
    except Exception:
        if on_partial and out:
            on_partial(bytes(out))
        try:                               # always release the transfer
            req(s, "37", timeout=3.0)
        except OSError:
            pass
        raise
    r = req(s, "37", timeout=5.0)
    if not (r and r[0] == 0x77):
        raise RuntimeError(f"TransferExit after upload: {fmt(r)}")
    if len(out) > length:
        out = out[:length]                # final block may overrun
    if len(out) != length:
        raise RuntimeError(f"short read: got {len(out)} of {length}")
    return bytes(out)


def upload_segmented(s, addr, length, seg=0x4000, progress=None):
    """Read a region as independent RequestUpload segments.

    Bounds the blast radius of any single failure and makes a partial result
    usable: each segment is its own 35/36/37 transaction, so one bad segment
    does not abort the rest.
    """
    out = bytearray()
    t0 = time.monotonic()
    while len(out) < length:
        n = min(seg, length - len(out))
        chunk = upload(s, addr + len(out), n)
        out += chunk
        if progress:
            el = time.monotonic() - t0
            progress(len(out), length, len(out) / el if el else 0)
    return bytes(out)


def vbf_reference(addr, length):
    """Return OEM bytes at a UDS byte address, or None if not covered."""
    for path in (APP_VBF, CAL_VBF):
        if not os.path.exists(path):
            continue
        _, blks = parse_vbf(path)
        for a, l, data, _ in blks:
            if a <= addr and addr + length <= a + l:
                return data[addr - a: addr - a + length]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--gate-addr", type=lambda v: int(v, 0), default=0x0001C000,
                    help="address for the byte-exact acceptance gate")
    ap.add_argument("--gate-len", type=lambda v: int(v, 0), default=0x200)
    ap.add_argument("--full", action="store_true",
                    help="dump all regions (requires the gate to pass first)")
    ap.add_argument("--segment", type=lambda v: int(v, 0), default=0x4000,
                    help="bytes per RequestUpload transaction (default 16 KiB)")
    ap.add_argument("--outdir", default=os.path.join(ROOT, "dumps"))
    ap.add_argument("--one", metavar="ADDR:LEN",
                    help="dump a single block, e.g. --one 0x1C000:0x10000 . "
                         "Runs the gate first, then this block only.")
    a = ap.parse_args()

    print("=" * 74)
    print("PSCM MEMORY DUMP via OEM SBL RequestUpload (0x35)")
    print("=" * 74)
    for n, lo, hi, d in REGIONS:
        print(f"  {n:<10} 0x{lo:08X}..0x{hi:08X}  {hi-lo:7d} B  {d}")
    print(f"\n  acceptance gate: 0x{a.gate_addr:08X} +0x{a.gate_len:X} "
          f"vs OEM VBF, byte-exact")
    ref = vbf_reference(a.gate_addr, a.gate_len)
    print(f"  reference available: {'YES' if ref else 'NO -- pick another gate addr'}")
    if ref:
        print(f"  expected sha256: {hashlib.sha256(ref).hexdigest()[:32]}")
    if not a.execute:
        print("\n  DRY RUN -- nothing sent. Re-run with --execute.")
        return
    if ref is None:
        raise SystemExit("refusing to run: gate address is not covered by a held VBF")

    os.makedirs(a.outdir, exist_ok=True)
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

        print("\n--- ACCEPTANCE GATE ------------------------------------------")
        got = upload(s, a.gate_addr, a.gate_len)
        ok = got == ref
        print(f"  read  sha256 {hashlib.sha256(got).hexdigest()[:32]}")
        print(f"  OEM   sha256 {hashlib.sha256(ref).hexdigest()[:32]}")
        print(f"  first 16 read: {got[:16].hex().upper()}")
        print(f"  first 16 OEM : {ref[:16].hex().upper()}")
        if not ok:
            diff = [i for i, (x, y) in enumerate(zip(got, ref)) if x != y]
            print(f"  *** MISMATCH at {len(diff)} of {len(ref)} bytes, "
                  f"first at +0x{diff[0]:X}" if diff else "  *** LENGTH MISMATCH")
            print("  GATE FAILED -- not dumping. Either the address mapping is")
            print("  wrong, or the upload transport is dropping/reordering data.")
            raise SystemExit(2)
        print("  *** GATE PASSED -- byte-exact against OEM VBF")

        if a.one:
            lo_s, len_s = a.one.split(":")
            lo, n = int(lo_s, 0), int(len_s, 0)
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            print(f"\n--- SINGLE BLOCK 0x{lo:08X} +0x{n:X} ({n} bytes) ---")

            def prog1(done, total, rate):
                pct = done / total * 100
                eta = (total - done) / rate if rate else 0
                print(f"\r    {done:7d}/{total} ({pct:5.1f}%) "
                      f"{rate/1024:6.1f} KiB/s eta {eta:5.0f}s", end="", flush=True)

            data = upload_segmented(s, lo, n, seg=a.segment, progress=prog1)
            print()
            path = os.path.join(a.outdir, f"PSCM_block_{lo:08X}_{n:X}_{stamp}.bin")
            with open(path, "xb") as fh:
                fh.write(data)
            sha = hashlib.sha256(data).hexdigest()
            print(f"    wrote {path}")
            print(f"    sha256 {sha}")
            print(f"    0xFF blank: {data.count(0xFF)/len(data)*100:.1f}%")
            # If the OEM VBF covers this block, verify byte-exact.
            oem = vbf_reference(lo, n)
            if oem is not None:
                same = oem == data
                print(f"    vs OEM VBF: {'BYTE-EXACT MATCH' if same else '*** MISMATCH ***'}")
                if not same:
                    d = [i for i, (x, y) in enumerate(zip(data, oem)) if x != y]
                    print(f"      {len(d)} differing bytes, first at +0x{d[0]:X}")
            else:
                print("    vs OEM VBF: not covered (no reference for this range)")
            return

        if not a.full:
            print("\n  Gate only. Re-run with --full to dump every region.")
            return

        print("\n--- FULL DUMP ------------------------------------------------")
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        meta = {"stamp": stamp, "f188": "CV6T-14C217-AR",
                "f124": "CV6T-14C218-AX", "regions": {}}
        for name, lo, hi, desc in REGIONS:
            n = hi - lo
            path = os.path.join(a.outdir, f"PSCM_{name}_{lo:08X}_{stamp}.bin")
            print(f"\n  {name}: 0x{lo:08X} +0x{n:X}  ({desc})")

            def prog(done, total, rate):
                pct = done / total * 100
                eta = (total - done) / rate if rate else 0
                print(f"\r    {done:7d}/{total} ({pct:5.1f}%) "
                      f"{rate/1024:6.1f} KiB/s eta {eta:5.0f}s", end="", flush=True)

            try:
                data = upload_segmented(s, lo, n, seg=a.segment, progress=prog)
            except RuntimeError as e:
                print(f"\n    FAILED: {e}")
                meta["regions"][name] = {"ok": False, "error": str(e)}
                # Re-sync: a failed region must not poison the next one.
                req(s, "3E00", timeout=1.5)
                continue
            print()
            with open(path, "xb") as fh:
                fh.write(data)
            sha = hashlib.sha256(data).hexdigest()
            blank = data.count(0xFF) / len(data) * 100
            print(f"    wrote {path}")
            print(f"    sha256 {sha}")
            print(f"    0xFF blank: {blank:.1f}%")
            meta["regions"][name] = {"ok": True, "addr": lo, "len": n,
                                     "path": path, "sha256": sha,
                                     "blank_pct": round(blank, 2)}
        mp = os.path.join(a.outdir, f"PSCM_dump_{stamp}.json")
        with open(mp, "x") as fh:
            json.dump(meta, fh, indent=2)
        print(f"\n  metadata: {mp}")
    finally:
        stop.set()
        time.sleep(0.1)
        print("\n  POWER CYCLE the module to clear the SBL from RAM.")


if __name__ == "__main__":
    main()
