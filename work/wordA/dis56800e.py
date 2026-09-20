#!/usr/bin/env python3
"""Table-driven DSP56800E disassembler.

Encodings are NOT hand-transcribed: they are extracted mechanically from the
DSP56800E/EX Core Reference Manual's own opcode grids by
`extract_encodings.py` (569 encodings, 148 mnemonics) into `encodings.json`.
Hand transcription is the #1 defect source on this architecture, so the manual
parses itself and every entry carries its erm.txt line number.

Design notes
------------
* Instruction LENGTH is the hard part on this core: many opcodes take extra
  words (16-bit immediate, absolute address, 32-bit long immediate). Length is
  inferred from the operand syntax string (`#xxxx`, `xxxxxx`, `<ABS19>`, ...),
  which the manual writes consistently.
* Where several encodings match one word, the most specific (largest mask)
  wins; ties are reported so ambiguity is visible rather than silent.
* Unknown words print `.word $XXXX` — partial coverage honestly labelled beats
  fabricated mnemonics.

Self-test (`--selftest`): the JSR call-target containment check on the OEM SBL
must stay at 55/55, the statistical result that earned trust in this decoding.

Usage:
  python3 dis56800e.py --selftest
  python3 dis56800e.py FILE.bin --base-word 0xE000 --start 0x81B4 --count 80
      --start/--end are BYTE offsets into the file; --base-word is the word
      address the file's first word loads at.
"""
import argparse
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TBL = os.path.join(HERE, "encodings.json")

DD = {0: "X0", 1: "Y0", 2: "??", 3: "Y1"}
F1 = {0: "A", 1: "B"}
FF = {0: "A", 1: "B", 2: "C", 3: "D"}
EEE = {0: "A", 1: "B", 2: "C", 3: "D", 4: "X0", 5: "Y0", 6: "??", 7: "Y1"}
FFF = {0: "A", 1: "B", 2: "C", 3: "D", 4: "A", 5: "B", 6: "C", 7: "D"}
MM = {0: "(R{n})+", 1: "(R{n}+N)", 2: "(R{n})-", 3: "(R{n})"}

# --- Table A-10, MOVE register encodings (erm ~32876) ---------------------
RRR = {0: "R0", 1: "R1", 2: "R2", 3: "R3", 4: "R4", 5: "R5", 6: "N", 7: "SP"}
NNN = {0: "R0", 1: "R1", 2: "R2", 3: "R3", 4: "R4", 5: "R5", 6: "N", 7: "??"}
SSS = NNN
RR = {0: "R0", 1: "R1", 2: "R2", 3: "R3"}
GGG = {0: "A", 1: "B", 2: "C", 3: "A1", 4: "X0", 5: "Y0", 6: "B1", 7: "Y1"}
GGGG = {0: "A", 1: "B", 2: "C", 3: "A1", 4: "X0", 5: "Y0", 6: "B1", 7: "Y1",
        8: "R0", 9: "R1", 10: "R2", 11: "R3", 12: "R4", 13: "R5", 14: "N", 15: "??"}

# --- Table A-11, DDDDD: different LOAD vs STORE register sets (erm ~33006)
# index -> (load_name, store_name)
D5 = {
    0b00000: ("A", "A1"), 0b00010: ("B", "B1"), 0b00100: ("C", "C1"),
    0b00110: ("D", "D1"), 0b01000: ("X0", "X0"), 0b01010: ("Y0", "Y0"),
    0b01100: ("??", "Y"), 0b01110: ("Y1", "Y1"),
    0b00001: ("A1", "A"), 0b00011: ("B1", "B"), 0b00101: ("C1", "C"),
    0b00111: ("D1", "D"), 0b01001: ("A2", "A2"), 0b01011: ("B2", "B2"),
    0b01101: ("A0", "A0"), 0b01111: ("B0", "B0"),
    0b10000: ("R0", "R0"), 0b10010: ("R1", "R1"), 0b10100: ("R2", "R2"),
    0b10110: ("R3", "R3"), 0b11000: ("R4", "R4"), 0b11010: ("R5", "R5"),
    0b11100: ("N", "N"), 0b11110: ("??", "??"),
    0b10001: ("SP", "SP"), 0b10011: ("N3", "N3"), 0b10101: ("M01", "M01"),
    0b10111: ("HWS", "HWS"), 0b11001: ("OMR", "OMR"), 0b11011: ("SR", "SR"),
    0b11101: ("LC", "LC"), 0b11111: ("LA", "LA"),
}

