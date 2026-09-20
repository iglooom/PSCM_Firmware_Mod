#!/usr/bin/env python3
"""PSCM SecurityAccess — unified keygen, validated against the BCM implementation.

RESULT OF work/keygen_equivalence.py:
  The BCM's key_from_seed(secret=64000B0C59) and VBFlasher's keygen(fixed=...)
  are THE SAME ALGORITHM. The BCM 5-byte secret maps to the VBFlasher 'fixed'
  as a BIG-ENDIAN 40-bit integer:

        BCM SECRET_L1 = 64 00 0B 0C 59   ==   fixed = 0x64000B0C59

  Verified: both produce byte-identical keys on all test seeds under that
  mapping. (A little-endian mapping does NOT match -- that is the trap.)

CONSEQUENCE FOR THE PSCM:
  VBFlasher lists 0x730 (PSCM) level 0x01 magic = 0x9B2533. As a 40-bit
  big-endian value that is 0x0000_9B2533, i.e. the BCM-style 5-byte secret

        00 00 9B 25 33

  So the PSCM secret is simply the BCM's secret slot with a different value,
  exactly as the user said. Use whichever form is convenient; they agree.

  NOTE: the published 0x730 magic is UNVERIFIED against the real module. It
  came from a third-party table, not from our own capture. Treat the first
  live 0x27 02 as the test: NRC 0x35 (invalidKey) means the constant is wrong
  for this module/level and must be re-derived from a genuine seed/key pair.
"""

MAGIC_PSCM_L1 = 0x9B2533          # VBFlasher form, ECU 0x730 level 0x01
SECRET_PSCM_L1 = bytes.fromhex("00009B2533")   # equivalent BCM 5-byte form


def key_from_seed(seed3, fixed=MAGIC_PSCM_L1):
    """seed3: 3 bytes from the 0x27 0x01 positive response. Returns 3-byte key."""
    cc = [fixed & 0xFF, (fixed >> 8) & 0xFF, (fixed >> 16) & 0xFF,
          (fixed >> 24) & 0xFF, (fixed >> 32) & 0xFF,
          seed3[2], seed3[1], seed3[0]]
    t1 = 0xC541A9
    for _ in range(64):
        bbit = (t1 & 1) ^ (cc[7] & 1)
        t2 = ((t1 >> 1) + bbit * 0x800000) & 0xFFFFFFFF
        t1 = (t2 ^ (0x109028 * bbit)) & 0xFFFFFFFF
        cc[7] = (cc[7] >> 1) & 0xFF
        for a in range(7, 0, -1):
            cc[a] = (cc[a] + (cc[a - 1] & 1) * 128) & 0xFF
            cc[a - 1] >>= 1
    return bytes([t1 >> 4 & 0xFF,
                  ((t1 >> 12 & 0x0F) << 4) + (t1 >> 20 & 0x0F),
                  (t1 >> 16 & 0x0F) + ((t1 & 0x0F) << 4)])


def secret_to_fixed(secret5: bytes) -> int:
    """BCM-style 5-byte secret -> VBFlasher 'fixed'. BIG-endian (validated)."""
    return int.from_bytes(secret5, 'big')


if __name__ == '__main__':
    # Control 1: the VBFlasher published vector must reproduce.
    assert key_from_seed([0x1F, 0x7C, 0x69], 0xFA5FC0).hex() == '9a64ce'
    # Control 2: the BCM secret must round-trip through the mapping.
    assert secret_to_fixed(bytes.fromhex("64000B0C59")) == 0x64000B0C59
    # Control 3: the two PSCM secret forms must agree.
    assert secret_to_fixed(SECRET_PSCM_L1) == MAGIC_PSCM_L1
    print("all controls PASS")
    print()
    print(f"PSCM 0x730 level 0x01  fixed=0x{MAGIC_PSCM_L1:06X}  "
          f"= secret {SECRET_PSCM_L1.hex().upper()}")
    for s in ([0x00, 0x00, 0x00], [0x11, 0x22, 0x33], [0xAB, 0xCD, 0xEF]):
        print(f"  seed {bytes(s).hex().upper()} -> key "
              f"{key_from_seed(s).hex().upper()}")
