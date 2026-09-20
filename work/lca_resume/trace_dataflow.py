#!/usr/bin/env python3
"""Reproduce the LCA-code -> torque-accumulator static trace.

Read-only: loads extracted 14C217 .bin blocks and prints raw P-space words.
It validates the two per-state jump tables, the wrapper scaling, the final
X-accumulator store, and direct references to each build's code-5 source.
"""
from __future__ import annotations

import argparse
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

SPECS = {
    "CV6T-AR": {
        "bin": "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin",
        "code": 0x2DB9, "state": 0x2DDE, "acc": 0x2D53,
        "dispatch1": 0x2AF90, "table1": 0x2AFA4,
        "case2_1": 0x2AFCF, "case5_1": 0x2AFE2,
        "target": 0x2D46, "source5": 0x2D50, "source5_init": 0x2B513,
        "wrapper": 0x2B054, "shift_addr": 0x2B065, "shift": 0x4C6A, "work": 0x2D49,
        "dispatch2": 0x2B00B, "table2": 0x2B025, "case25_2": 0x2B04E,
        "torque": 0x2B07C, "enable": 0x2B0B4, "store": 0x2B0B2,
        "reset": 0x2AEB7,
        "state_sign": 0x2B0E8, "sign_cell": 0x2D5E, "signed_value": 0x2D5F,
    },
    "CV6T-AH": {
        "bin": "bins/CV6T-14C217-AH/CV6T-14C217-AH_blk1_0x0001C000.bin",
        "code": 0x28B1, "acc": 0x2894,
        "dispatch1": 0x294CB, "table1": 0x294DD,
        "case2_1": 0x294F3, "case5_1": 0x29506,
        "target": 0x2887, "source5": 0x2891, "source5_init": 0x2963D,
        "wrapper": 0x2955E, "shift_addr": 0x2956F, "shift": 0x4C68, "work": 0x288A,
        "dispatch2": 0x2952F, "table2": 0x29549, "case25_2": 0x29558,
        "torque": 0x29586, "enable": 0x295BE, "store": 0x295BC,
        "reset": 0x29401,
    },
    "BV6T-AF": {
        "bin": "bins/BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin",
        "code": 0x23BF, "state": 0x23E4, "acc": 0x23AA,
        "dispatch1": 0x273BE, "table1": 0x273D0,
        "case2_1": 0x273E6, "case5_1": 0x273F9,
        "target": 0x21ED, "source5": 0x23A7, "source5_init": 0x277CE,
        "source5_writer": 0x273BA, "producer": 0x27346,
        "wrapper": 0x27460, "shift_addr": 0x2746F, "shift": 0x4C68, "work": 0x21F0,
        "dispatch2": 0x27422, "table2": 0x2743E, "case25_2": 0x2744D,
        "torque": 0x27476, "enable": 0x274AE, "store": 0x274AC,
        "reset": None,
    },
}

READ1 = {0xF07C, 0xF17C, 0xF27C, 0xF47C, 0xF57C, 0xF77C, 0xF87C, 0xFF7C}
WRITE1 = {0xD07C, 0xD17C, 0xD47C, 0xD57C}


