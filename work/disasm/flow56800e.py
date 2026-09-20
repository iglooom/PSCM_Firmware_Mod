#!/usr/bin/env python3
"""Recursive-descent disassembler for the PSCM (DSP56800E / MC56F8366).

Builds on `work/wordA/dis56800e.py`'s mechanically-extracted encoding table
(569 encodings from the ERM's own opcode grids) and adds the two things a
linear listing cannot give you:

  1. CONTROL FLOW.  Conditional branches/jumps (`Bcc`, `Jcc`) are missing from
     `encodings.json` entirely -- `extract_encodings.py` filters mnemonics with
     `^[A-Z][A-Z0-9_.]*$`, which rejects the lowercase "cc" in `Bcc`/`Jcc`.
     They are added here from ERM A-57..A-58 (Bcc) / A-153 (Jcc) + Table A-18.
  2. RECURSIVE TRAVERSAL.  Code is reached from entry points (reset/ISR vectors,
     call targets) and followed through branches, so instruction boundaries are
     correct -- a linear sweep desynchronises on the first embedded constant.

Address model: P-space word addresses. The flash image is assembled into one
sparse word array via the VBF block load addresses (byte addr / 2).

Usage:
  python3 flow56800e.py --selftest
  python3 flow56800e.py --func 0x19DD5 [--max 200]
  python3 flow56800e.py --xref-x 0xF8A9        # who touches this X address
  python3 flow56800e.py --find-imm 0x0294      # who loads this constant
"""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "work", "wordA"))

import dis56800e as base  # noqa: E402

BINS = os.path.join(ROOT, "bins", "CV6T-14C217-AR")
BLOCKS = [  # (file, VBF byte load address)
    ("CV6T-14C217-AR_blk0_0x00000000.bin", 0x00000000),
    ("CV6T-14C217-AR_blk1_0x0001C000.bin", 0x0001C000),
]

CC = {0: "cc", 1: "cs", 2: "ne", 3: "eq", 4: "ge", 5: "lt", 6: "gt", 7: "le",
      8: "??", 9: "??", 10: "??", 11: "??", 12: "hi", 13: "ls", 14: "nn",
      15: "nr"}

# Instructions that end a basic block (no fallthrough).
TERMINAL = {"RTS", "RTSD", "RTI", "RTID", "FRTID", "BRA", "BRAD", "JMP",
            "JMPD", "STOP", "ILLEGAL"}


class Image:
    """Sparse P-space word image assembled from the VBF blocks."""

    def __init__(self):
        self.spans = []  # (word_start, [words])
        for fn, byte_addr in BLOCKS:
            p = os.path.join(BINS, fn)
            d = open(p, "rb").read()
            nw = len(d) // 2
            self.spans.append((byte_addr // 2,
                               list(struct.unpack("<%dH" % nw, d[:nw * 2]))))
        self.spans.sort()

    def __contains__(self, wa):
        return self.get(wa) is not None

    def get(self, wa):
        for s, w in self.spans:
            if s <= wa < s + len(w):
                return w[wa - s]
        return None

    def word(self, wa):
        v = self.get(wa)
        return 0xFFFF if v is None else v

    def window(self, wa, n=4):
        return [self.word(wa + k) for k in range(n)]

    def iter_words(self):
        for s, w in self.spans:
            for i, v in enumerate(w):
                yield s + i, v


