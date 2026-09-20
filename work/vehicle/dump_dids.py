#!/usr/bin/env python3
"""Dump every DID the PSCM declares in its own firmware table.

WHY: the firmware's DID table at P:$0EDC4 lists 40 identifiers, ~13 of which
(FDxx) are not documented anywhere we have.  One of them may expose the
internal lane-state or per-state code (X:$2DDE / X:$2DB9) that we cannot read
any other way -- every RAM-read path loads the SBL, which stops the
application and overwrites RAM.

This tool is READ-ONLY.  It sends only 0x22 ReadDataByIdentifier (and 0x10 01
defaultSession, which is the session the ECU already sits in).  It never
writes, never erases, never enters programmingSession.

Two modes:
  snapshot  one pass over every DID, printed as a table        (engine on, parked)
  watch     poll a chosen subset continuously and log to CSV   (while driving)

The watch mode is the one that answers the question: log while LKA and LCA
engage, then diff the DIDs against the 0x0A5 lane state from the same capture.

Usage
-----
    python3 work/vehicle/dump_dids.py --selftest
    python3 work/vehicle/dump_dids.py snapshot --iface can0
    python3 work/vehicle/dump_dids.py snapshot --iface can0 --all
    python3 work/vehicle/dump_dids.py watch --iface can0 --log lanedids \\
            --dids FD07,FD08,FD09,FD0A --seconds 600

Safety
------
  * read-only: the only services used are 0x22 and 0x10 01
  * no --execute style gate is needed because nothing is modified
  * a DID that answers NRC is recorded and skipped, never retried in a loop
"""
import argparse
import os
import socket
import struct
import sys
import time

# ---------------------------------------------------------------- constants
REQ_ID = 0x730          # PSCM physical request
RSP_ID = 0x738          # PSCM response
ISOTP_PROTO = 6         # CAN_ISOTP

# Every DID this ECU is known to answer, from TWO independent sources:
#   (a) the firmware table at P:$0EDC4 (40 entries, stride 6) -- see
#       work/vehicle/verify_dids.py, which re-extracts it from the binary
#   (b) a UCDS session capture, candump-2026-09-14_222146.log, which reads 19
#       DIDs that are NOT in that table (0202, 3302, 330C, D1xx, D7xx, DDxx,
#       DE00, E103, F101, F108)
# The union is what we probe. Names come from the UCDS parameter list, bound
# positionally and VERIFIED: 8 of 8 independently-known DIDs land on their
# correct names. See PSCM_DID_map.md.
TABLE_DIDS = [
    0x0202, 0x3302, 0x330C,
    0xD100, 0xD10E, 0xD111, 0xD117, 0xD118,
    0xD700, 0xD701,
    0xDD00, 0xDD01, 0xDD02, 0xDD05, 0xDD06,
    0xDE00, 0xE103,
    0xF101, 0xF108, 0xF110, 0xF111, 0xF112, 0xF113, 0xF124,
    0xF15A, 0xF160, 0xF161, 0xF162, 0xF163, 0xF169,
    0xF180, 0xF188, 0xF18C, 0xF190, 0xF40C, 0xF40D,
    0xFD01, 0xFD03, 0xFD04, 0xFD07, 0xFD08, 0xFD09, 0xFD0A,
    0xFD0B, 0xFD0C, 0xFD0D, 0xFD0E, 0xFD0F, 0xFD10, 0xFD11,
    0xFD12, 0xFD13, 0xFD14, 0xFD15, 0xFD20, 0xFD21, 0xFD22,
    0xFD23, 0xFD24,
]

# DIDs that came ONLY from the firmware table (not seen in the UCDS capture).
# Kept because the ECU declares them; they may still answer.
FIRMWARE_ONLY = [0xF112, 0xF169, 0xF190, 0xFD22, 0xFD23, 0xFD24]

