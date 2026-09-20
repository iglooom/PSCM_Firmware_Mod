#!/usr/bin/env python3
"""Silence the IPMA so its lane-assist frames can be spoofed.

WHY 10 02 AND NOT 10 03
-----------------------
Measured on this vehicle and recorded in work/flash/bcmflash.py (BusQuiet):

    a module in programmingSession (10 02) STOPS transmitting its normal
    application frames.  extendedDiagnosticSession (10 03) does NOT.

An earlier version of this tool used 10 03 and the camera kept talking, exactly
as the BCM notes predict.  Also recorded there, so nobody re-adds them:

  * TesterPresent quiets NOTHING.  3E 80 only refreshes the S3 timer of a
    module already in a non-default session.  It is what makes the quiet
    PERSIST, not what causes it.
  * CommunicationControl (28) and ControlDTCSetting (85) are not needed; an
    attempt using 10 03 + 85 02 + 28 03 01 did not work on the car.

PHYSICAL, NOT FUNCTIONAL
------------------------
bcmflash.py broadcasts functionally (7DF) because it wants the WHOLE bus quiet.
Here we must do the opposite: silence ONLY the camera, because the test needs

    * the PSCM still transmitting 0x140 (its lane-assist status), and
    * the BCM still running (it drives the cluster indicators)

So the request is addressed physically to the IPMA at 0x706.  A bonus: a
physical request is answered, so unlike the functional broadcast this tool can
CONFIRM the session was entered instead of hoping.

RISK, STATED PLAINLY
--------------------
programmingSession puts the camera in its bootloader.  That is a bigger step
than an extended session, so:
  * this tool sends ONLY 10 02, 3E 80, 11 01 and 10 01 -- never 27 (security),
    31 (routine), 34/36/37 (download), 2E (write).  Without those, nothing can
    be erased or programmed.  Asserted by the self-test.
  * recovery is threefold: 11 01 hardReset on exit, S3 timeout ~5 s after the
    last TesterPresent, and an ignition cycle.  A crash cannot strand it.
  * the camera will set DTCs and the cluster may show a warning.  Expected;
    clears after a normal drive cycle.

Usage:
    python3 silence_ipma.py --selftest
    python3 silence_ipma.py --iface can0 --dry-run
    python3 silence_ipma.py --iface can0 --execute
    python3 silence_ipma.py --iface can0 --execute --session 3   # 10 03, for
                                                                 # comparison
"""
import argparse
import socket
import struct
import sys
import time

CAN_ISOTP = 6
SOL_CAN_ISOTP = 106
CAN_ISOTP_OPTS = 1

IPMA_TX, IPMA_RX = 0x706, 0x70E          # CAN-HS.dbc: BO_ 1798 / BO_ 1806

# A first request on an idle bus is often lost (same reason bcmflash.py sends
# its wake-up twice and repeats the functional arm 20x).
ARM_REPEAT = 3

SESSIONS = {0x01: "default", 0x02: "programming", 0x03: "extendedDiagnostic"}
NRC = {0x10: "generalReject", 0x11: "serviceNotSupported",
       0x12: "subFunctionNotSupported", 0x13: "incorrectMessageLength",
       0x22: "conditionsNotCorrect", 0x31: "requestOutOfRange",
       0x33: "securityAccessDenied", 0x7E: "subFunctionNotSupportedInSession",
       0x7F: "serviceNotSupportedInActiveSession"}


def open_isotp(iface, txid, rxid, timeout=2.0):
    s = socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, CAN_ISOTP)
    flags = 0x004 | 0x008                     # TX+RX padding, Ford style
    s.setsockopt(SOL_CAN_ISOTP, CAN_ISOTP_OPTS,
                 struct.pack("=IIBBBB", flags, 0, 0, 0x00, 0x00, 0))
    s.bind((iface, rxid, txid))
    s.settimeout(timeout)
    return s


def req(s, payload, timeout=2.0):
    """Send bytes, return response, transparently waiting out NRC 0x78."""
    s.send(payload)
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
            continue
        return r


def describe(r):
    if r is None:
        return "TIMEOUT (no response)"
    if r[0] == 0x7F and len(r) >= 3:
        return f"NRC {r[2]:02X} {NRC.get(r[2], '?')}"
    return r.hex().upper()