# --------------------------------------------------------------------------
# Conditional branch / jump decoding (absent from encodings.json)
# --------------------------------------------------------------------------
def decode_cond(img, wa):
    """Decode Bcc/Jcc at word address `wa`.

    ERM opcode grids:
      Bcc <OFFSET7>   1010 CCCC 0Aaa aaaa        (1 word, 7-bit signed)
      Bcc <OFFSET18>  1110 0CCC 0110 1CAA + AAAA (2 words, 18-bit signed)
      Jcc <ABS19>     1110 0CCC 0101 ACAA + AAAA (2 words, 19-bit absolute)

    CCCC BIT ORDER (verified, not assumed): in the 2-word forms the condition
    field is split, and the standalone C at bit 2 is the **MSB**:
        CCCC = (bit2 << 3) | CCC
    Proof: under this reading the six unconditional encodings land exactly on
    Table A-18's three Reserved codes, uniformly across both the 1-word and
    2-word grids --
        1001 -> BRA  (A900) / JMP  (E154) / BRA18  (E16C)
        1010 -> BSR  (E26C) / JSR  (E254)
        1011 -> BRAD (AB00) / JMPD (E354) / BRAD18 (E36C)
    Taking bit 2 as the LSB instead would make JMP collide with Jeq, JSR with
    Jlt and JMPD with Jle -- i.e. three real conditions unencodable.  So codes
    8..11 are never a genuine Bcc/Jcc and are rejected here; the unconditional
    forms are decoded separately in decode().

    Returns (text, nwords, target|None, is_conditional) or None.
    """
    w = img.word(wa)

    # --- Bcc <OFFSET7> : 1010 CCCC 0Aaa aaaa ---------------------------
    if (w & 0xF080) == 0xA000:
        cc = (w >> 8) & 0xF
        off = w & 0x7F
        if off & 0x40:
            off -= 0x80
        if off == 0:
            return None          # "Aaaaaaa must never be all zeros" (A-346)
        if 8 <= cc <= 11:
            return None          # BRA/BRAD live here, not a conditional
        tgt = wa + 1 + off
        return (f"B{CC[cc]:<7}P:${tgt:05X}", 1, tgt, True)

    # --- 2-word E0xx forms ---------------------------------------------
    if (w & 0xF800) == 0xE000:
        nxt = img.word(wa + 1)
        ccc = (w >> 8) & 0x7
        cc = (((w >> 2) & 1) << 3) | ccc      # bit2 is the MSB, see above
        if 8 <= cc <= 11:
            return None          # BRA/BSR/BRAD/JMP/JSR/JMPD, not conditional
        # Bcc <OFFSET18>: .... 0110 1CAA
        if (w & 0x00F8) == 0x0068:
            off = (((w & 0x3) << 16) | nxt)
            if off & 0x20000:
                off -= 0x40000
            tgt = wa + 2 + off
            return (f"B{CC[cc]:<7}P:${tgt & 0xFFFFF:05X}", 2, tgt, True)
        # Jcc <ABS19>: .... 0101 ACAA
        if (w & 0x00F0) == 0x0050:
            a = (((w >> 3) & 1) << 18) | ((w & 0x3) << 16)
            tgt = a | nxt
            return (f"J{CC[cc]:<7}P:${tgt:05X}", 2, tgt, True)
    return None


def decode(img, wa, table):
    """Decode one instruction. Returns dict with text/len/target/flow."""
    c = decode_cond(img, wa)
    if c:
        txt, ln, tgt, _ = c
        return {"text": txt, "len": ln, "target": tgt, "flow": "cond"}

    words = img.window(wa, 4)
    txt, ln, cands = base.disasm(words, 0, table)
    mnem = txt.split()[0] if txt.split() else ""
    d = {"text": txt, "len": ln, "target": None, "flow": "seq",
         "ambig": len(cands)}

    w = words[0]
    # JSR/JMP <ABS19>: 1110 0x10 0101 A1AA  (mask 0xFFF4, see wordA/NOTES.md)
    if (w & 0xFFF4) in (0xE254, 0xE154, 0xE354):
        a = (((w >> 3) & 1) << 18) | (((w >> 1) & 1) << 17) | ((w & 1) << 16)
        d["target"] = a | words[1]
        d["len"] = 2
        d["flow"] = "call" if (w & 0xFFF4) == 0xE254 else "jump"
        nm = {0xE254: "JSR", 0xE154: "JMP", 0xE354: "JMPD"}[w & 0xFFF4]
        d["text"] = f"{nm:<8}P:${d['target']:05X}"
        return d
    # BRA <OFFSET7>: 1010 1001 0Aaa aaaa (A900) / BRAD A B00
    if (w & 0xFF80) in (0xA900, 0xAB00):
        off = w & 0x7F
        if off & 0x40:
            off -= 0x80
        d["target"] = wa + 1 + off
        d["len"] = 1
        d["flow"] = "jump"
        d["text"] = ("BRA" if (w & 0xFF80) == 0xA900 else "BRAD") \
            + f"     P:${d['target']:05X}"
        return d
    # BRA/BSR <OFFSET18>: E16C / E26C + 2 addr bits
    if (w & 0xFFFC) in (0xE16C, 0xE26C, 0xE36C):
        off = ((w & 3) << 16) | words[1]
        if off & 0x20000:
            off -= 0x40000
        d["target"] = wa + 2 + off
        d["len"] = 2
        nm = {0xE16C: "BRA", 0xE26C: "BSR", 0xE36C: "BRAD"}[w & 0xFFFC]
        d["flow"] = "call" if nm == "BSR" else "jump"
        d["text"] = f"{nm:<8}P:${d['target'] & 0xFFFFF:05X}"
        return d

    if mnem in TERMINAL:
        d["flow"] = "end"
    return d


