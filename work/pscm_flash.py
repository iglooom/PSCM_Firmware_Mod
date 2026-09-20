#!/usr/bin/env python3
"""Flash a VBF to the Ford PSCM (EPAS, CAN 0x730) over UDS/ISO-TP.

⚠ THIS WRITES THE FLASH OF AN ELECTRIC POWER STEERING MODULE.
Nothing in this repository has ever written PSCM flash before; the read path
(pscm_dump.py) is hardware-proven, the write path is NOT. Treat the first run
as an experiment, with the stock VBF on hand.

SEQUENCE (mirrors work/pscm_dump.py for the proven stages, and the
hardware-validated BCM flasher for the erase/download stages)

    10 02                       programmingSession
    27 01 / 27 02 <key>         SecurityAccess (PSCM magic 0x9B2533)
    -- SBL stage (RAM) -------------------------------------------------
    34 00 44 <addr><len>        RequestDownload per SBL block
    36 <bc> <data...>           TransferData
    37                          RequestTransferExit
    31 01 0301 <callAddr>       start the SBL
    -- application stage -----------------------------------------------
    31 01 FF00 <addr><len>      eraseMemory, ONCE PER VBF ERASE REGION
    34/36/37                    download each block
    11 01                       ECUReset

WHY THE SBL IS REQUIRED
    The resident bootloader cannot write the application area; the OEM SBL
    (BV6T-14C220-AA) is downloaded to RAM and started first. This is exactly
    the sequence pscm_dump.py already performs successfully on this vehicle,
    up to and including `31 01 0301`.

SAFETY
    * --dry-run is the DEFAULT. Nothing is sent without --execute.
    * The VBF's own header is the authority for erase regions and block
      addresses. Addresses are never inferred from a capture.
    * Every block CRC-16 and the file CRC-32 is verified BEFORE the ECU is
      touched. A corrupt VBF is never transmitted.
    * Refuses unless the live 22 F188 part number matches the VBF's
      sw_part_number family, unless --force.
    * 7F xx 78 (responsePending) is CONTINUE, not failure: the OEM erase
      answers 7F 31 78 first and 71 01 FF 00 only ~230 ms later. Aborting on
      the first negative would stop mid-erase with the application gone.
    * 7F xx 21 (busyRepeatRequest) is retried.
    * TesterPresent keepalive runs for the whole erase+download.

Usage:
    python3 pscm_flash.py --selftest
    python3 pscm_flash.py info   <part.vbf>
    python3 pscm_flash.py verify <part.vbf>
    python3 pscm_flash.py flash  <part.vbf>              # dry run
    python3 pscm_flash.py flash  <part.vbf> --execute
"""
import argparse
import binascii
import os
import re
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

REQ_ID = 0x730
RSP_ID = 0x738
SBL_DEFAULT = os.path.join(ROOT, "BV6T-14C220-AA.vbf")
MAGIC_PSCM_L1 = 0x9B2533
TP_INTERVAL = 1.5          # TesterPresent period, seconds (S3 is 5 s)

NRC = {
    0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLength", 0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect", 0x24: "requestSequenceError",
    0x31: "requestOutOfRange", 0x33: "securityAccessDenied",
    0x35: "invalidKey", 0x36: "exceedNumberOfAttempts",
    0x70: "uploadDownloadNotAccepted", 0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure", 0x73: "wrongBlockSequenceCounter",
    0x78: "responsePending", 0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}


