#!/usr/bin/env python3
"""Build HV6T-14C217-AC_LCA_HANDSOFF.VBF — combined production image for the
HV6T-14C217-AC (2018) revision: the five-edit LCA enabler PLUS the hands-off
warning disable, in one flash.

Union of:
  A) LCA enabler relocated to HV6T (5 sites) — see build_lca_hv6t_vbf.py
  B) hands-off disable relocated to HV6T (1 site) — the status-frame composer's
     hands-off mirror copy at P:$2BA02.

HV6T-14C217-AC is isomorphic to CV6T-14C217-AR: identical block topology and
checksum algorithms, same lane-assist code, with a fixed RAM-cell delta (+0x5A
on the status cells) and relocated flash addresses. Every site was located
structurally and its stock words re-asserted here. The hands-off cell X:$22AA is
written ONLY at P:$2BA02 and read ONLY by the composer at P:$1DBF5, exactly
mirroring CV6T's X:$2250 -- so the single-word neutralise applies identically.

    #  blk1 off  stock                patched              effect
    1  0x39144   F07C 090A 4C01 A203  E700 E700 E700 E700  LCA state entry gate
    2  0x39A3A   FF7C 2E1B A209       E700 E700 E700       phase-1 entry inhibit
    3  0x3B3A4   A303                 E700                 code-5 availability
    4  0x3A1A0   B104                 B0FA                 code-5 ramp increment
    5  0x3A2A2   B170                 B153                 code-5 authority
    6  0x3B404   F67C 2E04 22AA       E680 22AA E700       force transmitted hands-off = 0

Usage:
    python3 work/lca_resume/build_lca_handsoff_hv6t_vbf.py --selftest
    python3 work/lca_resume/build_lca_handsoff_hv6t_vbf.py
"""
from __future__ import annotations
import argparse, binascii, hashlib, struct, sys
from pathlib import Path
import build_fd22_snapshot_vbf as base

ROOT = Path(__file__).resolve().parents[2]
STOCK = ROOT / "HV6T-14C217-AC.VBF"
CAL = ROOT / "HV6T-14C218-AD.VBF"
OUTPUT = ROOT / "HV6T-14C217-AC_LCA_HANDSOFF.VBF"

OEM_STOCK_SHA = "0915dfde81f2f76d8740e8a1b0cf6497b65b521be431f04843c2380c0f0f9bc6"
OEM_CAL_SHA = "fff77d9eaf25da53df9b42381cb5cbba1d9801b770ba279548c340288c3a4179"
OEM_BLK_SHA = {
    0x00000000: "dac414db1acca9741e973893e509aa6b9d6ee6d20d355e3d1f69223e5783103b",
    0x0001C000: "2a06dcd1cbdfb37a558ac02a14850e9ec3912a3b97b23e8d7ab5de4e48fb7941",
    0x04008C00: "504df54a3b1863e5c84adec10c967db89afd3dfaf8a2155764e1a357219b3575",
}
OEM_WORD_A = 0xB022
OEM_WORD_B = 0xD35E

# (label, blk1 byte offset, stock words, patched words)
PATCHES = (
    ("LCA state entry gate    P:$2A8A2", 0x39144, (0xF07C, 0x090A, 0x4C01, 0xA203), (0xE700,) * 4),
    ("phase-1 entry inhibit   P:$2AD1D", 0x39A3A, (0xFF7C, 0x2E1B, 0xA209), (0xE700,) * 3),
    ("code-5 availability     P:$2B9D2", 0x3B3A4, (0xA303,), (0xE700,)),
    ("code-5 ramp increment   d1 e5",    0x3A1A0, (0xB104,), (0xB0FA,)),
    ("code-5 authority        d2 e5",    0x3A2A2, (0xB170,), (0xB153,)),
    ("force hands-off = 0     P:$2BA02", 0x3B404, (0xF67C, 0x2E04, 0x22AA), (0xE680, 0x22AA, 0xE700)),
)

GUARDS = (
    ("dispatcher ==6 arm", 0x39140,
     (0x4C06, 0xA207, 0xF07C, 0x090A, 0x4C01, 0xA203, 0xE684, 0x2E38)),
    ("phase-1 state test", 0x39A32,
     (0xF07C, 0x2E38, 0x4C04, 0xA20C, 0xFF7C, 0x2E1B, 0xA209)),
    ("availability predicates", 0x3B398,
     (0xF07C, 0x2E13, 0x4C02, 0xA306, 0xE700, 0x4C05, 0xA303, 0xE700, 0x4C03)),
    ("dispatcher-1 table", 0x3A18C,
     (0xB109, 0x0002, 0xB0D2, 0x0002, 0xB0F1, 0x0002,
      0xB0F6, 0x0002, 0xB0FA, 0x0002, 0xB104)),
    ("dispatcher-2 table", 0x3A28E,
     (0xB171, 0x0002, 0xB153, 0x0002, 0xB170, 0x0002,
      0xB171, 0x0002, 0xB153, 0x0002, 0xB170)),
    # status-copy block: availability + deny copies frame the hands-off copy.
    ("status-copy block", 0x3B3F8,
     (0xF67C, 0x2D82, 0x22AC, 0xF67C, 0x2E12, 0x22AB,
      0xF67C, 0x2E04, 0x22AA)),
)