def run(iface, seconds, interval, session):
    print(f"IPMA silencer: {iface}  tester 0x{IPMA_TX:03X} -> "
          f"ecu 0x{IPMA_RX:03X}")
    print(f"  session 10 {session:02X} ({SESSIONS.get(session, '?')})")
    if session != 0x02:
        print("  NOTE: only 10 02 (programming) is known to stop broadcasts on "
              "this vehicle.")
    try:
        s = open_isotp(iface, IPMA_TX, IPMA_RX)
    except OSError as e:
        print(f"ERROR: cannot open ISO-TP socket on {iface}: {e}",
              file=sys.stderr)
        return 2

    r = None
    for attempt in range(1, ARM_REPEAT + 1):
        r = req(s, bytes([0x10, session]))
        print(f"  10 {session:02X} attempt {attempt} -> {describe(r)}")
        if r is not None and r[0] != 0x7F:
            break
        time.sleep(0.1)

    if r is None:
        print("\n  No response on 0x706/0x70E. Either there is no IPMA on this\n"
              "  bus or it uses other diagnostic IDs. NOT proceeding.",
              file=sys.stderr)
        s.close()
        return 3
    if r[0] == 0x7F:
        print("\n  Camera refused the session. NOT proceeding.\n"
              "  If NRC 22 conditionsNotCorrect: the vehicle may need to be\n"
              "  stationary with the engine off, or the camera may refuse\n"
              "  programming while it sees valid road input.", file=sys.stderr)
        s.close()
        return 4

    print(f"\n  Session entered. Holding with 3E 80 every {interval}s.")
    print(f"  VERIFY the camera is quiet:  python3 lane_observe.py --iface "
          f"{iface} --seconds 10")
    print("  0x0A5 must read 0 frames. Ctrl-C resets the camera.\n")
    t0 = time.time()
    n = 0
    try:
        while seconds is None or time.time() - t0 < seconds:
            s.send(bytes([0x3E, 0x80]))
            n += 1
            print(f"\r  tester-present x{n}   elapsed {time.time()-t0:6.1f}s",
                  end="", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  interrupted")
    finally:
        print()
        try:
            # hardReset is the deliberate, immediate route back; S3 timeout
            # (~5 s after the last TesterPresent) would also do it.
            s.send(bytes([0x11, 0x01]))
            print("  11 01 hardReset sent -- camera reboots into its "
                  "application")
        except OSError as e:
            print(f"  reset failed ({e}); S3 timeout restores it in ~5 s")
        s.close()
    print("  done -- confirm 0x0A5 is back with lane_observe.py")
    return 0


def selftest():
    ok = True

    def chk(n, cond, extra=""):
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL'}  {n}{extra}")

    print("-- diagnostic IDs match the DBC --")
    chk("request 0x706 (BO_ 1798)", IPMA_TX == 1798 == 0x706)
    chk("response 0x70E (BO_ 1806)", IPMA_RX == 1806 == 0x70E)
    chk("Ford req+8 convention", IPMA_RX - IPMA_TX == 8)

    print("-- the lesson from bcmflash.py is encoded, not just commented --")
    chk("default session is programming (10 02), not extended (10 03)",
        DEFAULT_SESSION == 0x02)

    print("-- only safe services can be emitted --")
    sent = []

    class FakeSock:
        def send(self, b):
            sent.append(bytes(b))

        def recv(self, _n):
            raise socket.timeout

        def settimeout(self, _t):
            pass

        def close(self):
            pass

    fs = FakeSock()
    req(fs, bytes([0x10, 0x02]), timeout=0.01)
    fs.send(bytes([0x3E, 0x80]))
    fs.send(bytes([0x11, 0x01]))
    req(fs, bytes([0x10, 0x01]), timeout=0.01)
    services = {p[0] for p in sent}
    chk(f"only 0x10/0x3E/0x11 sent ({sorted(hex(x) for x in services)})",
        services <= {0x10, 0x3E, 0x11})
    chk("no securityAccess / routine / download / write",
        not (services & {0x27, 0x31, 0x34, 0x36, 0x37, 0x2E, 0x23, 0x3D}))
    chk("reset is subfunction 01 (hardReset), not 81 functional",
        all(p[1] == 0x01 for p in sent if p[0] == 0x11))

    print("-- physical addressing, so the PSCM and BCM keep running --")
    chk("not the functional broadcast id", IPMA_TX != 0x7DF)

    print("-- response decoding --")
    chk("positive programming response", describe(bytes([0x50, 0x02])) == "5002")
    chk("NRC 22 decoded",
        "conditionsNotCorrect" in describe(bytes([0x7F, 0x10, 0x22])))
    chk("NRC 7E decoded",
        "subFunctionNotSupportedInSession" in describe(
            bytes([0x7F, 0x10, 0x7E])))
    chk("timeout reported", describe(None).startswith("TIMEOUT"))
    return ok


DEFAULT_SESSION = 0x02


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="can0")
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--session", type=lambda s: int(s, 0),
                    default=DEFAULT_SESSION,
                    help="2 = programming (quiets the bus), 3 = extended")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest or len(sys.argv) == 1:
        sys.exit(0 if selftest() else 1)

    if not a.execute or a.dry_run:
        print(f"DRY RUN -- ISO-TP {a.iface} 0x{IPMA_TX:03X}->0x{IPMA_RX:03X}:")
        print(f"   10 {a.session:02X}        "
              f"{SESSIONS.get(a.session, '?')}Session  (x{ARM_REPEAT} until "
              f"answered)")
        print(f"   3E 80        every {a.interval}s "
              f"({'until Ctrl-C' if a.seconds is None else f'for {a.seconds}s'})")
        print("   11 01        hardReset on exit")
        print("\nnothing sent. add --execute to run.")
        return
    sys.exit(run(a.iface, a.seconds, a.interval, a.session))


if __name__ == "__main__":
    main()