# --- Table A-12, ddddd: bit-manipulation register encodings (erm ~33080) --
d5 = {
    0b00000: "A", 0b00001: "B", 0b00010: "C", 0b00011: "D",
    0b00100: "X0", 0b00101: "Y0", 0b00110: "??", 0b00111: "Y1",
    0b01000: "R0", 0b01001: "R1", 0b01010: "R2", 0b01011: "R3",
    0b01100: "R4", 0b01101: "R5", 0b01110: "N", 0b01111: "??",
    0b10000: "A1", 0b10001: "B1", 0b10010: "C1", 0b10011: "D1",
    0b10100: "A2", 0b10101: "B2", 0b10110: "A0", 0b10111: "B0",
    0b11000: "SP", 0b11001: "N3", 0b11010: "M01", 0b11011: "HWS",
    0b11100: "OMR", 0b11101: "SR", 0b11110: "LC", 0b11111: "LA",
}

# --- Table A-13, size-dependent hhh / hhhh (erm ~33186) ------------------
# (byte/word load, byte/word store, long load, long store)
h3 = {
    0: ("A", "A1", "A", "A10"), 1: ("B", "B1", "B", "B10"),
    2: ("C", "C1", "C", "C10"), 3: ("D", "D1", "D", "D10"),
    4: ("X0", "X0", "??", "??"), 5: ("Y0", "Y0", "??", "??"),
    6: ("??", "Y", "??", "??"), 7: ("Y1", "Y1", "Y", "Y"),
}
h4 = dict(h3)
h4.update({8: ("R0",) * 4, 9: ("R1",) * 4, 10: ("R2",) * 4, 11: ("R3",) * 4,
           12: ("R4",) * 4, 13: ("R5",) * 4, 14: ("N",) * 4, 15: ("??",) * 4})


def load_table(path=TBL):
    with open(path) as f:
        enc = json.load(f)
    for e in enc:
        e["nbits"] = bin(e["mask"]).count("1")
    # most specific first
    enc.sort(key=lambda e: -e["nbits"])
    return enc


def extra_words(ops):
    """How many extension words does this operand syntax imply?

    The manual's operand strings are consistent:
      #xxxx / xxxx (16-bit imm or abs addr)      -> 1
      #xxxxxxxx / xxxxxxxx (long / 24-bit abs)   -> 2
      <ABS19>                                    -> 1 (low 16 bits follow)
    """
    n = 0
    # count 'xxxx' groups; 5+ x's means a long (2 words)
    for m in re.finditer(r"#?(x{4,})", ops):
        n += 2 if len(m.group(1)) > 4 else 1
    if "<ABS19>" in ops or "<ABS16>" in ops:
        n += 1
    if "<ABS24>" in ops:
        n += 2
    return n


def fld(word, bits):
    """Extract a field given its bit positions (as listed MSB-first)."""
    v = 0
    for b in bits:
        v = (v << 1) | ((word >> b) & 1)
    return v


def is_store(ops):
    """True if the memory reference is the DESTINATION (register -> memory).

    Table A-11/A-13 give different register names for load vs store, so the
    direction must be known before a register field can be named. The manual
    writes operands as src,dst — so a memory ref after the comma is a store.
    """
    if "," not in ops:
        return False
    dst = ops.rsplit(",", 1)[1]
    return ("X:" in dst) or ("P:" in dst)


