#!/usr/bin/env python3
"""Read-only RAM ownership evidence scan for three PSCM application builds.

The scanner never writes firmware.  It compares X-init occupancy, scans decoded
absolute X references, raw immediate pointer-load forms, nearby indexed
accesses/displacements, pointer-valued initialized words, and stack evidence.
This is conservative evidence collection, not a proof that arbitrary indirect
runtime pointers cannot exist.
"""
from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "work/disasm/out"
XBASE = 0x4600
TARGET = (0x6600, 0x6D10)
# Small Stage-1-only window: beyond the AR-only byte state through X:$6564,
# but below pointer-valued constants beginning at $662E.
FALLBACK = (0x6600, 0x6620)
BUILDS = {
    "CV6T-AR": "CV6T-14C217-AR",
    "CV6T-AH": "CV6T-14C217-AH",
    "BV6T-AF": "BV6T-14C217-AF",
}
LSTS = {k: OUT / f"{k}.lst" for k in BUILDS}
# Verified compiler immediate forms.  8748/4A/4C/4E are R0..R3 despite
# ambiguous base-disassembler register names; 8750/52/54/56/58/5A are also
# address-register forms.  8751 is SP.
PTR_OPS = {0x8748: "R0", 0x874A: "R1", 0x874C: "R2", 0x874E: "R3",
           0x8750: "R0", 0x8752: "R1", 0x8754: "R2", 0x8756: "R3",
           0x8758: "R4", 0x875A: "R5"}
SP_OP = 0x8751
LINE = re.compile(r"P:\$([0-9A-F]+)\s+((?:[0-9A-F]{4}(?:\s+|$))+?)\s{2,}(.*)$")
ABS_X = re.compile(r"X:\$([0-9A-F]+)")
DISP = re.compile(r"X:\([^)]*\+\$([0-9A-F]+)\)")
INDIRECT = re.compile(r"X:\((?:R[0-7]|Rn|RRR|SP)[^)]*\)")

@dataclass
class Insn:
    p: int
    words: tuple[int, ...]
    text: str


def words(path: Path) -> tuple[int, ...]:
    b = path.read_bytes()
    return struct.unpack(f"<{len(b)//2}H", b)


def blocks(part: str):
    d = ROOT / "bins" / part
    for n, base in ((0, 0), (1, 0x1C000)):
        p = next(d.glob(f"*_blk{n}_*.bin"))
        yield p, base // 2, words(p)


def xinit(part: str):
    p = next((ROOT / "bins" / part).glob("*_blk2_*.bin"))
    return p, words(p)


def listing(path: Path) -> list[Insn]:
    out = []
    for s in path.read_text(errors="replace").splitlines():
        m = LINE.search(s)
        if not m:
            continue
        raw = tuple(int(x, 16) for x in m.group(2).split())
        out.append(Insn(int(m.group(1), 16), raw, m.group(3)))
    return out


def overlap(v: int, disp: int, rg: tuple[int, int]) -> bool:
    return rg[0] <= ((v + disp) & 0xFFFF) < rg[1]


