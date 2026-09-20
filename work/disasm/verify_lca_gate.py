#!/usr/bin/env python3
"""INDEPENDENT verification of the claimed LCA calibration gate.

A subagent reported that the CV6T PSCM gates the LkaActvStats_D_Req == 6 (LCA)
arm on a calibration cell, while BV6T does not. That claim, if acted on, leads
to patching a STEERING CONTROLLER. It must be reproduced from the raw binaries
by a separate implementation before it is believed.

Claimed raw words:
  CV6T-AR @P:$2A77E  4C06 A207 F07C 0904 4C01 A203 E684 2DDE A902 E680 2DDE E708
  CV6T-AH @P:$28CF7  4C06 A207 F07C 076A 4C01 A203 E684 28D6 A902 E680 28D6 E708
  BV6T-AF @P:$2691D  4C06 A203 E684 23E4 A902 E680 23E4 E708

P: addresses are WORD addresses on DSP56800E. Program flash blk0 loads at
byte 0x00000000 and blk1 at byte 0x0001C000, so word address W lives at byte
2*W, i.e. blk1 covers word addresses 0xE000 upward.

Usage:  python3 verify_lca_gate.py
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BINS = os.path.join(HERE, "..", "..", "bins")

BUILDS = {
    "CV6T-14C217-AR": 0x2A77E,
    "CV6T-14C217-AH": 0x28CF7,
    "BV6T-14C217-AF": 0x2691D,
}
EXPECT = {
    "CV6T-14C217-AR": "4C06 A207 F07C 0904 4C01 A203 E684 2DDE A902 E680 2DDE E708",
    "CV6T-14C217-AH": "4C06 A207 F07C 076A 4C01 A203 E684 28D6 A902 E680 28D6 E708",
    "BV6T-14C217-AF": "4C06 A203 E684 23E4 A902 E680 23E4 E708",
}

ok = True


def chk(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def load_blocks(build):
    """-> [(word_base, data)] for each program-flash block."""
    d = os.path.join(BINS, build)
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".bin"):
            continue
        # name carries the byte load address
        m = fn.rsplit("_0x", 1)
        if len(m) != 2:
            continue
        byte_addr = int(m[1][:8], 16)
        if byte_addr >= 0x04000000:        # X-space / data flash, not P:
            continue
        out.append((byte_addr // 2, open(os.path.join(d, fn), "rb").read()))
    return out


def words_at(blocks, waddr, n):
    for base, data in blocks:
        off = (waddr - base) * 2
        if 0 <= off and off + 2 * n <= len(data):
            return [struct.unpack_from("<H", data, off + 2 * i)[0]
                    for i in range(n)]
    return None


def main():
    print("INDEPENDENT VERIFICATION OF THE CLAIMED LCA GATE\n")
    got = {}
    for build, addr in BUILDS.items():
        blocks = load_blocks(build)
        n = len(EXPECT[build].split())
        w = words_at(blocks, addr, n)
        if w is None:
            chk(f"{build}: address P:${addr:05X} readable", False)
            continue
        s = " ".join(f"{x:04X}" for x in w)
        got[build] = w
        chk(f"{build} @P:${addr:05X} matches the reported bytes",
            s == EXPECT[build], f"\n          got      {s}\n          expected {EXPECT[build]}")

    print("\n-- structural reading of the words --")
    if "CV6T-14C217-AR" in got and "BV6T-14C217-AF" in got:
        ar, bv = got["CV6T-14C217-AR"], got["BV6T-14C217-AF"]
        chk("both arms start with CMP.W #6 (0x4C06)",
            ar[0] == 0x4C06 and bv[0] == 0x4C06)
        chk("CV6T is exactly 4 words longer than BV6T",
            len(ar) - len(bv) == 4, f"{len(ar)} vs {len(bv)}")
        chk("CV6T Bne displacement is A207, BV6T is A203",
            ar[1] == 0xA207 and bv[1] == 0xA203,
            f"{ar[1]:04X} / {bv[1]:04X}")
        chk("the 4 inserted words are a load + compare-to-1 + branch",
            ar[2] == 0xF07C and ar[4] == 0x4C01 and ar[5] == 0xA203,
            f"{ar[2]:04X} .. {ar[4]:04X} {ar[5]:04X}")
        chk("CV6T gate cell operand is X:$0904", ar[3] == 0x0904,
            f"{ar[3]:04X}")
        chk("both set state 4 (E684) then branch, else state 0 (E680)",
            0xE684 in ar and 0xE680 in ar and 0xE684 in bv and 0xE680 in bv)
        # displacement arithmetic: A2xx is Bne with a displacement
        chk("BV6T A203 skips 3 words to the store-0",
            bv[1] & 0xFF == 0x03)
        chk("CV6T A207 skips 7 words (3 + the 4 inserted)",
            ar[1] & 0xFF == 0x07)

    print("\n-- the 2/4 arms must be UNGATED in all three --")
    for build, addr in BUILDS.items():
        blocks = load_blocks(build)
        w = words_at(blocks, addr - 12, 12)
        if w is None:
            continue
        s = " ".join(f"{x:04X}" for x in w)
        # Layout is: [F07C <enum cell>] then the ==2 arm then the ==4 arm.
        # The leading F07C is the DISPATCHER'S ENTRY LOAD of the enum itself,
        # not a gate -- an earlier version of this check wrongly flagged it.
        # An ungated arm is CMP / Bne+3 / MOVE #k / BRA = 4 words with no
        # extra load, so after dropping the entry load there must be no F07C.
        body = w[2:]
        chk(f"{build}: ==2 arm is CMP.W #2 / Bne+3 / store / BRA",
            body[0] == 0x4C02 and body[1] == 0xA203 and body[2] == 0xE681,
            " ".join(f"{x:04X}" for x in body[:4]))
        chk(f"{build}: ==4 arm is CMP.W #4 / Bne+3 / store / BRA",
            body[5] == 0x4C04 and body[6] == 0xA203 and body[7] == 0xE682,
            " ".join(f"{x:04X}" for x in body[5:9]))
        chk(f"{build}: NO calibration load inside the 2/4 arms",
            0xF07C not in body, s)

    print("\n-- is X:$0904 really read-only in CV6T-AR? --")
    blocks = load_blocks("CV6T-14C217-AR")
    reads = writes = 0
    sites = []
    for base, data in blocks:
        nw = len(data) // 2
        for i in range(nw - 1):
            op = struct.unpack_from("<H", data, 2 * i)[0]
            operand = struct.unpack_from("<H", data, 2 * i + 2)[0]
            if operand != 0x0904:
                continue
            # F07C = MOVE.W X:aa,A (read).  E68x/D5xx style = write forms.
            if op == 0xF07C:
                reads += 1
                sites.append(("read", base + i))
            elif (op & 0xFF00) == 0xE600 or (op & 0xF000) == 0xD000:
                writes += 1
                sites.append(("write", base + i))
    chk("exactly one read of X:$0904", reads == 1, f"{reads} read(s)")
    chk("no writes to X:$0904", writes == 0, f"{writes} write(s)")
    for kind, a in sites:
        print(f"         {kind} at P:${a:05X}")

    print("\n" + "=" * 62)
    print("VERIFICATION:", "CONFIRMED" if ok else "FAILED TO REPRODUCE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
