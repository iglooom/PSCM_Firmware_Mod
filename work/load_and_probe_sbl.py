#!/usr/bin/env python3
"""Load the OEM PSCM SBL into RAM, then EMPIRICALLY probe its service set.

WHY THIS, BEFORE WRITING ANY CUSTOM CODE
----------------------------------------
Two things are settled at once:

  1. THE QUESTION. Static analysis could not determine whether the OEM SBL
     implements 0x23/0x35 -- its dispatcher is table-driven through a computed
     pointer, so no byte-pattern scan can enumerate it (see
     PSCM_backup_feasibility.md §3.4). Asking the running SBL is definitive and
     takes one request. If it CAN read, no custom SBL is needed at all.

  2. THE DELIVERY PATH. The custom SBL will be delivered by exactly this
     sequence (10 02 / 27 / 34 / 36 / 37 / 31 01 0301). Proving it works with
     OEM, known-good code -- code that cannot be malformed because Ford shipped
     it -- de-risks the custom build enormously. If the load fails here, the
     fault is in our transport, not in hand-assembled DSP56800E.

WHAT THIS WRITES
----------------
Only RAM. The SBL block loads at byte 0x0009F000 = word P:0x4F800, which the
56F8367 datasheet documents as on-chip Program RAM (P:$04F800..$04FFFF, 4 KB).
NO flash erase (31 01 FF00) and NO flash programming are performed -- those
requests are not in this script at all.

Recovery: the SBL lives in volatile RAM. A power cycle restores the module to
normal PBL + application operation. The application in flash is untouched.

PRECONDITIONS (see PSCM_preflight.md)
  * Vehicle stationary, ignition on, engine off. This is the power steering.
  * Stable supply / battery on charger.
  * can0 qdisc = pfifo_fast.

Usage:
    python3 load_and_probe_sbl.py                 # dry run, prints plan only
    python3 load_and_probe_sbl.py --execute
"""
import argparse
import os
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from pscm_seckey import key_from_seed, MAGIC_PSCM_L1          # noqa: E402

SBL_VBF = os.path.join(ROOT, "BV6T-14C220-AA.vbf")
ECU_TX, ECU_RX = 0x730, 0x738
CAN_ISOTP, SOL_CAN_ISOTP, CAN_ISOTP_OPTS = 6, 106, 1

NRC = {0x10: 'generalReject', 0x11: 'serviceNotSupported',
       0x12: 'subFunctionNotSupported', 0x13: 'incorrectMessageLength',
       0x21: 'busyRepeatRequest', 0x22: 'conditionsNotCorrect',
       0x24: 'requestSequenceError', 0x31: 'requestOutOfRange',
       0x33: 'securityAccessDenied', 0x35: 'invalidKey',
       0x36: 'exceedNumberOfAttempts', 0x37: 'requiredTimeDelayNotExpired',
       0x70: 'uploadDownloadNotAccepted', 0x71: 'transferDataSuspended',
       0x72: 'generalProgrammingFailure', 0x73: 'wrongBlockSequenceCounter',
       0x78: 'responsePending',
       0x7E: 'subFunctionNotSupportedInActiveSession',
       0x7F: 'serviceNotSupportedInActiveSession'}


def open_isotp(iface):
    s = socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, CAN_ISOTP)
    s.setsockopt(SOL_CAN_ISOTP, CAN_ISOTP_OPTS,
                 struct.pack("=IIBBBB", 0x004 | 0x008, 0, 0, 0, 0, 0))
    s.bind((iface, ECU_RX, ECU_TX))
    s.settimeout(2.0)
    return s


def req(s, hexstr, timeout=3.0, pend_budget=8.0):
    s.send(bytes.fromhex(hexstr))
    deadline = time.monotonic() + pend_budget
    while True:
        try:
            s.settimeout(timeout)
            r = s.recv(4096)
        except socket.timeout:
            return None
        if len(r) >= 3 and r[0] == 0x7F and r[2] == 0x78:
            if time.monotonic() > deadline:
                return r
            continue
        return r