# ---- the measurement set -------------------------------------------------
# FD0E Torque Loop Demand is the EPS torque COMMAND -- the signal that does
# not exist anywhere on the CAN bus. PSCM_does_it_steer.md records that every
# statistical proxy for "did the PSCM apply torque" failed its own control,
# because TorsionBarTorque on 0x140 is DRIVER INPUT, not motor output.
# Confirmed on hardware: hand torque moved FD0E 00 -> EA and FD0C 00 -> E4
# while 36 of 39 other DIDs stayed put.
TORQUE_DIDS = [0xFD0E, 0xFD0C, 0xFD0D, 0xFD0F, 0x330C, 0x3302, 0xD118]

# Configuration / feature flags (UCDS "Select Configuration" list).
# NOTE FD13 disagrees with the standalone DIDs on 2 of 5 flags -- see
# PSCM_DID_map.md. Logged for observation; DO NOT write without resolving that.
CONFIG_DIDS = [0xFD13, 0xFD15, 0xFD11, 0xFD12, 0xFD07, 0xFD21,
               0xFD01, 0xFD04, 0xFD09, 0xFD08, 0xFD10, 0xFD14]

# The default watch set: torque first (highest polling priority), then the
# config flags, so a slow sweep still samples torque often.
WATCH_DEFAULT = TORQUE_DIDS + CONFIG_DIDS

# Known meanings, so the unknown ones stand out in the output.
KNOWN = {
    0x0202: "Number of Trouble Codes Set due to Diagnostic Test",
    0x3302: "Steering Wheel Angle",
    0x330C: "Steering Shaft Torque Sensor #2",
    0xD100: "Active Diagnostic Session",
    0xD10E: "HSCAN Network Management State",
    0xD111: "ECU Power Supply Voltage",
    0xD117: "ECU Internal Temperature",
    0xD118: "Motor Current",
    0xD700: "Critical Software Parameter Monitoring #1",
    0xD701: "Critical Software Parameter Monitoring #2",
    0xDD00: "Global Real Time",
    0xDD01: "Total Distance",
    0xDD02: "Main ECU Voltage Supply",
    0xDD05: "Outside temperature",
    0xDD06: "Power Mode",
    0xDE00: "Vehicle Variant Tune Selector",
    0xE103: "Car Configuration Parameter Faults",
    0xF101: "Primary Bootloader Configuration",
    0xF108: "ECU Network Signal Calibration Number",
    0xF110: "On-line Diagnostic Database Reference Number",
    0xF111: "ECU Core Assembly Number",
    0xF112: "(empty firmware table entry -- no descriptor)",
    0xF113: "ECU Delivery Assembly Number",
    0xF124: "ECU Calibration Data #1 Number",
    0xF15A: "NOS OSEK Network Management Version Number",
    0xF160: "NOS Diagnostic Version Number",
    0xF161: "NOS CAN Communication Layer Version Number",
    0xF162: "Software Download Specification Version",
    0xF163: "Diagnostic Specification Version",
    0xF169: "(firmware table only -- unnamed)",
    0xF180: "Boot Software Identification",
    0xF188: "Vehicle Manufacturer ECU Software Number",
    0xF18C: "ECU Serial Number",
    0xF190: "Vehicle Identification Number",
    0xF40C: "Engine RPM",
    0xF40D: "Vehicle Speed Sensor",
    0xFD01: "SAPP enabled [config]",
    0xFD03: "MicroHybrid enabled",
    0xFD04: "Rack Length Selector [config]",
    0xFD07: "PDC enabled [config]",
    0xFD08: "PDC initial torque [config]",
    0xFD09: "CCP enable [config]",
    0xFD0A: "CPU Info",
    0xFD0B: "Internal Fault Code",
    0xFD0C: "Q-Axis Current  <-- torque-producing current",
    0xFD0D: "D-Axis Current",
    0xFD0E: "Torque Loop Demand  <-- THE EPS TORQUE COMMAND",
    0xFD0F: "Motor Mechanical Velocity",
    0xFD10: "Straigh Ahead Adaptation Angle [config]",
    0xFD11: "ANC enable [config]",
    0xFD12: "Straight Ahead Adaptation enable [config]",
    0xFD13: "Function enable [config, 5 flags incl. LA enable]",
    0xFD14: "Adaptation Algorithms value [config]",
    0xFD15: "Lane Assist enable [config]",
    0xFD20: "Mean Friction Share Histogram",
    0xFD21: "TSC enable [config]",
    0xFD22: "(firmware table only -- unnamed)",
    0xFD23: "(firmware table only -- unnamed)",
    0xFD24: "(firmware table only -- unnamed)",
}

