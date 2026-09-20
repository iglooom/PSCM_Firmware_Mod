#!/usr/bin/env python3
"""PSCM preflight probe — READ-ONLY. Sends nothing that can modify the module.

Answers, on the real module, the questions that must be settled BEFORE any
custom SBL is built or loaded:

  Q1  Is the PSCM reachable at 0x730/0x738 and what is its identity?
  Q2  WHICH 14C217 version is flashed?  (we hold 3 candidates: BV6T-...-AF,
      CV6T-...-AH, CV6T-...-AR -- and the dump acceptance test is meaningless
      until we know which one to compare against)
  Q3  Is the published SecurityAccess constant 0x9B2533 actually correct?
      (unlock in the DEFAULT session is a read-only operation: it grants rights
      but changes no memory. If it fails we learn it now, not mid-dump.)
  Q4  Does the APPLICATION implement 0x23 / 0x35 ?  Ford's CANdela DB for a
      2025 Transit PSCM says no, but that is a different generation. A probe is
      the only way to know for THIS module. If 0x23 works, no custom SBL is
      needed at all.
  Q5  Which sessions does it accept, and does it stay awake?

SAFETY -- what this script will and will not do:
  * Sends ONLY: 3E (TesterPresent), 22 (ReadDataByIdentifier),
    10 (DiagnosticSessionControl), 27 (SecurityAccess), 23/35 read probes.
  * NEVER sends 34/36/37 (download), 31 (routine/erase), 2E (write), 11 (reset).
  * A 0x23/0x35 probe requests a TINY read at a known-safe address; a negative
    response is a perfectly good answer and is recorded as such.
  * --execute is required; default is a dry run that only prints the plan.

Usage:
    python3 pscm_preflight.py                 # dry run, prints plan
    python3 pscm_preflight.py --execute       # talk to the module
    python3 pscm_preflight.py --execute --iface can0
"""
import argparse
import os
import socket
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pscm_seckey import key_from_seed, MAGIC_PSCM_L1     # noqa: E402

ECU_TX, ECU_RX = 0x730, 0x738
CAN_ISOTP, SOL_CAN_ISOTP, CAN_ISOTP_OPTS = 6, 106, 1

NRC = {0x10: 'generalReject', 0x11: 'serviceNotSupported',
       0x12: 'subFunctionNotSupported', 0x13: 'incorrectMessageLength',
       0x22: 'conditionsNotCorrect', 0x24: 'requestSequenceError',
       0x31: 'requestOutOfRange', 0x33: 'securityAccessDenied',
       0x35: 'invalidKey', 0x36: 'exceedNumberOfAttempts',
       0x37: 'requiredTimeDelayNotExpired', 0x78: 'responsePending',
       0x7E: 'subFunctionNotSupportedInActiveSession',
       0x7F: 'serviceNotSupportedInActiveSession'}

DIDS = [("F111", "ECU hardware / assembly part"),
        ("F188", "APPLICATION software part  <- identifies the 14C217 version"),
        ("F124", "calibration part (14C218)"),
        ("F18C", "ECU serial number"),
        ("F190", "VIN"),
        ("F169", "configuration part"),
        ("F1A2", "?"),
        ("F195", "supplier sw version")]


def open_isotp(iface, txid, rxid, timeout=2.0):
    s = socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, CAN_ISOTP)
    flags = 0x004 | 0x008                     # TX+RX padding, Ford style
    s.setsockopt(SOL_CAN_ISOTP, CAN_ISOTP_OPTS,
                 struct.pack("=IIBBBB", flags, 0, 0, 0x00, 0x00, 0))
    s.bind((iface, rxid, txid))
    s.settimeout(timeout)
    return s


def req(s, hexstr, timeout=2.0):
    """Send and return the response, transparently waiting out 0x78."""
    s.send(bytes.fromhex(hexstr))
    deadline = time.monotonic() + 6.0
    while True:
        try:
            s.settimeout(timeout)
            r = s.recv(4096)
        except socket.timeout:
            return None
        if len(r) >= 3 and r[0] == 0x7F and r[2] == 0x78:
            if time.monotonic() > deadline:
                return r
            continue                          # responsePending -> keep waiting
        return r