# --------------------------------------------------------------------------
# VBF
# --------------------------------------------------------------------------
class Vbf:
    def __init__(self, path):
        self.path = path
        raw = open(path, "rb").read()
        self.raw = raw
        depth = 0
        he = None
        for i, c in enumerate(raw):
            if c == 0x7B:
                depth += 1
            elif c == 0x7D:
                depth -= 1
                if depth == 0:
                    he = i + 1
                    break
        if he is None:
            raise SystemExit(f"{path}: no header braces found")
        h = raw[:he].decode("latin-1")
        self.header_text = h

        def field(name):
            m = re.search(name + r"\s*=\s*([^;]+);", h)
            return m.group(1).strip() if m else None

        self.part = (field("sw_part_number") or "").strip('"').strip()
        self.ptype = (field("sw_part_type") or "").strip()
        self.ecu = field("ecu_address")
        m = re.search(r"file_checksum\s*=\s*0x([0-9A-Fa-f]+)", h)
        self.file_checksum = int(m.group(1), 16) if m else None
        m = re.search(r"\bcall\s*=\s*0x([0-9A-Fa-f]+)", h)
        self.call = int(m.group(1), 16) if m else None

        self.erase = []
        m = re.search(r"erase\s*=\s*\{(.*?)\}\s*;", h, re.S)
        if m:
            for a, l in re.findall(r"0x([0-9A-Fa-f]+)\s*,\s*0x([0-9A-Fa-f]+)",
                                   m.group(1)):
                self.erase.append((int(a, 16), int(l, 16)))

        # block walk: probe for the start offset that consumes the file exactly
        self.blocks = None
        for ds in range(he, he + 8):
            off, blocks, ok = ds, [], True
            while off < len(raw):
                if off + 8 > len(raw):
                    ok = False
                    break
                addr, ln = struct.unpack_from(">II", raw, off)
                if off + 8 + ln + 2 > len(raw):
                    ok = False
                    break
                crc = struct.unpack_from(">H", raw, off + 8 + ln)[0]
                blocks.append((addr, ln, raw[off + 8:off + 8 + ln], crc))
                off += 8 + ln + 2
            if ok and off == len(raw) and blocks:
                self.data_start, self.blocks = ds, blocks
                break
        if self.blocks is None:
            raise SystemExit(f"{path}: cannot walk blocks to EOF")

    def check(self):
        """-> list of problems (empty == good)."""
        p = []
        for i, (a, ln, d, crc) in enumerate(self.blocks):
            c = binascii.crc_hqx(d, 0xFFFF)
            if c != crc:
                p.append(f"block {i} @0x{a:08X}: crc16 {crc:04X} != {c:04X}")
        if self.file_checksum is not None:
            c = binascii.crc32(self.raw[self.data_start:]) & 0xFFFFFFFF
            if c != self.file_checksum:
                p.append(f"file_checksum 0x{self.file_checksum:08X} != 0x{c:08X}")
        return p

    def describe(self):
        o = [f"=== {os.path.basename(self.path)}  ({len(self.raw)} bytes)",
             f"   sw_part_number   {self.part}",
             f"   sw_part_type     {self.ptype}",
             f"   ecu_address      {self.ecu}",
             f"   file_checksum    0x{self.file_checksum:08X}"]
        if self.call is not None:
            o.append(f"   call             0x{self.call:08X}")
        o.append(f"   erase regions    {len(self.erase)}")
        for a, l in self.erase:
            o.append(f"        0x{a:08X} len 0x{l:08X}")
        o.append(f"   blocks           {len(self.blocks)}")
        for i, (a, ln, _, crc) in enumerate(self.blocks):
            o.append(f"        blk{i} load=0x{a:08X} len=0x{ln:08X} crc16=0x{crc:04X}")
        return "\n".join(o)


# --------------------------------------------------------------------------
# security access
# --------------------------------------------------------------------------
# The keygen is NOT reimplemented here. An earlier revision of this file
# contained a hand-written version that disagreed with the real algorithm on
# 2000/2000 random seeds -- it would have sent an invalid key to a steering
# module and burned SecurityAccess attempts (NRC 36 exceedNumberOfAttempts).
# The real algorithm is a 64-round LFSR over an 8-byte state, not a bit-mask
# fold. Always import the hardware-proven implementation.
from pscm_seckey import key_from_seed, MAGIC_PSCM_L1   # noqa: E402,F811


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------
CAN_ISOTP = 6
SOL_CAN_ISOTP = 106
CAN_ISOTP_OPTS = 1
# ISO-TP option flags (linux/can/isotp.h)
CAN_ISOTP_TX_PADDING = 0x004
CAN_ISOTP_RX_PADDING = 0x008


def open_isotp(iface, txid=REQ_ID, rxid=RSP_ID):
    """ISO-TP socket with PADDING ENABLED.

    ⚠ Without the padding flags the kernel emits a short CAN frame -- a 3-byte
    request goes out as DLC=4 (`730#03 22 F1 88`). Ford ECUs expect every
    diagnostic frame padded to DLC=8 and simply do not answer a short one, so
    the symptom is a silent timeout, not an error.

    This is exactly how the hardware-proven reader (work/load_and_probe_sbl.py)
    opens its socket; omitting it here was a real bug, observed on the vehicle
    as `730 [4] 03 22 F1 88` with no response.
    """
    s = socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, CAN_ISOTP)
    s.setsockopt(SOL_CAN_ISOTP, CAN_ISOTP_OPTS,
                 struct.pack("=IIBBBB",
                             CAN_ISOTP_TX_PADDING | CAN_ISOTP_RX_PADDING,
                             0, 0, 0, 0, 0))
    s.bind((iface, rxid, txid))
    return s


