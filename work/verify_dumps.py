#!/usr/bin/env python3
"""Verify the PSCM dumps against the OEM VBFs we hold.

The module runs CV6T-14C217-AR (application) + CV6T-14C218-AX (calibration),
both of which we have. Every VBF block that lies inside a dumped region is an
independent byte-exact control: flash is static, so a correct dump MUST
reproduce it exactly.

This also reports what is in the regions the VBFs do NOT cover -- that is the
genuinely new material (serial numbers, learned/adaptation data, the parts Ford
never ships).
"""
import glob
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from load_and_probe_sbl import parse_vbf                      # noqa: E402

DUMPS = os.path.join(ROOT, "dumps")
VBFS = [("CV6T-14C217-AR.VBF", "application"),
        ("CV6T-14C218-AX.VBF", "calibration"),
        ("CV6T-14C386-AB.vbf", "SIGCFG")]


def latest(pattern):
    f = sorted(glob.glob(os.path.join(DUMPS, pattern)))
    return f[-1] if f else None


regions = {}
for name, pat in (("pflash", "PSCM_pflash_00000000_*.bin"),
                  ("dflash", "PSCM_dflash_04008000_*.bin")):
    p = latest(pat)
    if p:
        base = int(os.path.basename(p).split('_')[2], 16)
        regions[name] = (base, open(p, 'rb').read(), p)

print("=" * 76)
print("DUMPS")
print("=" * 76)
for n, (base, data, p) in regions.items():
    print(f"  {n:<10} 0x{base:08X} +0x{len(data):X} ({len(data)} B)")
    print(f"             {os.path.basename(p)}")
    print(f"             sha256 {hashlib.sha256(data).hexdigest()}")

print()
print("=" * 76)
print("BYTE-EXACT VERIFICATION vs OEM VBF blocks")
print("=" * 76)
covered = {n: bytearray(len(d)) for n, (_, d, _) in regions.items()}
allok = True
nchecked = 0
for fn, kind in VBFS:
    path = os.path.join(ROOT, fn)
    if not os.path.exists(path):
        continue
    _, blks = parse_vbf(path)
    for addr, length, vdata, _ in blks:
        for rname, (base, data, _) in regions.items():
            if addr >= base and addr + length <= base + len(data):
                off = addr - base
                got = data[off:off + length]
                ok = got == vdata
                allok &= ok
                nchecked += 1
                for i in range(off, off + length):
                    covered[rname][i] = 1
                print(f"  {fn:<22} blk @0x{addr:08X} +0x{length:<6X} "
                      f"-> {rname:<8} {'MATCH' if ok else '*** MISMATCH ***'}")
                if not ok:
                    d = [i for i, (x, y) in enumerate(zip(got, vdata)) if x != y]
                    print(f"       {len(d)} differing bytes, first at "
                          f"+0x{d[0]:X} (abs 0x{addr+d[0]:08X})")

print()
print(f"  {nchecked} OEM block(s) checked -> "
      f"{'ALL MATCH — dumps are byte-exact where Ford ships data' if allok else 'MISMATCHES PRESENT'}")

print()
print("=" * 76)
print("COVERAGE — what the VBFs do NOT explain (the new material)")
print("=" * 76)
for rname, (base, data, _) in regions.items():
    cov = covered[rname]
    n_cov = sum(cov)
    print(f"\n  {rname}: {n_cov}/{len(data)} bytes covered by OEM VBFs "
          f"({n_cov/len(data)*100:.1f}%)")
    runs, start = [], None
    for i in range(len(data) + 1):
        c = cov[i] if i < len(data) else 1
        if not c and start is None:
            start = i
        elif c and start is not None:
            runs.append((start, i))
            start = None
    for lo, hi in runs:
        seg = data[lo:hi]
        blank = seg.count(0xFF) / len(seg) * 100
        zero = seg.count(0x00) / len(seg) * 100
        tag = "erased/blank" if blank > 95 else ("zeroed" if zero > 95 else "** DATA **")
        print(f"    0x{base+lo:08X}..0x{base+hi:08X}  {hi-lo:6d} B  "
              f"FF={blank:5.1f}% 00={zero:5.1f}%  {tag}")

print()
print("=" * 76)
print("INTERNAL CHECKSUMS (from PSCM_internal_checksums.md) on the LIVE dump")
print("=" * 76)


def sum16le(d):
    return sum(int.from_bytes(d[i:i + 2], 'little')
               for i in range(0, len(d) // 2 * 2, 2)) & 0xFFFF


if "pflash" in regions:
    base, img, _ = regions["pflash"]
    # 14C218 calibration region must sum to 0xFFFF  (doc §4)
    cal = img[0x9800:0x1C000]
    s = sum16le(cal)
    print(f"  14C218 region sum16le(0x09800..0x1C000) = 0x{s:04X}  "
          f"{'OK (== 0xFFFF as designed)' if s == 0xFFFF else '*** unexpected ***'}")
    # 14C217 blk1 word B = sum16le(linear[0 .. 0x7FFEC))  (doc §6)
    wb = int.from_bytes(img[0x7FFEC:0x7FFEE], 'little')
    calc = sum16le(img[:0x7FFEC])
    print(f"  14C217 blk1 word B @0x7FFEC stored 0x{wb:04X} calc 0x{calc:04X}  "
          f"{'OK' if wb == calc else '*** MISMATCH ***'}")
    wa = int.from_bytes(img[0x7FFEA:0x7FFEC], 'little')
    # 14C217 blk1 word A = CRC-16/MCRF4XX(blk0 ++ blk1[START:0x63FEA)) (doc §6)
    # START is PER BUILD; recover it from the firmware's own self-check code.
    _rp = int('{:016b}'.format(0x1021)[::-1], 2)
    _T = []
    for _i in range(256):
        _c = _i
        for _ in range(8):
            _c = (_c >> 1) ^ _rp if _c & 1 else _c >> 1
        _T.append(_c)

    def _crc(d, init=0xFFFF):
        c = init
        for x in d:
            c = (c >> 8) ^ _T[(c ^ x) & 0xFF]
        return c

    def _find_start(image):
        """The self-check loads #$0003FFF5 (=word A) then the START word addr."""
        END = 0x3FFF5
        n = len(image) // 2
        w = [int.from_bytes(image[i * 2:i * 2 + 2], 'little') for i in range(n)]

        def li(i):
            return None if (w[i] & 0xFFF0) != 0xE410 else (w[i + 2] << 16) | w[i + 1]
        for e in range(0x0E000, min(n - 2, 0x40000)):
            if li(e) != END:
                continue
            for j in range(e, min(e + 40, n - 2)):
                v = li(j)
                if v and v != END and 0x0E000 < v < 0x3FFF5:
                    return v * 2
        return None

    st = _find_start(img)
    if st is None:
        print(f"  14C217 blk1 word A @0x7FFEA stored 0x{wa:04X}  "
              f"(START constant not found - cannot verify)")
    else:
        ca = _crc(img[0x00000:0x09800] + img[st:0x7FFEA])
        print(f"  14C217 blk1 word A @0x7FFEA stored 0x{wa:04X} calc 0x{ca:04X}  "
              f"(START=0x{st:05X})  {'OK' if wa == ca else '*** MISMATCH ***'}")

if "dflash" in regions:
    base, img, _ = regions["dflash"]
    print(f"\n  dflash first 32 B: {img[:32].hex().upper()}")
    print(f"  dflash last  32 B: {img[-32:].hex().upper()}")