D1_TABLE = 0x3A18C
D2_TABLE = 0x3A28E


def _w(words):
    return struct.pack("<%dH" % len(words), *words)


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def verify_oem(stock, cal):
    if _sha(stock.raw) != OEM_STOCK_SHA:
        raise base.BuildError(f"stock SHA mismatch: {_sha(stock.raw)}")
    if _sha(cal.raw) != OEM_CAL_SHA:
        raise base.BuildError(f"cal SHA mismatch: {_sha(cal.raw)}")
    base.verify_container(stock)
    base.verify_container(cal)
    for addr, want in OEM_BLK_SHA.items():
        if _sha(stock.block_at(addr).data) != want:
            raise base.BuildError(f"stock block 0x{addr:08X} SHA mismatch")
    if [b.address for b in stock.blocks] != [0, base.BLK1_FLASH, 0x04008C00]:
        raise base.BuildError("unexpected stock block map")
    if len(cal.blocks) != 1 or cal.blocks[0].address != 0x00009800:
        raise base.BuildError("unexpected calibration block map")
    if base.sum16le(cal.blocks[0].data) != 0xFFFF:
        raise base.BuildError("calibration self-sum != 0xFFFF")
    b0 = stock.block_at(base.BLK0_FLASH).data
    b1 = stock.block_at(base.BLK1_FLASH).data
    if base.find_start_offset(b1) != 0x1800:
        raise base.BuildError("START immediate is not 0x1800")
    if base.calculate_word_a(b0, b1) != OEM_WORD_A:
        raise base.BuildError("word-A algorithm does not reproduce stock")
    if base.calculate_word_b(stock, cal, b1) != OEM_WORD_B:
        raise base.BuildError("word-B algorithm does not reproduce stock")


def build_bytes(stock, cal):
    verify_oem(stock, cal)
    block0 = stock.block_at(base.BLK0_FLASH).data
    record = stock.block_at(base.BLK1_FLASH)
    block1 = bytearray(record.data)

    for label, off, old, _new in PATCHES:
        base.expect_bytes(block1, off, _w(old), label)
    for label, off, words in GUARDS:
        base.expect_bytes(block1, off, _w(words), f"context: {label}")
    base.expect_bytes(block1, base.WORD_A_OFF, _w((OEM_WORD_A,)), "word A")
    base.expect_bytes(block1, base.WORD_B_OFF, _w((OEM_WORD_B,)), "word B")

    for _label, off, _old, new in PATCHES:
        block1[off:off + len(new) * 2] = _w(new)

    old_a = base._u16le(block1, base.WORD_A_OFF)
    new_a = base.calculate_word_a(block0, bytes(block1))
    struct.pack_into("<H", block1, base.WORD_A_OFF, new_a)
    old_b = base._u16le(block1, base.WORD_B_OFF)
    new_b = base.calculate_word_b(stock, cal, bytes(block1))
    struct.pack_into("<H", block1, base.WORD_B_OFF, new_b)

    out = bytearray(stock.raw)
    out[record.data_offset:record.crc_offset] = block1
    crc = binascii.crc_hqx(block1, 0xFFFF)
    struct.pack_into(">H", out, record.crc_offset, crc)
    new_file = binascii.crc32(out[stock.data_start:]) & 0xFFFFFFFF
    width = stock.checksum_span[1] - stock.checksum_span[0]
    out[slice(*stock.checksum_span)] = f"{new_file:0{width}X}".encode()

    return base.BuildResult(bytes(out), old_a, new_a, old_b, new_b,
                            record.stored_crc, crc,
                            int(stock.raw[slice(*stock.checksum_span)], 16), new_file)