def load(spec):
    data = (ROOT / spec["bin"]).read_bytes()
    assert len(data) % 2 == 0
    return struct.unpack("<%dH" % (len(data) // 2), data)


def word(words, addr):
    i = addr - 0xE000  # blk1 byte load 0x1C000 -> P-word base 0xE000
    if not 0 <= i < len(words):
        raise IndexError(hex(addr))
    return words[i]


def raw(words, addr, count):
    return " ".join(f"{word(words, addr+i):04X}" for i in range(count))


def ptrs(words, table):
    # Compiler emits six 32-bit P pointers as low-word then high-word (=0002).
    return [word(words, table + 2*i) | (word(words, table + 2*i + 1) << 16)
            for i in range(6)]


def refs(words, cell):
    """Opcode-filtered direct refs; return (instruction P addr, kind, raw)."""
    out = []
    for i, value in enumerate(words):
        if value != cell:
            continue
        a = 0xE000 + i
        prev = words[i-1] if i else -1
        prev2 = words[i-2] if i > 1 else -1
        if prev in READ1:
            out.append((a-1, "read", f"{prev:04X} {cell:04X}"))
        elif prev in WRITE1 or (prev & 0xFF80) == 0xE680:
            out.append((a-1, "write", f"{prev:04X} {cell:04X}"))
        elif prev == 0x8654:  # encoding observed as opcode,destination,immediate
            out.append((a-1, "write-imm", raw(words, a-1, 3)))
        elif prev2 == 0xF67C:
            out.append((a-2, "copy-dest", raw(words, a-2, 3)))
        elif prev == 0xF67C:
            out.append((a-1, "copy-source", raw(words, a-1, 3)))
    return out


def state_sign_selectors(words):
    """Find state==1 -> -1, state==2 -> +1, else -> 0 selectors."""
    out = []
    for i in range(len(words) - 14):
        if (words[i] == 0xF07C and
                words[i+2:i+5] == (0x4C01, 0xA203, 0xE6FF) and
                words[i+6:i+10] == (0xA907, 0x4C02, 0xA203, 0xE681) and
                words[i+11:i+13] == (0xA902, 0xE680) and
                words[i+5] == words[i+10] == words[i+13]):
            out.append((0xE000 + i, words[i+1], words[i+5]))
    return out


def check_build(label, s, w):
    p1 = ptrs(w, s["table1"])
    p2 = ptrs(w, s["table2"])
    assert p1[2] == s["case2_1"] and p1[5] == s["case5_1"]
    assert p2[2] == p2[5] == s["case25_2"]
    assert raw(w, s["case2_1"], 3) == f"8654 {s['target']:04X} " + (
        "0400" if label == "CV6T-AR" else "0100")
    assert raw(w, s["case5_1"], 3) == f"F67C {s['source5']:04X} {s['target']:04X}"
    assert word(w, s["shift_addr"]) == s["shift"]
    assert raw(w, s["store"], 2) == f"D07C {s['acc']:04X}"
    assert raw(w, s["enable"], 7) == f"F07C {s['code']:04X} 4C02 A303 E700 4C05 A203"

    print(f"\n== {label} ==")
    print(f"dispatch1 P:${s['dispatch1']:05X}; table P:${s['table1']:05X}")
    print(" table1 cases 0..5:", " ".join(f"P:${x:05X}" for x in p1))
    print(f" code2 P:${s['case2_1']:05X}: {raw(w, s['case2_1'], 5)}")
    print(f" code5 P:${s['case5_1']:05X}: {raw(w, s['case5_1'], 5)}")
    print(f" source5 X:${s['source5']:04X} direct refs:")
    for a, kind, r in refs(w, s["source5"]):
        print(f"  P:${a:05X} {kind:11s} {r}")
    print(f"wrapper P:${s['wrapper']:05X}: {raw(w, s['wrapper'], 39)}")
    print(f"dispatch2 P:${s['dispatch2']:05X}; table P:${s['table2']:05X}")
    print(" table2 cases 0..5:", " ".join(f"P:${x:05X}" for x in p2))
    print(f" shared code2/code5 target P:${s['case25_2']:05X}: "
          f"{raw(w, s['case25_2'], 8)}")
    print(f"torque P:${s['torque']:05X}; store P:${s['store']:05X}: "
          f"{raw(w, s['store'], 2)}")
    print(f"enable P:${s['enable']:05X}: {raw(w, s['enable'], 11)}")
    selectors = state_sign_selectors(w)
    print("state sign selectors:", " ".join(
        f"P:${a:05X}(X:${state:04X}->X:${dest:04X})"
        for a, state, dest in selectors) or "none")
    if "state_sign" in s:
        assert selectors == [(s["state_sign"], s["state"], s["sign_cell"])]
        assert raw(w, 0x2B107, 6) == "F07C 2D5E 6C11 8016 D07C 2D5F"
        print(" AR-only chain anchors:")
        for a, n in ((0x2B107, 6), (0x2B110, 9), (0x2B305, 8),
                     (0x2B366, 3), (0x2AE0F, 12), (0x2AE45, 3),
                     (0x2AEC2, 10), (0x2AED1, 3), (0x2AF2D, 8),
                     (0x2AF8B, 3)):
            print(f"  P:${a:05X}: {raw(w, a, n)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="run assertions (assertions also run in normal output mode)")
    args = ap.parse_args()
    images = {name: load(spec) for name, spec in SPECS.items()}
    for name, spec in SPECS.items():
        check_build(name, spec, images[name])

    # Same-family control: both CV builds have only one direct write to code-5
    # source and it is the constant initializer; BV has a runtime D07C writer.
    ar = refs(images["CV6T-AR"], SPECS["CV6T-AR"]["source5"])
    ah = refs(images["CV6T-AH"], SPECS["CV6T-AH"]["source5"])
    bv = refs(images["BV6T-AF"], SPECS["BV6T-AF"]["source5"])
    assert [k for _, k, _ in ar if k.startswith("write")] == ["write-imm"]
    assert [k for _, k, _ in ah if k.startswith("write")] == ["write-imm"]
    assert any(a == SPECS["BV6T-AF"]["source5_writer"] and k == "write"
               for a, k, _ in bv)
    assert state_sign_selectors(images["CV6T-AH"]) == []
    assert state_sign_selectors(images["BV6T-AF"]) == []
    if args.selftest:
        print("\nSELFTEST: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