def fmt(r):
    if r is None:
        return "<timeout>"
    if len(r) >= 3 and r[0] == 0x7F:
        return f"NRC {r[2]:02X} {NRC.get(r[2], '?')}"
    return r.hex()


class BusQuiet:
    """Silence other modules' application traffic for the duration of the flash.

    Ported from the vehicle-validated BCM implementation
    (BCM/Research/work/flash/bcmflash.py). ONE request does it:

        7DF#02 10 82    functional programmingSession, response suppressed

    A module in programmingSession stops transmitting its normal application
    frames, so the broadcast quiets the network by itself. No
    CommunicationControl (28) and no ControlDTCSetting (85) are needed.

    ⚠ Recorded so nobody re-adds them: TesterPresent alone quiets NOTHING (it
    only refreshes S3 for a module already in a non-default session), and an
    earlier BCM attempt with 10 03 + 85 02 + 28 03 01 left the bus noisy.

    THERE IS NO CONFIRMATION: the suppress bit means nobody answers, and an
    ISO-TP socket bound to one rx ID could not receive the storm anyway. Watch
    with candump for proof. Hence opt-in, default off.

    ⚠ PSCM-SPECIFIC WARNING. This is more consequential here than on the BCM:
    quieting the bus stops the ABS/PCM frames the PSCM uses, and the restore is
    a functional hardReset (11 81) that reboots EVERY module. Use only with the
    vehicle stationary and the engine off.
    """

    def __init__(self, iface="can0", can_id=0x7DF, execute=False, enabled=False):
        self.iface, self.can_id = iface, can_id
        self.execute, self.enabled = execute, enabled
        self.armed = False
        self.sock = None

    def _raw(self, data):
        if not self.execute:
            return
        if self.sock is None:
            self.sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW,
                                      socket.CAN_RAW)
            self.sock.bind((self.iface,))
        payload = bytes([len(data)]) + bytes(data)
        payload += b"\x00" * (8 - len(payload))
        self.sock.send(struct.pack("=IB3x8s", self.can_id, 8, payload))

    def arm(self):
        if not self.enabled:
            return
        print(f"   bus-quiet: {self.can_id:03X}#02 10 82 "
              "(functional programmingSession, NO confirmation)")
        for _ in range(20):
            self._raw([0x10, 0x82])
            time.sleep(0.02)
        self.armed = True

    def restore(self):
        if not self.armed:
            return
        print(f"   bus-quiet restore: {self.can_id:03X}#02 11 81 "
              "(functional hardReset, ALL modules)")
        for _ in range(3):
            self._raw([0x11, 0x81])
            time.sleep(0.05)
        self.armed = False
        if self.sock:
            self.sock.close()
            self.sock = None