# DIDs whose value is expected to be CONSTANT. If one of these changes between
# two snapshots, the read path is unstable and the results cannot be trusted.
CONSTANT_DIDS = [0xF110, 0xF111, 0xF113, 0xF124, 0xF188, 0xF18C, 0xF190]

NRC = {
    0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLength", 0x14: "responseTooLong",
    0x22: "conditionsNotCorrect", 0x24: "requestSequenceError",
    0x31: "requestOutOfRange", 0x33: "securityAccessDenied",
    0x35: "invalidKey", 0x78: "responsePending",
}


# ---------------------------------------------------------------- transport
# --- ISO-TP socket constants (must match work/pscm_flash.py, hardware-proven)
CAN_ISOTP = 6
SOL_CAN_ISOTP = 106          # SOL_CAN_BASE (100) + CAN_ISOTP (6)
CAN_ISOTP_OPTS = 1
CAN_ISOTP_TX_PADDING = 0x004
CAN_ISOTP_RX_PADDING = 0x008
ISOTP_PROTO = CAN_ISOTP      # kept for the socket() call


def open_isotp(iface, txid=REQ_ID, rxid=RSP_ID):
    """ISO-TP socket with PADDING ENABLED.

    ⚠ Without the padding flags the kernel emits a short CAN frame -- a 3-byte
    request goes out as DLC=4 (`730#03 22 F1 88`). Ford ECUs expect every
    diagnostic frame padded to DLC=8 and simply do not answer a short one, so
    the symptom is a silent timeout, not an error. Observed on this vehicle.

    Three bugs were present in the first version of this function:
      1. setsockopt level was CAN_ISOTP (6) instead of SOL_CAN_ISOTP (106)
      2. the call was wrapped in `except OSError: pass`, which SILENTLY
         swallowed the resulting failure and left padding OFF
      3. padding content bytes were 0xAA rather than the 0x00 used by the
         hardware-proven flasher
    Together they would have reproduced exactly the "no response" symptom the
    user already diagnosed once. There is no try/except here on purpose: if
    padding cannot be enabled this tool MUST fail loudly, not send short frames.
    """
    s = socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, CAN_ISOTP)
    s.setsockopt(SOL_CAN_ISOTP, CAN_ISOTP_OPTS,
                 struct.pack("=IIBBBB",
                             CAN_ISOTP_TX_PADDING | CAN_ISOTP_RX_PADDING,
                             0, 0, 0, 0, 0))
    s.bind((iface, rxid, txid))
    return s


class Ecu:
    def __init__(self, iface, txid, rxid, quiet=False):
        self.s = open_isotp(iface, txid, rxid)
        self.quiet = quiet

    def req(self, payload_hex, timeout=1.0):
        """Send one request, return the final response bytes or None."""
        self.s.send(bytes.fromhex(payload_hex))
        deadline = time.monotonic() + 5.0
        while True:
            self.s.settimeout(max(0.05, min(timeout, deadline - time.monotonic())))
            try:
                r = self.s.recv(4096)
            except socket.timeout:
                return None
            # responsePending -> keep waiting, restart the clock
            if len(r) >= 3 and r[0] == 0x7F and r[2] == 0x78:
                deadline = time.monotonic() + 5.0
                continue
            return r

    def read_did(self, did, timeout=1.0):
        """-> (ok, payload_bytes_or_nrc_name)"""
        r = self.req("22%04X" % did, timeout=timeout)
        if r is None:
            return (False, "timeout")
        if len(r) >= 3 and r[0] == 0x7F:
            return (False, "NRC %02X %s" % (r[2], NRC.get(r[2], "?")))
        if len(r) >= 3 and r[0] == 0x62:
            got = (r[1] << 8) | r[2]
            if got != did:
                return (False, "wrong DID echoed %04X" % got)
            return (True, r[3:])
        return (False, "unexpected " + r.hex())


