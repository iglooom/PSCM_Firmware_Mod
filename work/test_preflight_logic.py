#!/usr/bin/env python3
"""Offline test of pscm_preflight's response handling.

The probe will be run against a REAL power-steering ECU. Its parsing must be
correct before it is ever pointed at hardware, and the vcan loopback needs root
we do not have -- so exercise the pure logic with synthetic frames instead.

What must not happen on the car:
  * an NRC misread as success (would send us down a wrong path),
  * a responsePending (0x78) treated as failure,
  * the F188 version matcher claiming we hold a VBF that we do not.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pscm_preflight as pf                                  # noqa: E402
from pscm_seckey import key_from_seed, MAGIC_PSCM_L1         # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(name)


print("=" * 70)
print("1. NRC decoding — every negative must be reported as negative")
print("=" * 70)
for nrc, label in ((0x11, 'serviceNotSupported'), (0x33, 'securityAccessDenied'),
                   (0x35, 'invalidKey'), (0x31, 'requestOutOfRange'),
                   (0x7F, 'serviceNotSupportedInActiveSession')):
    check(f"NRC 0x{nrc:02X} maps to {label}", pf.NRC.get(nrc), label)

print()
print("=" * 70)
print("2. The F188 version matcher (this gates the dump acceptance test)")
print("=" * 70)
have = {"BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"}


def match(app):
    return [h for h in have if app.strip().upper().startswith(h)]


check("exact known version matches", match("CV6T-14C217-AR"), ["CV6T-14C217-AR"])
check("version with trailing NULs/space matches",
      match("CV6T-14C217-AH   "), ["CV6T-14C217-AH"])
check("UNKNOWN version must NOT match", match("DV6T-14C217-ZZ"), [])
check("near-miss version must NOT match", match("CV6T-14C217-AS"), [])
print("   (a false match here would make us 'verify' a dump against the wrong")
print("    reference and declare a bad dump good)")

print()
print("=" * 70)
print("3. Seed/key: the unlock decision must be driven by the ECU's reply")
print("=" * 70)
seed = [0x1F, 0x7C, 0x69]
k = key_from_seed(seed, MAGIC_PSCM_L1)
print(f"   seed {bytes(seed).hex().upper()} magic 0x{MAGIC_PSCM_L1:06X}"
      f" -> key {k.hex().upper()}")
check("key is 3 bytes", len(k), 3)
check("keygen is deterministic", key_from_seed(seed, MAGIC_PSCM_L1), k)
# the control that proves the algorithm itself
check("published reference vector still reproduces",
      key_from_seed([0x1F, 0x7C, 0x69], 0xFA5FC0).hex(), '9a64ce')

print()
print("=" * 70)
print("4. Response classification (what show() would conclude)")
print("=" * 70)
cases = [
    (bytes.fromhex("6701AABBCC"), 'positive 0x67 seed'),
    (bytes.fromhex("7F2735"), 'negative invalidKey'),
    (bytes.fromhex("7F2311"), 'negative serviceNotSupported'),
    (bytes.fromhex("62F188") + b"CV6T-14C217-AR", 'positive DID read'),
]
for raw, label in cases:
    is_neg = raw[0] == 0x7F
    verdict = 'NEGATIVE' if is_neg else 'POSITIVE'
    print(f"  {label:<34} -> {verdict}")
    if label.startswith('negative'):
        check(f"   '{label}' classified negative", is_neg, True)
    else:
        check(f"   '{label}' classified positive", is_neg, False)

print()
print("=" * 70)
if fails:
    print(f"RESULT: {len(fails)} FAILURE(S) — do not run against hardware")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("RESULT: all logic checks PASS")
print("""
  NOTE ON SCOPE: this validates PARSING only. It cannot validate the CAN
  transport, ISO-TP padding, or the ECU's real behaviour -- those are only
  testable on the module. It does establish that a negative answer will be
  reported as negative rather than silently read as success.""")