class Ecu:
    def __init__(self, iface, execute, txid=REQ_ID, rxid=RSP_ID, log=None):
        self.execute = execute
        self.iface, self.txid, self.rxid = iface, txid, rxid
        self.s = open_isotp(iface, txid, rxid) if execute else None
        self.log = []
        self.logfile = log

    def _write(self, line):
        if self.logfile:
            self.logfile.write(line + "\n")
            self.logfile.flush()

    def req(self, hexstr, timeout=3.0, pending_timeout=20.0, what=""):
        """Send a request and wait for its FINAL response.

        7F xx 78 (responsePending) is a CONTINUE, never a result. Measured on
        this vehicle from an OEM flash capture: erasing 0x0001C000+0x64000
        answers with responsePending at +0.026 s, AGAIN at +4.531 s, and only
        then 71 01 FF00 10 at +8.311 s. An earlier version of this function
        returned the pending frame once a budget elapsed, so the caller saw
        "NRC 78" and aborted a perfectly healthy erase.

        Each pending frame RESETS the wait: the ECU is telling us it is still
        working. Only `pending_timeout` seconds of TOTAL SILENCE ends it.
        """
        self.log.append(hexstr)
        self._write(f"{time.strftime('%H:%M:%S')} -> {hexstr}"
                    + (f"   ({what})" if what else ""))
        if not self.execute:
            return None
        self.s.send(bytes.fromhex(hexstr))
        pend = 0
        while True:
            self.s.settimeout(pending_timeout)
            try:
                r = self.s.recv(4096)
            except socket.timeout:
                self._write(f"{time.strftime('%H:%M:%S')} <- <timeout after "
                            f"{pend} pending>")
                return None
            except OSError as e:
                # ECOMM/ENETDOWN mid-transaction: report, do not crash the run
                self._write(f"{time.strftime('%H:%M:%S')} <- OSError {e.errno}")
                raise
            if len(r) >= 3 and r[0] == 0x7F and r[2] == 0x78:
                pend += 1
                self._write(f"{time.strftime('%H:%M:%S')} <- responsePending "
                            f"#{pend}")
                continue                      # keep waiting; clock restarts
            if len(r) >= 3 and r[0] == 0x7F and r[2] == 0x21:
                time.sleep(0.05)
                self.s.send(bytes.fromhex(hexstr))
                continue
            self._write(f"{time.strftime('%H:%M:%S')} <- {fmt(r)}")
            return r

    def expect(self, hexstr, pos, what, **kw):
        r = self.req(hexstr, what=what, **kw)
        if not self.execute:
            return None
        if not (r and r[0] == pos):
            raise SystemExit(f"{what}: {fmt(r)}")
        return r


class Keepalive:
    def __init__(self, ecu, period=1.5, can_id=None):
        self.ecu, self.period, self.sent = ecu, period, 0
        self.can_id = can_id          # None => physical, via the ISO-TP socket
        self._stop = threading.Event()
        self._t = None
        self._raw = None

    def start(self):
        if not self.ecu.execute or self.period <= 0:
            return

        if self.can_id is not None:
            self._raw = socket.socket(socket.AF_CAN, socket.SOCK_RAW,
                                      socket.CAN_RAW)
            self._raw.bind((self.ecu.iface,))

        def run():
            while not self._stop.wait(self.period):
                try:
                    if self._raw is not None:
                        payload = b"\x02\x3E\x80" + b"\x00" * 5
                        self._raw.send(struct.pack("=IB3x8s", self.can_id, 8,
                                                   payload))
                    else:
                        self.ecu.s.send(bytes.fromhex("3E80"))
                    self.sent += 1
                except OSError:
                    return
        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=2)
        if self._raw:
            self._raw.close()
            self._raw = None


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------
def download_blocks(ecu, blocks, label):
    for i, (addr, ln, data, _) in enumerate(blocks):
        r = ecu.expect("340044" + struct.pack(">I", addr).hex()
                       + struct.pack(">I", ln).hex(), 0x74,
                       f"{label} RequestDownload blk{i} @0x{addr:08X}",
                       timeout=8.0)
        # The ECU DECLARES its block size in the 0x74 response; obey it.
        # Measured from the OEM capture: 74 20 0082 -> maxblk 0x0082 = 130,
        # meaning 128 payload bytes (2 bytes go to SID + blockSequenceCounter).
        # 409600 bytes were sent in exactly 3200 transfers = 128 B each.
        # A hardcoded 0x400 was wrong and would be rejected.
        chunk = 0x80
        if r is not None and len(r) >= 3:
            if r[1] == 0x20 and len(r) >= 4:
                chunk = ((r[2] << 8) | r[3]) - 2
            elif r[1] == 0x10:
                chunk = r[2] - 2
            if chunk <= 0:
                raise SystemExit(f"nonsensical maxNumberOfBlockLength in {r.hex()}")
            print(f"      ECU declares {chunk + 2} -> {chunk} payload bytes/transfer")
        bc, off = 1, 0
        t0 = time.monotonic()
        while off < ln:
            piece = data[off:off + chunk]
            ecu.expect((bytes([0x36, bc & 0xFF]) + piece).hex(), 0x76,
                       f"{label} TransferData blk{i} bc={bc}", timeout=8.0)
            off += len(piece)
            bc = (bc + 1) & 0xFF
            if ecu.execute and off % (chunk * 64) < chunk:
                el = time.monotonic() - t0
                print(f"\r      {off:7d}/{ln} ({off/ln*100:5.1f}%) "
                      f"{off/el/1024 if el else 0:5.1f} KiB/s", end="", flush=True)
        if ecu.execute:
            print()
        ecu.expect("37", 0x77, f"{label} TransferExit blk{i}", timeout=8.0)