def audit(stock, built, cal):
    base.verify_container(built)
    if built.data_start != stock.data_start:
        raise base.BuildError("data_start changed")
    if [(b.address, len(b.data), b.data_offset, b.crc_offset) for b in built.blocks] != \
       [(b.address, len(b.data), b.data_offset, b.crc_offset) for b in stock.blocks]:
        raise base.BuildError("block topology changed")

    old_blk = stock.block_at(base.BLK1_FLASH).data
    new = built.block_at(base.BLK1_FLASH).data

    for label, off, _o, patched in PATCHES:
        base.expect_bytes(new, off, _w(patched), f"built: {label}")

    # availability + deny copies neighbouring the hands-off copy must survive
    base.expect_bytes(new, 0x3B3F8, _w((0xF67C, 0x2D82, 0x22AC)),
                      "built: availability copy must survive")
    base.expect_bytes(new, 0x3B3FE, _w((0xF67C, 0x2E12, 0x22AB)),
                      "built: deny copy must survive")

    # only entry 5 may move in either table; entries 0..4 + strides frozen
    for table in (D1_TABLE, D2_TABLE):
        for code in range(5):
            o = table + 4 * code
            if base._u16le(new, o) != base._u16le(old_blk, o):
                raise base.BuildError(f"table 0x{table:X} entry {code} changed")
        for sep in range(1, 11, 2):
            o = table + 2 * sep
            if base._u16le(new, o) != base._u16le(old_blk, o):
                raise base.BuildError(f"table 0x{table:X} separator changed")

    allowed = set()
    expected = 0
    for _label, off, old, patched in PATCHES:
        allowed |= set(range(off, off + len(patched) * 2))
        expected += sum(1 for a, b in zip(_w(old), _w(patched)) if a != b)
    allowed |= {base.WORD_A_OFF, base.WORD_A_OFF + 1,
                base.WORD_B_OFF, base.WORD_B_OFF + 1}
    expected += 4
    changed = {i for i, (a, b) in enumerate(zip(old_blk, new)) if a != b}
    if changed - allowed:
        raise base.BuildError(f"{len(changed - allowed)} unexpected payload changes")
    if len(changed) != expected:
        raise base.BuildError(f"{len(changed)} bytes changed, expected {expected}")

    if base._u16le(new, base.WORD_A_OFF) != base.calculate_word_a(
            built.block_at(base.BLK0_FLASH).data, new):
        raise base.BuildError("checksum A invalid")
    if base._u16le(new, base.WORD_B_OFF) != base.calculate_word_b(built, cal, new):
        raise base.BuildError("checksum B invalid")
    if any(a.data != b.data or a.stored_crc != b.stored_crc
           for a, b in zip(stock.blocks, built.blocks) if a.address != base.BLK1_FLASH):
        raise base.BuildError("untouched block changed")
    hdr = {i for i in range(stock.data_start) if stock.raw[i] != built.raw[i]}
    if hdr - set(range(*stock.checksum_span)):
        raise base.BuildError("header changed outside file checksum")
    return len(changed)


def _expected_changed():
    n = 0
    for _label, _off, old, patched in PATCHES:
        n += sum(1 for a, b in zip(_w(old), _w(patched)) if a != b)
    return n + 4


def selftest():
    try:
        stock = base.parse_vbf(STOCK)
        cal = base.parse_vbf(CAL)
        first = build_bytes(stock, cal)
        if first.output != build_bytes(stock, cal).output:
            raise base.BuildError("nondeterministic output")
        parsed = base.parse_vbf_bytes(first.output, Path("lca-handsoff-hv6t-selftest.vbf"))
        n = audit(stock, parsed, cal)
        exp = _expected_changed()
        if n != exp:
            raise base.BuildError(f"expected {exp} changed bytes, got {n}")
        print(f"SELFTEST: ALL PASS ({n} bytes changed = "
              f"{exp - 4} patch + 4 checksum; 6 sites)")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"SELFTEST FAILURE: {exc}", file=sys.stderr)
        return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--output", type=Path, default=OUTPUT)
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    try:
        stock = base.parse_vbf(STOCK)
        cal = base.parse_vbf(CAL)
        result = build_bytes(stock, cal)
        parsed = base.parse_vbf_bytes(result.output, args.output)
        n = audit(stock, parsed, cal)
        base.write_atomic(args.output, result.output)
        readback = base.parse_vbf(args.output)
        audit(stock, readback, cal)
        if readback.raw != result.output:
            raise base.BuildError("read-back mismatch")
        print(f"wrote: {args.output}")
        print(f"sha256: {base.sha256(result.output)}")
        print(f"\nPATCHES APPLIED (6 sites): {n} bytes differ from stock")
        for label, off, old, new in PATCHES:
            o = " ".join(f"{x:04X}" for x in old)
            nn = " ".join(f"{x:04X}" for x in new)
            print(f"  0x{off:05X}  {label}\n            {o}  ->  {nn}")
        print("\nTELEMETRY: none")
        print(f"word A: {OEM_WORD_A:04X} -> {result.new_word_a:04X}")
        print(f"word B: {OEM_WORD_B:04X} -> {result.new_word_b:04X}")
        print(f"block CRC-16: {result.new_block_crc:04X}   file CRC-32: {result.new_file_checksum:08X}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