# --------------------------------------------------------------------------
# Recursive traversal
# --------------------------------------------------------------------------
def trace(img, entry, table, max_insn=4000):
    """Follow control flow from `entry`. Returns {wa: instruction dict}."""
    seen = {}
    todo = [entry]
    while todo and len(seen) < max_insn:
        wa = todo.pop()
        while wa not in seen and img.get(wa) is not None:
            ins = decode(img, wa, table)
            seen[wa] = ins
            if ins["flow"] == "end":
                break
            if ins["target"] is not None and ins["flow"] != "call":
                todo.append(ins["target"])
                if ins["flow"] == "jump":
                    break
            elif ins["flow"] == "cond" and ins["target"] is not None:
                todo.append(ins["target"])
            wa += max(1, ins["len"])
            if len(seen) >= max_insn:
                break
    return seen


def render_func(img, entry, table, max_insn=4000):
    body = trace(img, entry, table, max_insn)
    targets = {i["target"] for i in body.values()
               if i["target"] is not None and i["flow"] != "call"}
    out = [f"; ---- sub_{entry:05X}  ({len(body)} instructions traced) ----"]
    prev = None
    for wa in sorted(body):
        if prev is not None and wa != prev:
            out.append(f"        ; ... gap to P:${wa:05X} ...")
        ins = body[wa]
        raw = " ".join(f"{img.word(wa + k):04X}" for k in range(ins["len"]))
        lbl = f"L_{wa:05X}:" if wa in targets else ""
        out.append(f"{lbl:<10} P:${wa:05X}  {raw:<12} {ins['text']}")
        prev = wa + ins["len"]
    return "\n".join(out)


# --------------------------------------------------------------------------
# Cross-reference scans
# --------------------------------------------------------------------------
# Instruction words whose FOLLOWING word is an absolute X address / immediate.
ABS_PREFIX = {
    0xF07C: "move.w  X:$%04X,A",      0xD07C: "move.w  A,X:$%04X",
    0xF47C: "move.w  X:$%04X,B",      0xD47C: "move.w  B,X:$%04X",
    0xF27C: "move.w  X:$%04X,?",      0xD27C: "move.w  ?,X:$%04X",
    0x8748: "moveu.w #$%04X,R0",      0x874C: "moveu.w #$%04X,R1",
    0x8740: "moveu.w #$%04X,?",       0x8744: "moveu.w #$%04X,?",
    0xF07D: "move.bp X:$%04X,A",      0xD07D: "move.bp A,X:$%04X",
    0x8656: "cmp.w   #$%04X,A",       0x8654: "move.w  #$%04X,A",
}


def scan_imm(img, value):
    hits = []
    for wa, w in img.iter_words():
        if w in ABS_PREFIX and img.word(wa + 1) == value:
            hits.append((wa, ABS_PREFIX[w] % value))
    return hits


def flexcan_map():
    """MC56F8366 FlexCAN message-buffer register map (X-space word addrs).

    MB base 0xF840, 8 words per buffer:
      +0 CONTROL/STATUS, +1 ID_HIGH, +2 ID_LOW, +3..+6 DATA0..3, +7 reserved.
    A standard 11-bit ID occupies ID_HIGH bits [12:2], i.e. stored as ID<<2.
    """
    m = {}
    for n in range(16):
        b = 0xF840 + 8 * n
        for off, nm in ((0, "CTRL"), (1, "IDHI"), (2, "IDLO"), (3, "D0"),
                        (4, "D1"), (5, "D2"), (6, "D3")):
            m[b + off] = f"MB{n}_{nm}"
    return m