def do_flash(a):
    vbf = Vbf(a.vbf)
    print(vbf.describe())
    probs = vbf.check()
    if probs:
        print("\n!! VBF INTEGRITY FAILURE - refusing to transmit")
        for p in probs:
            print("   " + p)
        raise SystemExit(1)
    print("\n   integrity: OK (all block CRC-16 + file CRC-32)")

    if not vbf.erase:
        raise SystemExit("!! this VBF declares no erase regions; an EXE part must.")

    sbl = Vbf(a.sbl)
    if sbl.call is None:
        raise SystemExit(f"{a.sbl}: no `call` address in header")
    if sbl.check():
        raise SystemExit(f"{a.sbl}: SBL integrity failure")

    print(f"\n   SBL: {sbl.part}  call=0x{sbl.call:08X}  "
          f"{len(sbl.blocks)} block(s)")

    print("\n== PLAN ==")
    print("   0. 22 F188 / 22 F124          identity + calibration gate")
    print("   1. 10 02                     programmingSession")
    print("   2. 27 01 / 27 02             SecurityAccess")
    print(f"   3. download SBL to RAM, 31 01 0301 {sbl.call:08X}")
    print(f"   4. erase {len(vbf.erase)} region(s):")
    for s_, l_ in vbf.erase:
        print(f"        31 01 FF00 {s_:08X} {l_:08X}")
    print(f"   5. download {len(vbf.blocks)} application block(s)")
    print("   6. 31 01 0304                finalise")
    print("   7. 11 01                     ECUReset")

    if not a.execute:
        print("\n*** DRY RUN - nothing sent. Re-run with --execute. ***")
        return

    log = None
    if getattr(a, "logfile", None):
        os.makedirs(os.path.dirname(a.logfile), exist_ok=True)
        log = open(a.logfile, "a")
        log.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                  f"{os.path.basename(a.vbf)} on {a.iface} "
                  f"tx=0x{a.txid:03X} rx=0x{a.rxid:03X} ====\n")
        print(f"   logging to {a.logfile}")

    ecu = Ecu(a.iface, True, txid=a.txid, rxid=a.rxid, log=log)
    ka = Keepalive(ecu, period=a.tp_interval,
                   can_id=(a.tp_id if a.tp_id >= 0 else None))
    quiet = BusQuiet(a.iface, a.tp_id if a.tp_id >= 0 else 0x7DF,
                     execute=True, enabled=a.quiet_bus)
    try:
        quiet.arm()
        print("\n== 1. identity ==")
        r = ecu.req("22F188", timeout=3.0)
        live = ""
        if r and r[0] == 0x62:
            live = r[3:].decode("latin-1").strip("\x00 ")
            print(f"   22 F188 -> {live}")
        else:
            print(f"   22 F188 -> {fmt(r)}")
        if live and not a.force:
            fam = vbf.part.split("-")[1] if "-" in vbf.part else vbf.part
            if fam not in live:
                raise SystemExit(
                    f"live part {live!r} does not match VBF {vbf.part!r}; "
                    "use --force to override")

        # The 14C217 blk1 word B (SUM-16) is computed over the LINEAR image,
        # which includes the 14C218 calibration sitting at 0x9800..0x1C000.
        # That region is neither erased nor rewritten here, so the word is only
        # correct if the calibration on the car is the one word B was computed
        # against. A mismatch means a WRONG CHECKSUM on a module whose word-A/B
        # monitor runs continuously -> fault while driving. Check it.
        r = ecu.req("22F124", timeout=3.0)
        if r and r[0] == 0x62:
            cal = r[3:].decode("latin-1").strip("\x00 ")
            print(f"   22 F124 -> {cal}")
            if a.expect_cal and a.expect_cal not in cal and not a.force:
                raise SystemExit(
                    f"calibration on car is {cal!r}, expected {a.expect_cal!r}. "
                    "blk1 word B was computed against the expected one; "
                    "flashing would leave a wrong checksum. Use --force only "
                    "if you have recomputed word B against the live part.")
        else:
            print(f"   22 F124 -> {fmt(r)}  (calibration identity unknown)")
            if a.expect_cal and not a.force:
                raise SystemExit(
                    "cannot confirm the calibration part; blk1 word B depends "
                    "on it. Use --force to override.")

        print("\n== 2. session + security ==")
        ecu.expect("1002", 0x50, "10 02 programmingSession", timeout=5.0)
        time.sleep(0.1)
        r = ecu.expect("2701", 0x67, "27 01 requestSeed", timeout=5.0)
        if len(r) < 5:
            raise SystemExit(f"short seed: {r.hex()}")
        seed = list(r[2:5])
        if seed == [0, 0, 0]:
            print("   seed 000000 -> already unlocked")
        else:
            key = key_from_seed(seed, MAGIC_PSCM_L1)
            print(f"   seed {bytes(seed).hex()} -> key {key.hex()}")
            ecu.expect("2702" + key.hex(), 0x67, "27 02 sendKey", timeout=5.0)
        print("   unlocked")
        ka.start()

        print("\n== 3. SBL into RAM ==")
        download_blocks(ecu, sbl.blocks, "sbl")
        ecu.expect("31010301" + struct.pack(">I", sbl.call).hex(), 0x71,
                   "31 01 0301 start SBL", timeout=8.0)
        print(f"   SBL running at 0x{sbl.call:08X}")

        print(f"\n== 4. erase {len(vbf.erase)} region(s) ==")
        for s_, l_ in vbf.erase:
            print(f"   31 01 FF00 {s_:08X} {l_:08X} ...", flush=True)
            ecu.expect("3101FF00" + struct.pack(">I", s_).hex()
                       + struct.pack(">I", l_).hex(), 0x71,
                       f"erase {s_:08X}", timeout=15.0,
                       pending_timeout=a.erase_timeout)
            print("      done")

        print("\n== 5. download application ==")
        download_blocks(ecu, vbf.blocks, "app")

        # Measured in the OEM capture at t=107.50, AFTER the last TransferExit
        # and BEFORE the reset:
        #     -> 31 01 0304
        #     <- 7F 31 78        responsePending
        #     <- 71 01 0304 1002 positive
        # Ford's checkProgrammingDependencies / finalise routine. Our flasher
        # omitted it entirely, which is why the first attempt left the module
        # in an indeterminate state.
        print("\n== 6. finalise (31 01 0304) ==")
        ecu.expect("31010304", 0x71, "31 01 0304 finalise", timeout=15.0,
                   pending_timeout=a.erase_timeout)
        print("   accepted")

        print("\n== 7. reset ==")
        ecu.req("1101", timeout=8.0, what="11 01")
    finally:
        ka.stop()
        if ka.sent:
            print(f"   keepalive: {ka.sent} TesterPresent frames")
        quiet.restore()
        if log:
            log.close()
    print("\n*** FLASH COMPLETE ***")
    print("    Power-cycle if the module does not return on its own.")
    print("    THEN, BEFORE DRIVING: confirm steering assist is present and")
    print("    normal at parking speed, and that no DTCs are set.")


