#!/usr/bin/env python3
"""Minimal DSP56800E disassembler — only the opcodes we need, each verified.

Ghidra has no DSP56800E processor module and no public one exists, so this
covers the subset required to (a) read the OEM SBL's hot paths and (b) hand-
assemble the custom dump reader.

EVERY encoding below is transcribed from the DSP56800E/EX Core Reference Manual
(docs/erm.txt, from community.nxp.com), Appendix A "Instruction Opcode
Encoding". The erm.txt line number is cited on each entry so it can be re-checked.

Register encodings, Table A-7 (erm.txt ~32529):
    DD  : 00=X0 01=Y0 10=rsvd 11=Y1
    F   : 0=A 1=B
    FF  : 00=A 01=B 10=C 11=D
    EEE : 000=A 001=B 010=C 011=D 100=X0 101=Y0 110=rsvd 111=Y1
Addressing, Table A-16:
    m   : 0=(Rn)+   1=(Rn)+N
    MM  : 00=(Rn)+  01=(Rn+N)  10=(Rn)-  11=(Rn)
"""
import struct

DD = {0: 'X0', 1: 'Y0', 2: '??', 3: 'Y1'}
FF = {0: 'A', 1: 'B', 2: 'C', 3: 'D'}
EEE = {0: 'A', 1: 'B', 2: 'C', 3: 'D', 4: 'X0', 5: 'Y0', 6: '??', 7: 'Y1'}
GGG = EEE
MM = {0: '(R{n})+', 1: '(R{n}+N)', 2: '(R{n})-', 3: '(R{n})'}


def ea_m(m, rr):
    return f"(R{rr})+" if m == 0 else f"(R{rr})+N"


def disasm(words, i, base):
    """Return (text, length_in_words). words is a list of u16."""
    op = words[i]
    nxt = words[i + 1] if i + 1 < len(words) else None

    # --- prefix 1110 0AAA 0A11 AAAA : 24-bit absolute extension (erm 27041)
    # Supplies the upper 7 address bits for a following absolute-address insn.
    # E030 => all upper bits 0, i.e. address is X:$00xxxx.
    if (op & 0xF8CF) == 0xE0C0 or (op & 0xF8F0) == 0xE030:
        AAA = (op >> 8) & 7
        A6 = (op >> 6) & 1
        A30 = op & 0xF
        upper = (AAA << 5) | (A6 << 4) | A30
        # MOVE.W #xxxx,X:xxxxxx : prefix + 1000 0110 0101 0100 + addr + imm
        if nxt == 0x8654 and i + 3 < len(words):
            addr = (upper << 16) | words[i + 2]
            return f"MOVE.W  #${words[i+3]:04X},X:${addr:06X}", 4
        # MOVE.W X:xxxxxx,HHHHH / other absolute forms: show the prefix only
        return f"[abs24 prefix upper=${upper:02X}]", 1

    # --- JSR <ABS19> : 1110 0010 0101 A1AA          (erm ~24990) -----------
    # WARNING: the mask below is 0xFFF5, which is WRONG in general -- it also
    # forces address bit 16 (opcode bit 0) to 0, so it silently skips 0xE255
    # and 0xE257. It happens to catch every JSR in the SBL (all targets are
    # 0x4xxxx, bit0=0) which is why the 55/55 self-test never caught it, but on
    # 14C217 blk1 the common form IS 0xE255 and this mask finds nothing.
    # The correct fixed-bit mask is 0xFFF4 (bit 2 is the JSR/JMP literal).
    # Use work/wordA/dis56800e.py, which has this right. Left as-is here only
    # so the historical SBL analysis stays byte-reproducible.
    if (op & 0xFFF5) == 0xE254 and nxt is not None:
        a = ((op >> 3 & 1) << 18) | ((op >> 1 & 1) << 17) | ((op & 1) << 16) | nxt
        return f"JSR     P:${a:05X}", 2

    # --- JMP <ABS19> : same family, bit pattern differs by op bit2 ---------
    if (op & 0xFFF5) == 0xE250 and nxt is not None:
        a = ((op >> 3 & 1) << 18) | ((op >> 1 & 1) << 17) | ((op & 1) << 16) | nxt
        return f"JMP     P:${a:05X}", 2

    # --- MOVE.W P:<ea_m>,GGG : 1000 0GGG 0110 1mRR  (erm 26953) -----------
    if (op & 0xF8F8) == 0x8068:
        g = (op >> 8) & 7
        return f"MOVE.W  P:{ea_m((op >> 2) & 1, op & 3)},{GGG[g]}", 1

    # --- MOVE.W GGGG,P:<ea_m> : 1000 GGGG 0110 0mRR (erm 26929) -----------
    if (op & 0xF0F8) == 0x8060:
        g = (op >> 8) & 0xF
        return f"MOVE.W  {GGG.get(g & 7,'?')},P:{ea_m((op >> 2) & 1, op & 3)}", 1

    # --- MOVEU.W P:<ea_m>,SSS : 1000 1SSS 0110 1mRR (erm 27443) -----------
    if (op & 0xF8F8) == 0x8868:
        return f"MOVEU.W P:{ea_m((op >> 2) & 1, op & 3)},R{(op >> 8) & 7}", 1

    # --- CMP.W #<0-31>,DD : 0101 111D D00B BBBB     (erm 22537) -----------
    if (op & 0xFE60) == 0x5E00:
        return f"CMP.W   #${op & 0x1F:02X},{DD[(op >> 8) & 3]}", 1
    # --- CMP.W #<0-31>,FF : 0100 110F F00B BBBB     (erm 22541) -----------
    if (op & 0xFE60) == 0x4C00:
        return f"CMP.W   #${op & 0x1F:02X},{FF[(op >> 8) & 3]}", 1
    # --- CMP.W #xxxx,DD : 0101 111D D100 0000 +imm  (erm 22545) -----------
    if (op & 0xFE7F) == 0x5E40 and nxt is not None:
        return f"CMP.W   #${nxt:04X},{DD[(op >> 8) & 3]}", 2
    # --- CMP.W #xxxx,FF : 0100 110F F100 0000 +imm  (erm 22549) -----------
    if (op & 0xFE7F) == 0x4C40 and nxt is not None:
        return f"CMP.W   #${nxt:04X},{FF[(op >> 8) & 3]}", 2
    # --- CMP #<0-31>,FF : 0101 110F F00B BBBB       (erm 22140) -----------
    if (op & 0xFE60) == 0x5C00:
        return f"CMP     #${op & 0x1F:02X},{FF[(op >> 8) & 3]}", 1
    # --- CMP #xxxx,FF                                (erm 22144) ----------
    if (op & 0xFE7F) == 0x5C40 and nxt is not None:
        return f"CMP     #${nxt:04X},{FF[(op >> 8) & 3]}", 2

    # --- MOVE.W #xxxx,HHHHH : 1000 0111 010d dddd   (erm ~27018) ----------
    if (op & 0xFFE0) == 0x8740 and nxt is not None:
        return f"MOVE.W  #${nxt:04X},reg{op & 0x1F:02d}", 2

    return f".word   ${op:04X}", 1


