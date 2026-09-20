#!/usr/bin/env python3
"""Regression test: blockSequenceCounter must wrap 0xFF -> 0x00 (ISO 14229-1).

THE BUG THIS EXISTS TO PREVENT
------------------------------
The first full dump died at exactly +0x7F80 = 32640 bytes = 255 blocks of 128,
with NRC 73 wrongBlockSequenceCounter. Cause: upload() had

    bc = (bc + 1) & 0xFF
    if bc == 0: bc = 1          # <-- WRONG, invented constraint

ISO 14229-1 defines blockSequenceCounter as starting at 0x01 and wrapping
0xFF -> 0x00 -> 0x01. Skipping 0x00 desynchronises the ECU on block 256.

The 0x200-byte acceptance gate could never have caught this: it is only 4
blocks long. This test drives a FAKE ECU past the wrap point so the failure is
reproducible offline, with no hardware and no risk.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []


class FakeSBL:
    """Minimal ECU that enforces the ISO counter rule and serves known data."""

    def __init__(self, length, payload=128):
        self.payload = payload
        self.length = length
        self.data = bytes((i * 7 + 13) & 0xFF for i in range(length))
        self.sent = 0
        self.expect = 1
        self.active = False
        self.last = None
        self.exit_seen = 0

    def send(self, raw):
        sid = raw[0]
        if sid == 0x35:
            self.active, self.sent, self.expect = True, 0, 1
            self.last = bytes([0x75, 0x20, 0x00, self.payload + 2])
        elif sid == 0x36:
            if not self.active:
                self.last = bytes([0x7F, 0x36, 0x22])       # conditionsNotCorrect
                return
            bc = raw[1]
            if bc != self.expect:
                self.last = bytes([0x7F, 0x36, 0x73])       # wrongBlockSequence
                return
            piece = self.data[self.sent:self.sent + self.payload]
            self.sent += len(piece)
            self.expect = (self.expect + 1) & 0xFF          # correct wrap
            self.last = bytes([0x76, bc]) + piece
        elif sid == 0x37:
            self.exit_seen += 1
            self.active = False
            self.last = bytes([0x77])
        elif sid == 0x3E:
            self.last = bytes([0x7E, 0x00])
        else:
            self.last = bytes([0x7F, sid, 0x11])

    def recv(self, n=4096):
        return self.last

    def settimeout(self, t):
        pass


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"         {detail}")
        fails.append(name)


import pscm_dump as pd                                        # noqa: E402

# Route the module's req() at our fake ECU.
pd.req = lambda s, hexstr, timeout=3.0, pend_budget=8.0: (
    s.send(bytes.fromhex(hexstr)) or s.recv())

print("=" * 70)
print("1. Read PAST the 0xFF->0x00 wrap (this is what failed on the car)")
print("=" * 70)
N = 0x10000                       # 64 KiB = 512 blocks, crosses the wrap twice
ecu = FakeSBL(N)
try:
    got = pd.upload(ecu, 0x1C000, N)
    check(f"read {N} bytes ({N//128} blocks) across the wrap", got == ecu.data,
          f"got {len(got)} bytes, mismatch")
except Exception as e:
    check(f"read {N} bytes across the wrap", False, f"raised {e}")

print()
print("=" * 70)
print("2. The OLD buggy counter must be REJECTED by the same fake ECU")
print("=" * 70)
print("   (proves the fake ECU really enforces the rule, i.e. test 1 is real)")


def buggy_upload(s, addr, length):
    s.send(bytes.fromhex("350044" + f"{addr:08X}" + f"{length:08X}"))
    r = s.recv()
    payload = ((r[2] << 8) | r[3]) - 2
    out, bc = bytearray(), 1
    while len(out) < length:
        s.send(bytes([0x36, bc & 0xFF]))
        rr = s.recv()
        if rr[0] != 0x76:
            raise RuntimeError(f"NRC {rr[2]:02X} at +0x{len(out):X}")
        out += rr[2:]
        bc = (bc + 1) & 0xFF
        if bc == 0:
            bc = 1                                   # the original bug
    return bytes(out)


ecu2 = FakeSBL(N)
try:
    buggy_upload(ecu2, 0x1C000, N)
    check("old counter logic is rejected", False,
          "it SUCCEEDED -- the fake ECU is too lenient, test 1 proves nothing")
except RuntimeError as e:
    at = str(e)
    check("old counter logic is rejected", "73" in at, at)
    print(f"         reproduced: {at}")
    expect_at = 255 * 128
    check(f"fails at exactly +0x{expect_at:X} (matches the car)",
          f"+0x{expect_at:X}" in at, at)

print()
print("=" * 70)
print("3. A failed transfer must be released with 37 (else NRC 22 next time)")
print("=" * 70)
ecu3 = FakeSBL(256, payload=128)
try:
    pd.upload(ecu3, 0, 4096)          # asks for more than the ECU has
except Exception:
    pass
check("RequestTransferExit sent after failure", ecu3.exit_seen >= 1,
      f"exit_seen={ecu3.exit_seen}")
check("ECU left in a non-active state", not ecu3.active)
ecu3.send(bytes.fromhex("350044000000000000000100"))
check("a subsequent RequestUpload is accepted", ecu3.recv()[0] == 0x75,
      f"got {ecu3.recv().hex()}")

print()
print("=" * 70)
print("4. Segmented reads reassemble byte-exactly")
print("=" * 70)
ecu4 = FakeSBL(0x8000)
try:
    got = pd.upload_segmented(ecu4, 0, 0x8000, seg=0x1000)
    check("segmented == contiguous", got == ecu4.data,
          f"len {len(got)}")
except Exception as e:
    check("segmented read", False, f"raised {e}")

print()
print("=" * 70)
if fails:
    print(f"RESULT: {len(fails)} FAILURE(S) -- do not run against hardware")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("RESULT: all PASS")
print("""
  SCOPE: this exercises the counter/state machine against a fake ECU. It cannot
  validate CAN transport or the real SBL's behaviour. It does prove the exact
  defect that broke the first dump is now fixed and cannot silently return.""")