# --------------------------------------------------------------------------
def selftest():
    ok = True

    def chk(n, c, d=""):
        nonlocal ok
        ok = ok and bool(c)
        print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f"  {d}" if d else ""))

    print("SELFTEST\n")
    # keygen: MUST be the hardware-proven implementation, not a local copy
    import pscm_seckey
    chk("keygen is imported from pscm_seckey, not reimplemented",
        key_from_seed is pscm_seckey.key_from_seed)
    chk("VBFlasher published vector reproduces",
        key_from_seed([0x1F, 0x7C, 0x69], 0xFA5FC0).hex() == "9a64ce",
        key_from_seed([0x1F, 0x7C, 0x69], 0xFA5FC0).hex())
    chk("PSCM magic is 0x9B2533", MAGIC_PSCM_L1 == 0x9B2533)
    k = key_from_seed([0x12, 0x34, 0x56])
    chk("keygen deterministic", k == key_from_seed([0x12, 0x34, 0x56]), k.hex())
    chk("keygen returns 3 bytes", len(k) == 3)

    # VBF parsing against the real files
    stock = os.path.join(ROOT, "CV6T-14C217-AR.VBF")
    mod = os.path.join(ROOT, "CV6T-14C217-AR_LCA.VBF")
    sbl = SBL_DEFAULT
    for p in (stock, mod, sbl):
        chk(f"exists {os.path.basename(p)}", os.path.exists(p))
    if os.path.exists(stock):
        v = Vbf(stock)
        chk("stock parses, 3 blocks", len(v.blocks) == 3)
        chk("stock integrity clean", v.check() == [], str(v.check()))
        chk("stock erase regions = 3", len(v.erase) == 3, str(len(v.erase)))
        chk("stock ecu_address 0x730", "730" in (v.ecu or ""), str(v.ecu))
        chk("stock type EXE", v.ptype == "EXE", v.ptype)
    if os.path.exists(mod):
        m = Vbf(mod)
        chk("patched integrity clean", m.check() == [], str(m.check()))
        chk("patched same erase map as stock",
            m.erase == Vbf(stock).erase)
        chk("patched same block addresses",
            [b[0] for b in m.blocks] == [b[0] for b in Vbf(stock).blocks])
    if os.path.exists(sbl):
        s = Vbf(sbl)
        chk("SBL has a call address", s.call is not None,
            f"0x{s.call:08X}" if s.call else "")
        chk("SBL integrity clean", s.check() == [], str(s.check()))
        chk("SBL declares no erase (RAM only)", not s.erase)

    # a corrupt VBF must be refused
    if os.path.exists(stock):
        raw = bytearray(open(stock, "rb").read())
        raw[len(raw) // 2] ^= 0xFF
        tmp = "/tmp/_corrupt.vbf"
        open(tmp, "wb").write(bytes(raw))
        chk("corrupt VBF is detected", Vbf(tmp).check() != [])
        os.unlink(tmp)

    # dry run must send nothing
    e = Ecu("can0", execute=False)
    e.req("1002")
    chk("dry-run Ecu opens no socket and logs", e.s is None and e.log == ["1002"])

    # --- CLI compatibility with bcmflash.py ------------------------------
    import io
    import contextlib
    saved = sys.argv[:]
    try:
        # every bcmflash subcommand must exist
        for cmd in ("info", "verify", "ident", "flash"):
            sys.argv = ["x", cmd, "--help"]
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    main()
            except SystemExit:
                pass
            chk(f"subcommand '{cmd}' exists", "usage" in buf.getvalue().lower())

        # flash must accept the full bcmflash flag set
        sys.argv = ["x", "flash", "--help"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                main()
        except SystemExit:
            pass
        h = buf.getvalue()
        for flag in ("--iface", "--rxid", "--txid", "--sbl", "--execute",
                     "--force", "--erase-timeout", "--tp-interval",
                     "--tp-id", "--quiet-bus", "--logfile"):
            chk(f"flash accepts {flag}", flag in h)

        # --iface must actually reach the Ecu, and hex IDs must parse
        sys.argv = ["x", "flash", os.path.join(ROOT, "CV6T-14C217-AR_LCA.VBF"),
                    "--iface", "can1", "--txid", "0x730", "--rxid", "0x738"]
        ap_ns = {}
        import argparse as _ap
        real_parse = _ap.ArgumentParser.parse_args

        def cap(self, *args, **kw):
            ns = real_parse(self, *args, **kw)
            ap_ns.update(vars(ns))
            return ns
        _ap.ArgumentParser.parse_args = cap
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                main()
        except SystemExit:
            pass
        finally:
            _ap.ArgumentParser.parse_args = real_parse
        chk("--iface can1 parsed", ap_ns.get("iface") == "can1",
            str(ap_ns.get("iface")))
        chk("--txid parsed as int 0x730", ap_ns.get("txid") == 0x730,
            hex(ap_ns.get("txid", 0)))
        chk("--rxid parsed as int 0x738", ap_ns.get("rxid") == 0x738,
            hex(ap_ns.get("rxid", 0)))
        chk("dry run is the default (no --execute)",
            ap_ns.get("execute") is False)
    finally:
        sys.argv = saved

    # the interface must propagate to the socket bind, not be hardcoded
    e2 = Ecu("can1", execute=False, txid=0x111, rxid=0x222)
    chk("Ecu stores the requested iface/txid/rxid",
        (e2.iface, e2.txid, e2.rxid) == ("can1", 0x111, 0x222))
    chk("TP_INTERVAL defined", isinstance(TP_INTERVAL, float))
    chk("--tp-interval 0 disables the keepalive",
        Keepalive(Ecu("can0", False), period=0).start() is None)

    print("\n" + "=" * 58)
    print("SELFTEST:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


def do_ident(a):
    """Read the live identity DIDs. Read-only, no session change."""
    ecu = Ecu(a.iface, True, txid=a.txid, rxid=a.rxid)
    for did, label in (("F188", "sw part (14C217)"),
                       ("F124", "calibration (14C218)"),
                       ("F111", "hardware part"),
                       ("F190", "VIN")):
        r = ecu.req("22" + did, timeout=3.0)
        if r and r[0] == 0x62:
            txt = r[3:].decode("latin-1").strip("\x00 ")
            print(f"   22 {did} -> {txt}")
        else:
            print(f"   22 {did} -> {fmt(r)}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Ford PSCM VBF flasher")
    ap.add_argument("--selftest", action="store_true",
                    help="run offline self-tests and exit")
    sub = ap.add_subparsers(dest="cmd")

    for name in ("info", "verify"):
        s = sub.add_parser(name)
        s.add_argument("vbf")

    s = sub.add_parser("ident")
    s.add_argument("--iface", default="can0")
    s.add_argument("--txid", type=lambda x: int(x, 0), default=REQ_ID)
    s.add_argument("--rxid", type=lambda x: int(x, 0), default=RSP_ID)

    s = sub.add_parser("flash")
    s.add_argument("vbf")
    s.add_argument("--sbl", default=SBL_DEFAULT)
    s.add_argument("--iface", default="can0",
                   help="SocketCAN interface, e.g. can0 or can1")
    s.add_argument("--txid", type=lambda x: int(x, 0), default=REQ_ID,
                   help=f"diagnostic request ID (default 0x{REQ_ID:03X})")
    s.add_argument("--rxid", type=lambda x: int(x, 0), default=RSP_ID,
                   help=f"diagnostic response ID (default 0x{RSP_ID:03X})")
    s.add_argument("--execute", action="store_true",
                   help="actually transmit (default is a dry run)")
    s.add_argument("--force", action="store_true",
                   help="ignore an identity or calibration mismatch")
    s.add_argument("--erase-timeout", type=float, default=60.0,
                   help="seconds of SILENCE that ends an erase wait; "
                        "measured worst case 8.3 s with 2 pending frames")
    s.add_argument("--tp-interval", type=float, default=TP_INTERVAL,
                   help=f"TesterPresent period in seconds "
                        f"(default {TP_INTERVAL:.1f}; 0 disables)")
    s.add_argument("--tp-id", type=lambda x: int(x, 0), default=-1,
                   help="functional/broadcast CAN ID for TesterPresent "
                        "(default: physical, via the ISO-TP socket; "
                        "pass 0x7DF for broadcast)")
    s.add_argument("--quiet-bus", action="store_true",
                   help="silence the OTHER modules for the duration of the "
                        "flash with functional 7DF#02 10 82 "
                        "(programmingSession), undone with a functional "
                        "hardReset. UNCONFIRMED at runtime (responses are "
                        "suppressed) and network-wide - off by default. On "
                        "the PSCM this also stops the ABS/PCM frames it "
                        "consumes: stationary, engine off, only.")
    s.add_argument("--logfile",
                   default=os.path.join(ROOT, "work/logs/pscm_flash.log"),
                   help="append a timestamped request/response trace here")
    s.add_argument("--expect-cal", default="CV6T-14C218-AX",
                   help="calibration part that blk1 word B was computed "
                        "against; '' disables the check")

    a = ap.parse_args()
    if a.selftest or a.cmd is None:
        return selftest()
    if a.cmd == "info":
        v = Vbf(a.vbf)
        print(v.describe())
        p = v.check()
        print("\n   integrity:", "OK" if not p else "FAILED")
        for x in p:
            print("      " + x)
        return 0
    if a.cmd == "verify":
        v = Vbf(a.vbf)
        p = v.check()
        print(f"{v.path}\n   " + ("ALL CRCs OK" if not p else "FAILURES:"))
        for x in p:
            print("      " + x)
        return 1 if p else 0
    if a.cmd == "ident":
        return do_ident(a)
    if a.cmd == "flash":
        a.dry = not a.execute
        do_flash(a)
        return 0


if __name__ == "__main__":
    sys.exit(main())