def render(e, word, words, i):
    """Produce operand text with variable fields substituted."""
    ops = e["operands"]
    f = e["fields"]
    vals = {k: fld(word, v) for k, v in f.items()}
    store = is_store(ops)
    is_long = ".L" in e["mnem"]

    # ---- addressing modes -------------------------------------------------
    rr = vals.get("R", 0)
    if "<ea_m>" in ops and "m" in vals:
        ops = ops.replace("<ea_m>", "(R%d)+" % rr if vals["m"] == 0 else "(R%d)+N" % rr)
    if "<ea_MM>" in ops and "M" in vals:
        ops = ops.replace("<ea_MM>", MM[vals["M"]].format(n=rr))
    for pat in ("<ea_Rn>", "<ea>"):
        if pat in ops:
            ops = ops.replace(pat, "(R%d)" % rr)

    # ---- register fields, longest field name first -----------------------
    # (so GGGG is not partially eaten by GGG, DDDDD not by DD, etc.)
    #
    # NOTE: the operand SYNTAX name and the BIT-GRID letter often differ, e.g.
    #   "MOVE.W X:xxxx,HHHHH"  has grid  1111 dddd d111 1100  -> field letter 'd'
    # so a field is resolved by (a) its own initial, else (b) the single
    # variable field of matching WIDTH present in the grid.
    def field_value(fieldname):
        c = fieldname[0]
        if c in vals:
            return vals[c]
        want = len(fieldname)
        cands = [k for k, bits in f.items() if len(bits) == want]
        if len(cands) == 1:
            return vals[cands[0]]
        return None

    def named(fieldname, v):
        if fieldname in ("DDDDD", "HHHHH"):
            return D5.get(v, ("??", "??"))[1 if store else 0]
        if fieldname == "ddddd":
            return d5.get(v, "??")
        if fieldname in ("HHHH", "hhhh"):
            t = h4.get(v, ("??",) * 4)
            return t[(2 if is_long else 0) + (1 if store else 0)]
        if fieldname in ("HHH", "hhh"):
            t = h3.get(v, ("??",) * 4)
            return t[(2 if is_long else 0) + (1 if store else 0)]
        if fieldname == "GGGG":
            return GGGG.get(v, "??")
        if fieldname == "GGG":
            return GGG.get(v, "??")
        if fieldname in ("RRR", "nnn"):
            return RRR.get(v, "??")
        if fieldname in ("NNN", "SSS", "SSSS"):
            return NNN.get(v, "??")
        if fieldname == "RR":
            return RR.get(v, "??")
        if fieldname == "EEE":
            return EEE.get(v, "??")
        if fieldname == "FFF":
            return FFF.get(v, "??")
        if fieldname == "FF":
            return FF.get(v, "??")
        if fieldname == "DD":
            return DD.get(v, "??")
        if fieldname == "F":
            return F1.get(v, "??")
        return None

    for fieldname in sorted(
            ("DDDDD", "ddddd", "HHHHH", "HHHH", "hhhh", "HHH", "hhh",
             "GGGG", "GGG", "RRR", "nnn", "NNN", "SSSS", "SSS", "RR",
             "EEE", "FFF", "FF", "DD", "F"), key=len, reverse=True):
        while fieldname in ops:
            v = field_value(fieldname)
            if v is None:
                break
            nm = named(fieldname, v)
            if nm is None:
                break
            ops = ops.replace(fieldname, nm, 1)

    # ---- immediates / absolute addresses from following words ------------
    k = i + 1

    def nextw():
        nonlocal k
        w = words[k] if k < len(words) else 0
        k += 1
        return w

    def sub_long(mo):
        lo = nextw()
        hi = nextw()
        v = (hi << 16) | lo
        return f"#${v:08X}" if mo.group(0).startswith("#") else f"${v:06X}"

    def sub_word(mo):
        return f"#${nextw():04X}" if mo.group(0).startswith("#") else f"${nextw():04X}"

    ops = re.sub(r"#?x{5,}", sub_long, ops)
    ops = re.sub(r"#?x{4}", sub_word, ops)

    if "<ABS19>" in ops:
        a = (fld(word, f["A"]) << 16) if "A" in f else 0
        ops = ops.replace("<ABS19>", f"P:${a | nextw():05X}")
    if "<ABS16>" in ops:
        ops = ops.replace("<ABS16>", f"P:${nextw():04X}")

    # ---- packed small immediates ----------------------------------------
    for nm in ("B", "b"):
        if nm in vals and re.search(r"#<0[-–]\d+>", ops):
            ops = re.sub(r"#<0[-–]\d+>", f"#${vals[nm]:02X}", ops, count=1)
    # short relative branch offset
    if "<OFFSET7>" in ops and "a" in vals:
        off = vals["a"]
        if off & 0x40:
            off -= 0x80
        ops = ops.replace("<OFFSET7>", f"*{off:+d}")

    return ops


def disasm(words, i, table):
    """Return (text, length_in_words, matches)."""
    w = words[i]
    cands = [e for e in table if (w & e["mask"]) == e["value"]]
    if not cands:
        return f".word   ${w:04X}", 1, []
    best = cands[0]
    ops = render(best, w, words, i)
    ln = 1 + extra_words(best["operands"])
    ln = min(ln, max(1, len(words) - i))
    txt = f"{best['mnem']:<8}{ops}"
    return txt, ln, cands


def run(data, base_word, start_w=0, count=None, table=None):
    table = table or load_table()
    nw = len(data) // 2
    words = list(struct.unpack("<%dH" % nw, data[:nw * 2]))
    out = []
    i = start_w
    n = 0
    while i < nw and (count is None or n < count):
        txt, ln, cands = disasm(words, i, table)
        raw = " ".join(f"{words[i+k]:04X}" for k in range(min(ln, nw - i)))
        out.append((base_word + i, i * 2, raw, txt, len(cands)))
        i += max(1, ln)
        n += 1
    return out