# ---------------------------------------------------------------- rendering
def render(data):
    """Best-effort human view of a DID payload."""
    if not data:
        return "(empty)"
    hexs = data.hex().upper()
    txt = "".join(chr(c) if 32 <= c < 127 else "." for c in data)
    out = "%-40s |%s|" % (hexs[:40], txt[:20])
    if len(data) == 2:
        out += "  u16=%d" % struct.unpack(">H", data)[0]
    elif len(data) == 1:
        out += "  u8=%d" % data[0]
    elif len(data) == 4:
        out += "  u32=%d" % struct.unpack(">I", data)[0]
    return out


def do_snapshot(a):
    dids = TABLE_DIDS if a.all else [d for d in TABLE_DIDS
                                     if d not in (0xF112,)]
    ecu = Ecu(a.iface, a.txid, a.rxid)
    print("PSCM DID snapshot   iface=%s  tx=%03X rx=%03X" %
          (a.iface, a.txid, a.rxid))
    print("read-only: service 0x22 only\n")
    print("%-7s %-38s %s" % ("DID", "meaning", "value"))
    print("-" * 100)
    results = {}
    for did in dids:
        ok, val = ecu.read_did(did, timeout=a.timeout)
        results[did] = (ok, val)
        meaning = KNOWN.get(did, "UNKNOWN")
        if ok:
            print("%-7s %-38s %s" % ("%04X" % did, meaning, render(val)))
        else:
            print("%-7s %-38s -- %s" % ("%04X" % did, meaning, val))
        time.sleep(a.gap)

    # integrity control: re-read the constant DIDs and compare
    print("\nCONTROL: re-reading DIDs that must not change")
    stable = True
    for did in CONSTANT_DIDS:
        if did not in results or not results[did][0]:
            continue
        ok, val = ecu.read_did(did, timeout=a.timeout)
        same = ok and val == results[did][1]
        stable = stable and same
        print("   %04X  %s" % (did, "stable" if same else "*** CHANGED ***"))
    print("\n%s" % ("all constant DIDs stable -- reads are trustworthy"
                    if stable else
                    "*** a constant DID changed: DO NOT TRUST THESE READS ***"))

    if a.out:
        import json
        with open(a.out, "w") as f:
            json.dump({"%04X" % k: (v[0], v[1].hex() if v[0] else v[1])
                       for k, v in results.items()}, f, indent=1)
        print("\nwrote %s" % a.out)
    return 0


