#!/usr/bin/env python3
"""HONEST re-test: is the OEM SBL capable of reading memory out?

The previous instrument (work/sbl_read_capability.py) is INVALID and its
conclusion must not be used. Proof that it is invalid: services 0x27
(SecurityAccess) and 0x3E (TesterPresent) are PROVEN to be implemented -- they
appear in the SBL's own subfunction-permission table at file 0x0F58 -- yet the
"word immediate" test scores them 0, exactly like the read services. A test that
reports "absent" for something known present measures nothing.

Reason it fails: DSP56800E compares a service ID using an 8-bit immediate packed
INSIDE the instruction word, not as a separate 16-bit literal. So absence of a
standalone literal says nothing at all.

What we can still measure WITHOUT a disassembler, using the one structure whose
meaning is established: the permission table itself.
"""
import struct

P = ("/home/gl/Projects/ford/PSCM/Research/bins/BV6T-14C220-AA/"
     "BV6T-14C220-AA_blk0_0x0009F000.bin")
d = open(P, 'rb').read()

NAME = {0x10: 'DiagnosticSessionControl', 0x11: 'ECUReset',
        0x27: 'SecurityAccess', 0x3E: 'TesterPresent'}

print("=" * 72)
print("THE ONE ESTABLISHED STRUCTURE: subfunction-permission table @ 0x0F58")
print("=" * 72)
tbl = d[0x0F58:0x0F80]
assert d[0x0F80] == 0xFF, "expected 0xFF terminator"
for i in range(0, len(tbl), 10):
    r = tbl[i:i + 10]
    sid = r[0]
    subs = [(r[j], r[j + 1]) for j in range(2, 10, 2)]
    print(f"  0x{sid:02X} {NAME.get(sid,'?'):<26} "
          + ' '.join(f'sub={s:02X}(sess={f:02X})' for s, f in subs))
print("  0xFF  <- terminator")
print()
print("  This table lists ONLY services that take a subfunction byte.")
print("  0x34/0x36/0x37/0x31 take no subfunction, so their absence here is")
print("  expected and proves nothing either way. Likewise 0x23/0x35 take no")
print("  subfunction -- so this table CANNOT answer the read question.")
print()

print("=" * 72)
print("WHAT IS ACTUALLY ESTABLISHED vs NOT")
print("=" * 72)
print("""
  ESTABLISHED (hard evidence):
    * Container parses and verifies clean; 1 block, 3970 bytes.
    * Loads to byte 0x0009F000 = word P:0x4F800, entry 'call'=0x4F800.
    * 56F8367 Program RAM is exactly P:0x4F800..0x4FFFF (4 KB) [datasheet].
      The SBL therefore fills 1985/2048 words = 96.9% of ALL program RAM.
    * The SBL implements 0x10, 0x11, 0x27, 0x3E with subfunctions (table above).
    * A CRC-16/CCITT nibble table (poly 0x1021) sits at file 0x0F32.

  NOT ESTABLISHED -- REQUIRES A DISASSEMBLER:
    * Whether 0x23 ReadMemoryByAddress or 0x35 RequestUpload is implemented.
      I have NO valid evidence either way. My earlier byte-frequency test was
      invalid (it also 'disproved' 0x27 and 0x3E, which are proven present).

  STRONG CIRCUMSTANTIAL ARGUMENT (not proof):
    * Only ~63 words (126 bytes) of program RAM remain free. Any OEM upload
      path would have to fit in a budget already 97% consumed.
    * Ford SBLs are shipped to write one direction only; the flash tool
      (VBFlasher, the working open-source Ford flasher) implements
      UDSRequestUpload but never calls it in any flow.
""")