# --------------------------------------------------------------------------
def selftest(img, table):
    ok = True

    def chk(name, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"  {'PASS' if good else 'FAIL'}  {name}: {got!r}"
              + ("" if good else f"  want {want!r}"))

    print("-- Bcc/Jcc decoder (ERM A-57/A-153, Table A-18) --")
    # BNE *+6 : 1010 0010 0000 0110 = 0xA206, cc=0010=ne, off=+6
    class Fake:
        def __init__(self, ws):
            self.ws = ws

        def word(self, wa):
            return self.ws.get(wa, 0)

        def get(self, wa):
            return self.ws.get(wa)

        def window(self, wa, n=4):
            return [self.word(wa + k) for k in range(n)]

    f = Fake({0x1000: 0xA206})
    r = decode_cond(f, 0x1000)
    chk("BNE <OFFSET7> @0x1000 (+6)", r[0].split()[0] + " " + r[0].split()[1],
        "Bne P:$01007")
    # backward branch: off = -2 -> 0x7E
    f = Fake({0x1000: 0xA27E})
    r = decode_cond(f, 0x1000)
    chk("BNE <OFFSET7> backward (-2)", r[2], 0x0FFF)
    # Aaaaaaa all-zero is NOT a Bcc (A-346)
    f = Fake({0x1000: 0xA200})
    chk("offset-0 rejected", decode_cond(f, 0x1000), None)
    # Jeq <ABS19>: CCCC=0011 -> bit2(MSB)=0, CCC=011
    #   w = 1110 0011 0101 0000 = 0xE350, A bits = 0
    f = Fake({0x1000: 0xE350, 0x1001: 0x2345})
    r = decode_cond(f, 0x1000)
    chk("Jeq <ABS19> target", r[2], 0x02345)
    chk("Jeq <ABS19> mnemonic", r[0].split()[0], "Jeq")
    chk("Jeq <ABS19> len", r[1], 2)
    # the unconditional forms must NOT be claimed by the conditional decoder
    for word, nm in ((0xE154, "JMP"), (0xE254, "JSR"), (0xE354, "JMPD"),
                     (0xA900, "BRA"), (0xAB00, "BRAD"), (0xE26C, "BSR")):
        f = Fake({0x1000: word, 0x1001: 0x1111})
        chk(f"{nm} not decoded as conditional", decode_cond(f, 0x1000), None)

    print("-- unconditional flow (cross-checked vs pscm_disasm rules) --")
    d = decode(img, 0x19C35, table)   # known: jsr P:$19DD5
    chk("JSR at P:$19C35", (d["text"].split()[0], d["target"]),
        ("JSR", 0x19DD5))

    print("-- image load --")
    # reset vector is a JSR <ABS19>; the low bits carry address bits 18..16,
    # so match the MASK (0xFFF4) and not a bare literal (wordA/NOTES.md).
    chk("reset vector is JSR", img.word(0x00000) & 0xFFF4, 0xE254)
    chk("reset vector target", decode(img, 0x00000, table)["target"], 0x12BC9)
    chk("blk1 present", img.get(0x2213D), 0xA503)

    print("-- FlexCAN map --")
    m = flexcan_map()
    chk("MB12_D0 address", [k for k, v in m.items() if v == "MB12_D0"][0],
        0xF8A3)
    chk("MB13_IDHI address", [k for k, v in m.items() if v == "MB13_IDHI"][0],
        0xF8A9)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--func", type=lambda s: int(s, 0))
    ap.add_argument("--max", type=int, default=4000)
    ap.add_argument("--find-imm", type=lambda s: int(s, 0))
    ap.add_argument("--xref-x", type=lambda s: int(s, 0))
    a = ap.parse_args()

    img = Image()
    table = base.load_table()

    if a.selftest or len(sys.argv) == 1:
        sys.exit(0 if selftest(img, table) else 1)
    if a.func is not None:
        print(render_func(img, a.func, table, a.max))
    if a.find_imm is not None:
        for wa, t in scan_imm(img, a.find_imm):
            print(f"  P:${wa:05X}  {t}")
    if a.xref_x is not None:
        for wa, t in scan_imm(img, a.xref_x):
            print(f"  P:${wa:05X}  {t}")


if __name__ == "__main__":
    main()