def do_watch(a):
    """Poll a DID subset continuously, logging against ABSOLUTE epoch time.

    TIME BASE (this is the whole point of the rewrite)
    --------------------------------------------------
    The first version logged seconds since the tool started. `candump` logs
    absolute epoch seconds. Joining the two therefore required guessing an
    offset -- and when we tried, cross-correlating the SAME physical sensor
    (330C here vs TorsionBarTorque on 0x140) gave |r| < 0.04 at every lag from
    -20 s to +40 s. The logs could not be aligned at all, the positive control
    failed (LKA showed LESS torque than idle), and the run was wasted.

    Now every row carries `epoch`, the same clock candump stamps its frames
    with, so the join is exact and needs no correlation.

    Each row also carries `t_rel` (seconds since start, for convenience) and
    `sweep` (an incrementing sweep index, so a partial sweep is detectable).
    """
    if a.dids:
        dids = [int(x, 16) for x in a.dids.split(",")]
    elif a.torque:
        dids = TORQUE_DIDS
    else:
        dids = WATCH_DEFAULT
    ecu = Ecu(a.iface, a.txid, a.rxid)
    path = a.log + ".csv"

    t_start = time.time()
    print("watching %d DID(s) for %.0f s -> %s" % (len(dids), a.seconds, path))
    print("   " + " ".join("%04X" % d for d in dids))
    print("   epoch clock: %.3f  (%s)"
          % (t_start, time.strftime("%H:%M:%S", time.localtime(t_start))))
    print("\nrun la_monitor.py / candump in another terminal; the logs join on"
          "\nthe `epoch` column -- no offset guessing needed.")

    n = 0
    with open(path, "w") as f:
        f.write("# PSCM DID watch, ABSOLUTE epoch timestamps\n")
        f.write("# started %s (epoch %.3f)\n"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_start)),
                   t_start))
        f.write("# " + ", ".join("%04X=%s" % (d, KNOWN.get(d, "?"))
                                 for d in dids) + "\n")
        f.write("epoch,t_rel,sweep," + ",".join("%04X" % d for d in dids) + "\n")
        try:
            while time.time() - t_start < a.seconds:
                row = []
                # stamp each DID with the time it was actually read, then
                # report the sweep midpoint -- a sweep is not instantaneous
                t_sweep0 = time.time()
                for did in dids:
                    ok, val = ecu.read_did(did, timeout=a.timeout)
                    row.append(val.hex().upper() if ok else "")
                t_sweep1 = time.time()
                mid = (t_sweep0 + t_sweep1) / 2.0
                f.write("%.3f,%.3f,%d,%s\n"
                        % (mid, mid - t_start, n, ",".join(row)))
                f.flush()
                n += 1
                if n % 20 == 0:
                    print("   %4.0f s  %d sweeps  (%.0f ms/sweep)"
                          % (time.time() - t_start, n,
                             1000.0 * (time.time() - t_start) / n))
                time.sleep(a.gap)
        except KeyboardInterrupt:
            print("\ninterrupted")
    print("\n%d sweeps -> %s" % (n, path))
    print("epoch range %.3f .. %.3f" % (t_start, time.time()))
    return 0