def run(data, base_word, start=0, count=None, stop_at=None):
    nw = len(data) // 2
    w = list(struct.unpack('<%dH' % nw, data[:nw * 2]))
    out, i, n = [], start, 0
    while i < nw and (count is None or n < count):
        text, ln = disasm(w, i, base_word)
        raw = ' '.join(f"{w[i+k]:04X}" for k in range(ln))
        out.append((base_word + i, raw, text))
        i += ln
        n += 1
        if stop_at and text.startswith(stop_at):
            break
    return out


if __name__ == '__main__':
    import sys
    P = ("/home/gl/Projects/ford/PSCM/Research/bins/BV6T-14C220-AA/"
         "BV6T-14C220-AA_blk0_0x0009F000.bin")
    d = open(P, 'rb').read()
    BASE = 0x4F800

    # ---- SELF-TEST: the JSR decode must still be 55/55 in range ----------
    nw = len(d) // 2
    w = list(struct.unpack('<%dH' % nw, d[:nw * 2]))
    jsr = [(i, w[i], w[i + 1]) for i in range(nw - 1) if (w[i] & 0xFFF5) == 0xE254]
    inrange = sum(1 for i, o, n in jsr
                  if BASE <= (((o >> 3 & 1) << 18) | ((o >> 1 & 1) << 17)
                              | ((o & 1) << 16) | n) < BASE + nw)
    print(f"SELF-TEST JSR: {inrange}/{len(jsr)} targets in range "
          f"{'PASS' if inrange == len(jsr) == 55 else 'CHECK'}")
    print()

    start = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0
    cnt = int(sys.argv[2], 0) if len(sys.argv) > 2 else 40
    print(f"--- disassembly from word offset {start} (P:${BASE+start:05X}) ---")
    for addr, raw, text in run(d, BASE, start, cnt):
        print(f"  P:${addr:05X}  {raw:<14} {text}")