def fmt(r):
    if r is None:
        return "TIMEOUT"
    if r[0] == 0x7F and len(r) >= 3:
        return f"NRC {r[2]:02X} {NRC.get(r[2], '?')}"
    return r.hex().upper()[:56]


def parse_vbf(path):
    """Parse via the validated container rules (probe data_start, no ws-skip)."""
    d = open(path, 'rb').read()
    i = d.find(b'header')
    depth, j = 0, d.find(b'{', i)
    while True:
        c = d[j:j + 1]
        if c == b'{':
            depth += 1
        elif c == b'}':
            depth -= 1
            if depth == 0:
                hend = j + 1
                break
        j += 1
    import re
    hdr = d[:hend].decode('latin1')
    m = re.search(r'call\s*=\s*0x([0-9A-Fa-f]+)', hdr)
    call = int(m.group(1), 16) if m else None   # only SBL parts carry `call`
    # Pitfall 1: do NOT skip whitespace. Probe offsets and require exact consume.
    for start in range(hend, min(hend + 8, len(d))):
        p, blks = start, []
        ok = True
        while p + 8 <= len(d):
            a, l = struct.unpack('>II', d[p:p + 8])
            if p + 8 + l + 2 > len(d):
                ok = False
                break
            blks.append((a, l, d[p + 8:p + 8 + l],
                         struct.unpack('>H', d[p + 8 + l:p + 8 + l + 2])[0]))
            p = p + 8 + l + 2
            if p == len(d):
                break
        if ok and p == len(d) and blks:
            return call, blks
    raise SystemExit("VBF block walk did not consume the file exactly")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--vbf", default=SBL_VBF)
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()

    call, blks = parse_vbf(a.vbf)
    total = sum(l for _, l, _, _ in blks)

    print("=" * 74)
    print("LOAD OEM SBL INTO RAM, THEN PROBE ITS SERVICES")
    print("=" * 74)
    print(f"  VBF   : {os.path.basename(a.vbf)}")
    print(f"  call  : 0x{call:08X}  (= word P:0x{call:05X}, on-chip Program RAM)")
    for i, (addr, l, _, crc) in enumerate(blks):
        print(f"  blk{i}  : load 0x{addr:08X}  len 0x{l:X}  crc16 0x{crc:04X}"
              f"   -> word P:0x{addr//2:05X}")
    print(f"  total : {total} bytes into RAM")
    print()
    print("  WILL SEND : 10 02, 27 01/02, 34/36/37 (into RAM only), 31 01 0301,")
    print("              then read-only probes 23/35/22/1A/21/33")
    print("  WILL NOT  : 31 01 FF00 (erase), any flash write, 11 (reset)")
    print("  RECOVERY  : power cycle -- SBL is in volatile RAM, flash untouched")
    if not a.execute:
        print("\n  DRY RUN -- nothing sent. Re-run with --execute.")
        return

    s = open_isotp(a.iface)
    stop = threading.Event()

    def keepalive():
        while not stop.wait(1.5):
            try:
                s.send(bytes.fromhex("3E80"))       # suppressed-response TP
            except OSError:
                return

    print("--- stage 1: unlock in programming session -------------------")
    print(f"  3E 00        {fmt(req(s, '3E00', timeout=1.5))}")
    r = req(s, "1002", timeout=4.0)
    print(f"  10 02        {fmt(r)}")
    if not (r and r[0] == 0x50):
        raise SystemExit("  programming session refused -- aborting, nothing loaded")
    time.sleep(0.1)
    r = req(s, "2701", timeout=4.0)
    print(f"  27 01        {fmt(r)}")
    if not (r and r[0] == 0x67 and len(r) >= 5):
        raise SystemExit("  no seed -- aborting")
    seed = list(r[2:5])
    key = key_from_seed(seed, MAGIC_PSCM_L1)
    print(f"     seed {bytes(seed).hex().upper()} -> key {key.hex().upper()}")
    r = req(s, "2702" + key.hex(), timeout=4.0)
    print(f"  27 02        {fmt(r)}")
    if not (r and r[0] == 0x67):
        raise SystemExit("  unlock refused -- aborting, nothing loaded")

    threading.Thread(target=keepalive, daemon=True).start()

    print("\n--- stage 2: transfer SBL into RAM ---------------------------")
    for bi, (addr, length, data, _) in enumerate(blks):
        rd = "34" + "00" + "44" + struct.pack(">I", addr).hex() \
             + struct.pack(">I", length).hex()
        r = req(s, rd, timeout=5.0)
        print(f"  34 blk{bi} @0x{addr:08X} len 0x{length:X}   {fmt(r)}")
        if not (r and r[0] == 0x74):
            stop.set()
            raise SystemExit("  RequestDownload refused -- aborting")
        chunk = (r[2] << 8 | r[3]) if r[1] == 0x20 else (r[2] if r[1] == 0x10 else 0x100)
        chunk -= 2
        bc, off = 1, 0
        while off < length:
            piece = data[off:off + chunk]
            rr = req(s, (bytes([0x36, bc & 0xFF]) + piece).hex(), timeout=6.0)
            if not (rr and rr[0] == 0x76):
                stop.set()
                raise SystemExit(f"  TransferData bc={bc} failed: {fmt(rr)}")
            off += len(piece)
            bc = (bc + 1) & 0xFF
        print(f"       transferred {off} bytes in {bc-1} chunks")
        r = req(s, "37", timeout=5.0)
        print(f"  37 blk{bi}                              {fmt(r)}")
        if not (r and r[0] == 0x77):
            stop.set()
            raise SystemExit("  TransferExit refused -- aborting")

    print("\n--- stage 3: start the SBL -----------------------------------")
    r = req(s, "3101" + "0301" + struct.pack(">I", call).hex(), timeout=5.0)
    print(f"  31 01 0301 {call:08X}   {fmt(r)}")
    started = bool(r and r[0] == 0x71)
    print(f"  SBL started: {started}")
    time.sleep(0.3)

    print("\n--- stage 4: PROBE THE RUNNING SBL (read-only) ---------------")
    print("  Is a memory-read service implemented in the SBL?\n")
    probes = [
        ("3E 00  TesterPresent (is SBL alive?)", "3E00"),
        ("22 F188 ReadDataByIdentifier", "22F188"),
        ("1A 88   ReadECUIdentification", "1A88"),
        ("21 01   ReadDataByLocalId", "2101"),
        ("23 14 00000000 04   ReadMemoryByAddress", "231400000000" + "04"),
        ("23 24 00000000 0004 ReadMemoryByAddress", "232400000000" + "0004"),
        ("23 44 00000000 00000004", "234400000000" + "00000004"),
        ("35 00 44 00000000 00000010  RequestUpload", "350044" + "00000000" + "00000010"),
        ("33      RequestRoutineResults", "3301"),
    ]
    verdict = {}
    for label, payload in probes:
        r = req(s, payload, timeout=3.0)
        print(f"    {label:<42} {fmt(r)}")
        verdict[label.split()[0] + label.split()[1]] = r

    stop.set()
    time.sleep(0.1)

    print("\n" + "=" * 74)
    print("VERDICT")
    print("=" * 74)
    reads = [(k, v) for k, v in verdict.items() if k.startswith(('23', '35'))]
    pos = [k for k, v in reads if v and v[0] in (0x63, 0x75)]
    if pos:
        print(f"  *** THE OEM SBL CAN READ: {', '.join(pos)}")
        print("  => NO custom SBL needed. Build the dumper on this service.")
    else:
        print("  No read service answered positively in the SBL.")
        print("  => Custom SBL confirmed necessary (as predicted from Ford's")
        print("     own CANdela service list).")
    print("\n  Delivery path (34/36/37 + 31 01 0301) exercised end-to-end:")
    print(f"    SBL transfer + start succeeded: {started}")
    print("    This is the same path the custom SBL will use.")
    print("\n  POWER CYCLE the module now to restore normal operation.")


if __name__ == "__main__":
    main()