def analyze(name: str, part: str):
    xp, xw = xinit(part)
    ins = listing(LSTS[name])
    # The final seven words are an X-init descriptor/footer, not application
    # object occupancy.  Report the last populated application word below it.
    last_non_ff = max((XBASE + i for i, v in enumerate(xw[:0x7FF9-XBASE]) if v != 0xFFFF), default=None)
    direct = []
    for i in ins:
        for m in ABS_X.finditer(i.text):
            a = int(m.group(1), 16)
            if TARGET[0] <= a < TARGET[1] or FALLBACK[0] <= a < FALLBACK[1]:
                direct.append((i.p, a, i.text))
    ptr = []
    sp = []
    literal_target = []
    for bp, base, w in blocks(part):
        for n, op in enumerate(w[:-1]):
            v = w[n + 1]
            p = base + n
            if op in PTR_OPS:
                if 0x6000 <= v < 0x8000:
                    ptr.append((p, PTR_OPS[op], v))
            elif op == SP_OP:
                sp.append((p, v))
            if TARGET[0] <= v < TARGET[1]:
                literal_target.append((p, op, v))
    # Range-aware local over-approximation: pair each pointer immediate with
    # explicit indexed displacement in the following 24 decoded instructions.
    local_reach = []
    for j, i in enumerate(ins):
        if len(i.words) >= 2 and i.words[0] in PTR_OPS:
            v = i.words[1]
            for q in ins[j + 1:j + 25]:
                if q.p - i.p > 48 or q.text.startswith(("RTS", "RTI")):
                    break
                for m in DISP.finditer(q.text):
                    d = int(m.group(1), 16)
                    for rg, tag in ((TARGET, "target"), (FALLBACK, "fallback")):
                        if overlap(v, d, rg):
                            local_reach.append((i.p, v, q.p, d, tag, q.text))
    init_ptrs = [(XBASE + i, v) for i, v in enumerate(xw)
                 if (TARGET[0] <= v < TARGET[1] or FALLBACK[0] <= v < FALLBACK[1])]
    def erased(rg):
        vals = xw[rg[0]-XBASE:rg[1]-XBASE]
        return len(vals), all(v == 0xFFFF for v in vals)
    # Occupied islands around the candidate: non-FFFF runs from 0x6000 onward.
    runs = []
    start = None
    for a in range(0x6000, 0x7000):
        occ = xw[a-XBASE] != 0xFFFF
        if occ and start is None: start = a
        if not occ and start is not None:
            runs.append((start, a-1)); start = None
    if start is not None: runs.append((start, 0x6FFF))
    return {
        "xinit": xp, "sha": hashlib.sha256(xp.read_bytes()).hexdigest(),
        "last": last_non_ff, "erased_target": erased(TARGET),
        "erased_fallback": erased(FALLBACK), "direct": direct, "ptr": ptr,
        "sp": sp, "literal_target": literal_target, "local_reach": local_reach,
        "init_ptrs": init_ptrs, "runs": runs,
        "indirect_count": sum(bool(INDIRECT.search(i.text)) for i in ins),
        "decoded_count": len(ins),
    }


def main():
    print("RAM OWNERSHIP EVIDENCE SCAN (read-only)\n")
    results = {}
    for name, part in BUILDS.items():
        r = results[name] = analyze(name, part)
        print(f"[{name}] X-init sha256 {r['sha']}")
        print(f"  last non-FFFF X-init word: X:${r['last']:04X}")
        print(f"  target erased: {r['erased_target'][1]} ({r['erased_target'][0]} words)")
        print(f"  fallback erased: {r['erased_fallback'][1]} ({r['erased_fallback'][0]} words)")
        print(f"  decoded instructions: {r['decoded_count']}; indirect-X forms: {r['indirect_count']}")
        print(f"  decoded absolute refs in target/fallback: {len(r['direct'])}")
        print(f"  local pointer+explicit-displacement reaches: {len(r['local_reach'])}")
        print(f"  initialized pointer-valued words in target/fallback (ambiguous constants): {len(r['init_ptrs'])}")
        print(f"  raw preceding-word/literal pairs in full target: {len(r['literal_target'])}")
        print("  SP immediate writes:", ", ".join(f"P:${p:05X}->${v:04X}" for p,v in r['sp']) or "none")
        near = [(p,reg,v) for p,reg,v in r['ptr'] if 0x6300 <= v < 0x6700]
        print("  pointer immediates X:$6300..$66FF:", ", ".join(f"P:${p:05X} {reg}=${v:04X}" for p,reg,v in near) or "none")
        print("  non-FFFF X-init runs >=X:$6000:", ", ".join(f"${a:04X}..${b:04X}" for a,b in r['runs']) or "none")
        if r['local_reach']:
            for h in r['local_reach']:
                print("   REACH", h)
        print()
    # Hard assertions protect report claims from drift.
    ar = results["CV6T-AR"]
    assert ar["last"] == 0x63A3
    assert all(r["erased_target"] == (0x710, True) for r in results.values())
    assert all(r["erased_fallback"] == (0x20, True) for r in results.values())
    assert all(not r["direct"] and not r["local_reach"] for r in results.values())
    assert all(not any(FALLBACK[0] <= v < FALLBACK[1] for _, v in r["init_ptrs"])
               for r in results.values())
    print("ASSERTIONS: PASS")
    print("LIMIT: unconstrained X:(Rn), X:(Rn+N), postincrement, stack, and runtime-loaded pointers remain unbounded.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
