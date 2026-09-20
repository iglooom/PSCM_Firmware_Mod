#!/usr/bin/env python3
"""Find every reference to the checksum RAM cell X:$11EF and disassemble the
code that WRITES it - that code is the word-A accumulator.

Established by find_loop.py (blk1+0x82A0, present in all versions):
    MOVE.L #$0003FFF5,R0    ; address of word A in flash
    MOVE.W P:(R0)+,A        ; read the STORED word A
    CMP.W  X:$11EF,A        ; compare with RAM cell X:$11EF
=> X:$11EF holds the COMPUTED checksum. Whoever writes it computes word A.

Absolute X: addressing forms that carry a 16-bit address in the NEXT word
(so we must decode the opcode, never scan for the bare value - see skill
unknown-architecture-disassembly.md §3b):
    F07C xxxx  MOVE.W X:$xxxx,A        (load)
    F77C xxxx  MOVE.W X:$xxxx,Y1
    F57C xxxx  MOVE.W X:$xxxx,Y0
    D07C xxxx  MOVE.W A1,X:$xxxx       (store)
    D57C xxxx  MOVE.W Y0,X:$xxxx
    FF7C xxxx  TST.W  X:$xxxx
    4C44 xxxx  CMP.W  X:$xxxx,A
    4E44 xxxx  DEC.W  X:$xxxx
plus the generic 'low byte 0x7C / 0x44' families. We simply decode every word
with the real table and keep instructions whose rendered text contains the
target address.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dis56800e as D

BASE_DIR = "/home/gl/Projects/ford/PSCM/Research/bins"
VERS = ["BV6T-14C217-AF", "CV6T-14C217-AH", "CV6T-14C217-AR"]
WORD_BASE = 0xE000
TARGETS = [int(a, 0) for a in (sys.argv[1:] or ["0x11EF"])]

table = D.load_table()

for VER in VERS:
    p = f"{BASE_DIR}/{VER}/{VER}_blk1_0x0001C000.bin"
    if not os.path.exists(p):
        continue
    d = open(p, "rb").read()
    nw = len(d) // 2
    w = list(struct.unpack("<%dH" % nw, d[:nw * 2]))

    print("=" * 78)
    print(VER)
    for T in TARGETS:
        hits = []
        i = 0
        # decode linearly; record any instruction mentioning $T as an X: address
        while i < nw - 2:
            # cheap prefilter: the address must appear as the very next word
            if w[i + 1] == T:
                txt, ln, cands = D.disasm(w, i, table)
                if f"${T:04X}" in txt and "X:" in txt:
                    hits.append((i, txt, len(cands)))
            i += 1
        print(f"\n-- X:${T:04X}: {len(hits)} referencing instruction(s) --")
        stores = []
        for i, txt, nc in hits:
            # a store has the X: ref AFTER the comma
            is_st = "," in txt and f"X:${T:04X}" in txt.rsplit(",", 1)[1]
            kind = "STORE" if is_st else "read "
            if is_st:
                stores.append(i)
            print(f"   {kind} blk1 word 0x{i:05X} byte 0x{i*2:05X} "
                  f"P:${WORD_BASE+i:05X}  {txt}")
        print(f"   -> {len(stores)} store(s)")

    # disassemble around each store of the FIRST target
    T = TARGETS[0]
    rets = [i for i in range(nw) if w[i] in (0xE708, 0xE710)]
    shown = set()
    for i in range(nw - 2):
        if w[i + 1] != T:
            continue
        txt, ln, _ = D.disasm(w, i, table)
        if not (f"X:${T:04X}" in txt and "," in txt
                and f"X:${T:04X}" in txt.rsplit(",", 1)[1]):
            continue
        prev = [r for r in rets if r < i]
        s = (prev[-1] + 1) if prev else 0
        if s in shown:
            continue
        shown.add(s)
        print(f"\n### function writing X:${T:04X}  (store at byte 0x{i*2:05X})")
        print(f"    entry after RTS at word 0x{s:05X} (byte 0x{s*2:05X})")
        j = s
        n = 0
        while j < nw and n < 60:
            t2, l2, c2 = D.disasm(w, j, table)
            raw = " ".join(f"{w[j+k]:04X}" for k in range(min(l2, nw - j)))
            mark = "   <<<< STORE" if j == i else ""
            amb = "" if len(c2) <= 1 else f"  ;{len(c2)}enc"
            print(f"  P:${WORD_BASE+j:05X} b0x{j*2:05X}  {raw:<22} {t2}{amb}{mark}")
            if t2.startswith(("RTS", "RTI")) and j > s:
                print("    --- RTS ---")
                break
            j += max(1, l2)
            n += 1
    break        # first version is enough for the structure