def selftest(table):
    """JSR containment on the OEM SBL: the check that earned trust (55/55).

    NOTE on the mask: JSR <ABS19> is 1110 0010 0101 A1AA, so bits 3,1,0 are
    address bits 18,17,16 and bit 2 is the literal 1 -> fixed-bit mask 0xFFF4.
    Earlier code here used 0xFFF5, which additionally forced bit0 = 0 and so
    silently skipped 0xE255/0xE257. It still scored 55/55 on the SBL because
    every SBL target is 0x4xxxx (A = 100, bit0 = 0) -- a latent bug that only
    shows up on blk1, where 0xE255 is the common form. Kept at 0xFFF4 now, and
    the expected count is asserted so a regression is loud.
    """
    P = ("/home/gl/Projects/ford/PSCM/Research/bins/BV6T-14C220-AA/"
         "BV6T-14C220-AA_blk0_0x0009F000.bin")
    if not os.path.exists(P):
        print("SELFTEST SKIP: SBL image not found")
        return True
    d = open(P, "rb").read()
    BASE = 0x4F800
    nw = len(d) // 2
    w = list(struct.unpack("<%dH" % nw, d[:nw * 2]))
    jsr = [(i, w[i], w[i + 1]) for i in range(nw - 1)
           if (w[i] & 0xFFF4) == 0xE254]
    tgt = [((o >> 3 & 1) << 18) | ((o >> 1 & 1) << 17) | ((o & 1) << 16) | n
           for _, o, n in jsr]
    inr = sum(1 for t in tgt if BASE <= t < BASE + nw)
    ok = (inr == len(jsr) == 55)
    print(f"SELFTEST JSR containment: {inr}/{len(jsr)} in range, "
          f"{len(set(tgt))} distinct  {'PASS' if ok else 'CHECK'}")

    # table sanity: the encodings we independently verified must still match
    checks = [(0xE254, "JSR"), (0x8654, "MOVE.W")]
    allok = ok
    for word, mnem in checks:
        hit = [e for e in table if (word & e["mask"]) == e["value"]
               and e["mnem"] == mnem]
        print(f"SELFTEST enc 0x{word:04X} -> {mnem}: "
              f"{'PASS' if hit else 'FAIL'}")
        allok = allok and bool(hit)

    # CROSS-VALIDATION against the independent pscm_mc56f8366_disasm_v1 tool,
    # whose 4 rules were hand-verified from CodeWarrior output. Two independent
    # derivations agreeing is much stronger than either alone.
    print("-- cross-check vs pscm_mc56f8366_disasm_v1 hand-verified rules --")
    XV = [
        ([0xE254, 0x1234], "JSR", "P:$01234"),
        ([0xE708], "RTS", ""),
        ([0xF07C, 0x1234], "MOVE.W", "X:$1234,A"),
    ]
    for ws, want_mnem, want_ops in XV:
        txt, ln, _ = disasm(ws, 0, table)
        got_mnem = txt.split()[0]
        got_ops = txt[8:].strip()
        good = got_mnem == want_mnem and (not want_ops or got_ops == want_ops)
        print(f"   {' '.join(f'{w:04X}' for w in ws):<12} -> {txt:<28}"
              f" expect {want_mnem} {want_ops:<12} {'PASS' if good else 'FAIL'}")
        allok = allok and good

    # KNOWN AMBIGUITY, documented rather than papered over.
    # 0x8748 = 1000 0111 0100 1000 fits BOTH, bit-identically:
    #     MOVE.W  #xxxx,HHHHH   1000 0111 010d dddd   ddddd=01000 -> X0 (A-11)
    #     MOVEU.W #xxxx,SSSS    1000 0111 010d dddd   low4  =1000 -> R0 (AGU)
    # The manual's encodings genuinely overlap; an assembler resolves by the
    # operand written, a decoder cannot. For THIS firmware the AGU reading is
    # the meaningful one: e.g. `8748 8C00` loads 0x8C00, and blk2's load address
    # is 0x04008C00 - i.e. a POINTER, so R0. pscm_mc56f8366_disasm_v1 reached the
    # same conclusion independently and names it moveu.w #$xxxx,R0.
    txt, _, cands = disasm([0x8748, 0x1234], 0, table)
    print(f"   8748 1234    -> {txt:<28} AMBIGUOUS: {len(cands)} encodings "
          f"(MOVE.W->X0 / MOVEU.W->R0); prefer R0 in this firmware")

    print(f"SELFTEST table: {len(table)} encodings loaded")
    return allok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?")
    ap.add_argument("--base-word", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--start", type=lambda s: int(s, 0), default=0,
                    help="BYTE offset into the file")
    ap.add_argument("--count", type=lambda s: int(s, 0), default=60,
                    help="instructions to decode")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    table = load_table()
    if a.selftest or not a.file:
        sys.exit(0 if selftest(table) else 1)

    d = open(a.file, "rb").read()
    print(f"# {os.path.basename(a.file)}  base word ${a.base_word:05X}  "
          f"from byte 0x{a.start:X}")
    print(f"# {'word':>7} {'byte':>8}  {'raw':<20} instruction")
    for aw, bo, raw, txt, nc in run(d, a.base_word, a.start // 2, a.count, table):
        amb = "" if nc <= 1 else f"   ; {nc} enc match"
        print(f"  P:${aw:05X} b0x{bo:05X}  {raw:<20} {txt}{amb}")


if __name__ == "__main__":
    main()