def show(tag, r):
    if r is None:
        print(f"    {tag:<42} TIMEOUT (no response)")
        return None
    h = r.hex().upper()
    if r[0] == 0x7F and len(r) >= 3:
        print(f"    {tag:<42} NRC {r[2]:02X} {NRC.get(r[2], '?')}")
    else:
        print(f"    {tag:<42} {h[:60]}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()

    print("=" * 74)
    print("PSCM PREFLIGHT PROBE  (read-only)")
    print("=" * 74)
    print(f"  interface {a.iface}   tester 0x{ECU_TX:03X} -> ecu 0x{ECU_RX:03X}")
    print("  will send : 3E, 22 (x8 DIDs), 10 01/03, 27 01/02, 23 probe, 35 probe")
    print("  will NEVER send: 34 36 37 (download), 31 (routine/erase),")
    print("                   2E (write), 11 (reset), 14/85 (DTC)")
    if not a.execute:
        print("\n  DRY RUN -- nothing sent. Re-run with --execute.")
        return

    s = open_isotp(a.iface, ECU_TX, ECU_RX)
    results = {}

    print("\n--- Q1/Q2: identity (default session) --------------------------")
    for _ in range(2):
        req(s, "3E00", timeout=1.0); time.sleep(0.05)
    for did, desc in DIDS:
        r = show(f"22 {did}  {desc}", req(s, "22" + did))
        if r and r[0] == 0x62:
            txt = r[3:].decode('latin1').rstrip('\x00 ')
            if txt.isprintable() and txt.strip():
                print(f"        -> {txt!r}")
                results[did] = txt

    print("\n--- Q5: session support ----------------------------------------")
    for sub, name in (("01", "default"), ("03", "extendedDiagnostic"),
                      ("02", "programming")):
        show(f"10 {sub}  ({name})", req(s, "10" + sub, timeout=3.0))
        time.sleep(0.2)
        req(s, "3E00", timeout=1.0)

    print("\n--- Q4: does the APPLICATION expose a read service? -------------")
    print("    (extended session first; a negative answer here is fine)")
    req(s, "1003", timeout=3.0); time.sleep(0.3)
    # ReadMemoryByAddress: 4-byte addr, 1-byte len  -> aslen 0x14
    show("23 14 00000000 04  (read 4 bytes @0)", req(s, "2314" + "00000000" + "04"))
    show("23 24 00000000 0004", req(s, "2324" + "00000000" + "0004"))
    show("35 00 44 00000000 00000010 (RequestUpload)",
         req(s, "350044" + "00000000" + "00000010"))

    print("\n--- Q3: SecurityAccess constant check --------------------------")
    print("    unlocking grants rights only; it modifies no memory.")
    r = show("27 01  (request seed)", req(s, "2701", timeout=3.0))
    if r and r[0] == 0x67 and len(r) >= 5:
        seed = list(r[2:5])
        k = key_from_seed(seed, MAGIC_PSCM_L1)
        print(f"        seed {bytes(seed).hex().upper()} "
              f"-> key {k.hex().upper()}  (magic 0x{MAGIC_PSCM_L1:06X})")
        if seed == [0, 0, 0]:
            print("        seed is all zeros: already unlocked, or unlock not required")
        r2 = show("27 02  (send key)", req(s, "2702" + k.hex(), timeout=3.0))
        if r2 and r2[0] == 0x67:
            print("        *** UNLOCK SUCCEEDED -- the constant 0x9B2533 is CORRECT")
            results['unlock'] = True
        elif r2 and r2[0] == 0x7F and len(r2) > 2 and r2[2] == 0x35:
            print("        *** invalidKey -- constant is WRONG for this module.")
            print("        Capture a genuine seed/key pair and re-derive.")
            results['unlock'] = False

    print("\n" + "=" * 74)
    print("SUMMARY")
    print("=" * 74)
    app = results.get('F188')
    print(f"  application part (F188) : {app or 'NOT READ'}")
    if app:
        have = {"BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"}
        m = [h for h in have if app.strip().upper().startswith(h)]
        print(f"  matching local VBF      : {m[0] if m else '*** NONE -- we do NOT hold this version'}")
        if not m:
            print("  => the dump acceptance test has no reference. Obtain this VBF,")
            print("     or verify the dump another way before trusting it.")
    print(f"  unlock constant OK      : {results.get('unlock', 'not tested')}")
    print("\n  Nothing was modified.")


if __name__ == "__main__":
    main()