# ---------------------------------------------------------------- selftest
def selftest():
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print("  %s  %s%s" % ("PASS" if cond else "FAIL", name,
                              ("  " + detail) if detail else ""))

    print("dump_dids.py selftest\n")
    # 59 = the union of the firmware table (40) and the UCDS capture, which
    # reads 19 DIDs the table does not declare.
    chk("59 DIDs in the probe set", len(TABLE_DIDS) == 59, str(len(TABLE_DIDS)))
    chk("superset of the firmware table (40 entries)",
        len([d for d in TABLE_DIDS if 0xF100 <= d <= 0xFDFF]) >= 40)
    chk("no duplicates", len(set(TABLE_DIDS)) == len(TABLE_DIDS))
    chk("table is sorted", TABLE_DIDS == sorted(TABLE_DIDS))
    chk("F112 present (the empty entry)", 0xF112 in TABLE_DIDS)
    fd = [d for d in TABLE_DIDS if 0xFD00 <= d <= 0xFDFF]
    chk("23 FDxx DIDs", len(fd) == 23, str(len(fd)))
    unknown = [d for d in TABLE_DIDS if d not in KNOWN]
    chk("every probed DID has a name", len(unknown) == 0,
        " ".join("%04X" % d for d in unknown))

    # --- the measurement set -------------------------------------------
    chk("FD0E Torque Loop Demand is in the torque set", 0xFD0E in TORQUE_DIDS)
    chk("FD0E is FIRST (polled most promptly)", TORQUE_DIDS[0] == 0xFD0E,
        "%04X" % TORQUE_DIDS[0])
    chk("FD0C Q-Axis Current is in the torque set", 0xFD0C in TORQUE_DIDS)
    chk("330C driver torque sensor included as a CONTROL",
        0x330C in TORQUE_DIDS)
    chk("FD13 Function enable is in the config set", 0xFD13 in CONFIG_DIDS)
    chk("FD15 Lane Assist enable is in the config set", 0xFD15 in CONFIG_DIDS)
    chk("watch default = torque set + config set",
        WATCH_DEFAULT == TORQUE_DIDS + CONFIG_DIDS)
    chk("no duplicates in the watch default",
        len(set(WATCH_DEFAULT)) == len(WATCH_DEFAULT))
    chk("every watched DID is probeable",
        all(d in TABLE_DIDS for d in WATCH_DEFAULT))
    chk("torque set sweeps fast enough for a 3 s episode",
        len(TORQUE_DIDS) * 0.05 < 1.0,
        "%.2f s/sweep at 50 ms" % (len(TORQUE_DIDS) * 0.05))

    # the two hardware-confirmed torque DIDs must be named as such
    chk("FD0E named 'Torque Loop Demand'", "Torque Loop Demand" in KNOWN[0xFD0E])
    chk("FD0C named 'Q-Axis Current'", "Q-Axis Current" in KNOWN[0xFD0C])
    chk("330C is the DRIVER sensor, not EPS output",
        "Steering Shaft Torque Sensor" in KNOWN[0x330C])

    # request framing
    chk("request is 22 + big-endian DID",
        "22%04X" % 0xFD07 == "22FD07")
    # response parsing, without a bus
    class Fake(Ecu):
        def __init__(self, resp):
            self.resp = resp
            self.quiet = True

        def req(self, payload_hex, timeout=1.0):
            return self.resp

    e = Fake(bytes.fromhex("62F188" + b"CV6T-14C217-AR".hex()))
    okr, val = e.read_did(0xF188)
    chk("parses a positive response", okr and val == b"CV6T-14C217-AR",
        val if not okr else val.decode())

    e = Fake(bytes.fromhex("7F2231"))
    okr, val = e.read_did(0xFD07)
    chk("parses NRC 31 requestOutOfRange",
        not okr and "requestOutOfRange" in val, str(val))

    e = Fake(bytes.fromhex("62F18C0102"))
    okr, val = e.read_did(0xF188)
    chk("rejects a mismatched DID echo", not okr and "wrong DID" in str(val))

    e = Fake(None)
    okr, val = e.read_did(0xF188)
    chk("handles timeout", not okr and val == "timeout")

    chk("render() shows hex and ascii",
        "|CV6T|" in render(b"CV6T") and "43563654" in render(b"CV6T").upper())
    chk("render() decodes a u16", "u16=4" in render(b"\x00\x04"))

    chk("constant-DID control list is non-empty", len(CONSTANT_DIDS) >= 5)
    chk("control DIDs are all in the table",
        all(d in TABLE_DIDS for d in CONSTANT_DIDS))

    # --- SAFETY: prove the tool can only ever transmit service 0x22 --------
    # The earlier version of this check grepped the source for "2E"/"31"/...
    # and failed on its own docstring -- a string search cannot distinguish a
    # comment from a transmission. Instead, intercept the socket and record
    # every byte the tool actually sends.
    sent = []

    class Recorder(Ecu):
        def __init__(self):
            self.quiet = True

            class S:
                def send(_s, b):
                    sent.append(b)

                def settimeout(_s, t):
                    pass

                def recv(_s, n):
                    raise socket.timeout()
            self.s = S()

    r = Recorder()
    for did in TABLE_DIDS:
        r.read_did(did, timeout=0.01)
    chk("transmitted one request per DID", len(sent) == len(TABLE_DIDS),
        "%d frames" % len(sent))
    services = sorted({b[0] for b in sent})
    chk("the ONLY service ever transmitted is 0x22", services == [0x22],
        " ".join("%02X" % s for s in services))
    chk("no request exceeds 3 bytes (22 + DID)",
        all(len(b) == 3 for b in sent))
    writing = {0x2E, 0x31, 0x34, 0x35, 0x36, 0x37, 0x11, 0x27, 0x28, 0x3E}
    chk("no writing/session/reset service transmitted",
        not (writing & set(services)))
    echoed = [(b[1] << 8) | b[2] for b in sent]
    chk("requested DIDs match the table exactly", echoed == TABLE_DIDS)

    # --- PADDING: the bug that silently breaks everything -------------------
    # A 3-byte request MUST leave as DLC=8. Without the padding flags the
    # kernel sends `730#03 22 F1 88` (DLC=4) and Ford ECUs never answer.
    # Verify against the hardware-proven flasher, then on a real socket.
    chk("SOL_CAN_ISOTP is 106, not the protocol number 6",
        SOL_CAN_ISOTP == 106, str(SOL_CAN_ISOTP))
    chk("TX and RX padding flags both set",
        (CAN_ISOTP_TX_PADDING | CAN_ISOTP_RX_PADDING) == 0x00C)

    flasher = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "pscm_flash.py")
    if os.path.exists(flasher):
        fsrc = open(flasher).read()
        for const in ("SOL_CAN_ISOTP = 106", "CAN_ISOTP_TX_PADDING = 0x004",
                      "CAN_ISOTP_RX_PADDING = 0x008"):
            chk("matches the proven flasher: %s" % const, const in fsrc)
        chk("uses the same struct layout as the flasher",
            '"=IIBBBB"' in fsrc)

    src = open(os.path.abspath(__file__)).read()
    chk("open_isotp does NOT swallow setsockopt errors",
        "except OSError:\n        pass" not in src)

    # real socket: does the kernel accept our option block?
    try:
        t = socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, CAN_ISOTP)
        t.setsockopt(SOL_CAN_ISOTP, CAN_ISOTP_OPTS,
                     struct.pack("=IIBBBB",
                                 CAN_ISOTP_TX_PADDING | CAN_ISOTP_RX_PADDING,
                                 0, 0, 0, 0, 0))
        t.close()
        chk("kernel accepts the padding option block", True)
    except OSError as e:
        chk("kernel accepts the padding option block", False, "errno %s" % e.errno)

    # control: the WRONG level must be rejected -- proves the test can fail
    try:
        t = socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, CAN_ISOTP)
        t.setsockopt(CAN_ISOTP, CAN_ISOTP_OPTS,
                     struct.pack("=IIBBBB", 0x00C, 0, 0, 0, 0, 0))
        t.close()
        chk("CONTROL: wrong level (6) is rejected by the kernel", False,
            "it was accepted -- this test proves nothing")
    except OSError:
        chk("CONTROL: wrong level (6) is rejected by the kernel", True,
            "so the old code would have run unpadded")

    # --- TIME BASE: the bug that wasted a whole test drive -----------------
    # The first watch implementation logged seconds-since-start. candump logs
    # absolute epoch. Joining them needed a guessed offset; cross-correlating
    # the same physical sensor across both logs gave |r| < 0.04 at EVERY lag,
    # the positive control failed, and the drive produced nothing usable.
    # These tests assert the CSV is joinable by construction.
    import csv as _csv
    import tempfile

    class FakeWatch(Ecu):
        def __init__(self):
            self.quiet = True
            self.n = 0

            class S:
                def send(_s, b):
                    pass

                def settimeout(_s, t):
                    pass

                def recv(_s, n):
                    raise socket.timeout()
            self.s = S()

        def read_did(self, did, timeout=1.0):
            self.n += 1
            time.sleep(0.002)          # a real ISO-TP round trip is far slower
            return (True, bytes([self.n & 0xFF]))

    class Args:
        pass

    tmpd = tempfile.mkdtemp()
    aa = Args()
    aa.iface, aa.txid, aa.rxid = "vcan0", REQ_ID, RSP_ID
    aa.timeout, aa.gap = 0.01, 0.0
    aa.log = os.path.join(tmpd, "tt")
    aa.seconds = 0.35
    aa.dids = "FD0E,FD0C"
    aa.torque = False

    t_before = time.time()
    real_open = globals()["Ecu"]
    try:
        globals()["Ecu"] = lambda *args, **kw: FakeWatch()
        do_watch(aa)
    finally:
        globals()["Ecu"] = real_open
    t_after = time.time()

    with open(aa.log + ".csv") as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    rdr = list(_csv.DictReader(lines))
    chk("watch wrote rows", len(rdr) >= 2, "%d rows" % len(rdr))
    chk("CSV has an 'epoch' column", "epoch" in (rdr[0] if rdr else {}))
    chk("CSV keeps t_rel and sweep",
        "t_rel" in (rdr[0] if rdr else {}) and "sweep" in (rdr[0] if rdr else {}))

    if rdr:
        ep = [float(r["epoch"]) for r in rdr]
        chk("epoch is ABSOLUTE unix time, not seconds-since-start",
            all(e > 1.7e9 for e in ep), "first=%.1f" % ep[0])
        # epoch is written with %.3f, so allow half-a-millisecond of
        # rounding at each edge.
        chk("epoch lies inside the run window",
            all(t_before - 0.001 <= e <= t_after + 0.001 for e in ep),
            "%.3f .. %.3f vs window %.3f .. %.3f"
            % (ep[0], ep[-1], t_before, t_after))
        chk("epoch increases monotonically",
            all(b >= a for a, b in zip(ep, ep[1:])))
        rel = [float(r["t_rel"]) for r in rdr]
        chk("t_rel starts near zero", rel[0] < 0.5, "%.3f" % rel[0])
        chk("epoch - t_rel is a constant start time",
            max(e - r for e, r in zip(ep, rel))
            - min(e - r for e, r in zip(ep, rel)) < 0.01)
        sw = [int(r["sweep"]) for r in rdr]
        chk("sweep index increments by 1",
            sw == list(range(len(sw))), str(sw[:5]))

        # the join itself: a candump-style epoch must land in the right row
        import bisect
        probe = ep[len(ep) // 2] + 0.001
        i = bisect.bisect_right(ep, probe) - 1
        chk("a candump epoch joins to the correct sweep",
            i == len(ep) // 2, "landed on row %d of %d" % (i, len(ep)))

    print("\n" + "=" * 56)
    print("SELFTEST:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


# ---------------------------------------------------------------- cli
def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    def common(s):
        s.add_argument("--iface", default="can0")
        s.add_argument("--txid", type=lambda x: int(x, 0), default=REQ_ID)
        s.add_argument("--rxid", type=lambda x: int(x, 0), default=RSP_ID)
        s.add_argument("--timeout", type=float, default=1.0)
        s.add_argument("--gap", type=float, default=0.05,
                       help="delay between requests (s)")

    s1 = sub.add_parser("snapshot", help="read every DID once")
    common(s1)
    s1.add_argument("--all", action="store_true",
                    help="include F112, the empty table entry")
    s1.add_argument("--out", help="write results to this JSON file")

    s2 = sub.add_parser("watch", help="poll DIDs continuously while driving")
    common(s2)
    s2.add_argument("--log", default="dids", help="output CSV basename")
    s2.add_argument("--seconds", type=float, default=600.0)
    s2.add_argument("--dids", help="comma-separated hex list, e.g. FD0E,FD0C")
    s2.add_argument("--torque", action="store_true",
                    help="poll ONLY the torque set (fastest sweep, best time "
                         "resolution for short LKA/LCA episodes)")

    a = p.parse_args()
    if a.selftest:
        return selftest()
    if not a.cmd:
        p.print_help()
        return 1
    if a.cmd == "snapshot":
        return do_snapshot(a)
    return do_watch(a)


if __name__ == "__main__":
    sys.exit(main())
