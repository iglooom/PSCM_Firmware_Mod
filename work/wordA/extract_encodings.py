#!/usr/bin/env python3
"""Extract EVERY instruction encoding from the DSP56800E/EX Core Reference Manual.

Hand-transcribing bit patterns is the #1 defect source in this kind of work
(see skill ecu-firmware-reverse-engineering, unknown-architecture-disassembly.md).
So we parse the manual's own encoding grids mechanically instead.

Grid format in docs/erm.txt (pdftotext -layout output):

    ....  15            12   11            8     7            4     3              0
    <blank>
    MNEM  operands          0  0  0  0   1  G  G  G   F  0  1  0   0  m  R  R

i.e. a header line naming the bit columns, then a line whose LAST 16
whitespace-separated tokens are single characters: '0', '1' or a field letter.
Everything before those 16 tokens is the mnemonic + operand syntax.

Output: encodings.json  — a list of
    {mnem, operands, bits:"0000 1GGG F010 0mRR", mask, value, fields:{...}, line}
where mask/value let a decoder match a word directly:  (w & mask) == value
and fields maps each variable letter to its bit positions (MSB-first, bit15=15).

Usage:  python3 extract_encodings.py [erm.txt] [-o encodings.json]
"""
import json
import re
import sys
from collections import Counter, defaultdict

ERM = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
    else "/home/gl/Projects/ford/PSCM/Research/docs/erm.txt"
OUT = "encodings.json"
if "-o" in sys.argv:
    OUT = sys.argv[sys.argv.index("-o") + 1]

lines = open(ERM, encoding="utf-8", errors="replace").read().split("\n")

# a bit-grid header looks like: 15 ... 12 11 ... 8 7 ... 4 3 ... 0
HDR = re.compile(r"\b15\b.*\b12\b.*\b11\b.*\b8\b.*\b7\b.*\b4\b.*\b3\b.*\b0\b")

# a legal bit cell: binary digit or a single field letter
CELL = re.compile(r"^[01A-Za-z~]$")


def parse_grid_line(ln):
    """If ln ends with 16 single-char bit cells, return (prefix, cells)."""
    toks = ln.split()
    if len(toks) < 16:
        return None
    cells = toks[-16:]
    if not all(CELL.match(c) for c in cells):
        return None
    # reject lines that are all letters and look like prose
    if sum(c in "01" for c in cells) == 0:
        return None
    prefix = " ".join(toks[:-16]).strip()
    return prefix, cells


def split_mnem(prefix):
    """Separate 'ADD' from 'X:<ea_m>,GGG'. First token is the mnemonic."""
    if not prefix:
        return "", ""
    p = prefix.split()
    return p[0], " ".join(p[1:])


out = []
seen = set()
i = 0
while i < len(lines):
    if HDR.search(lines[i]):
        # the grid line is within the next few lines
        for j in range(i + 1, min(i + 5, len(lines))):
            g = parse_grid_line(lines[j])
            if not g:
                continue
            prefix, cells = g
            mnem, ops = split_mnem(prefix)
            if not mnem or not re.match(r"^[A-Z][A-Z0-9_.]*$", mnem):
                break
            mask = 0
            value = 0
            fields = defaultdict(list)
            for k, c in enumerate(cells):
                bit = 15 - k
                if c == "0":
                    mask |= 1 << bit
                elif c == "1":
                    mask |= 1 << bit
                    value |= 1 << bit
                else:
                    fields[c].append(bit)
            bits = "".join(cells)
            key = (mnem, ops, bits)
            if key in seen:
                break
            seen.add(key)
            out.append({
                "mnem": mnem,
                "operands": ops,
                "bits": " ".join(bits[b:b + 4] for b in range(0, 16, 4)),
                "mask": mask,
                "value": value,
                "fields": {k: v for k, v in fields.items()},
                "line": j + 1,
            })
            break
    i += 1

json.dump(out, open(OUT, "w"), indent=1)

print(f"extracted {len(out)} encodings -> {OUT}")
c = Counter(e["mnem"] for e in out)
print(f"distinct mnemonics: {len(c)}")
print("most encoded:", ", ".join(f"{m}({n})" for m, n in c.most_common(12)))

# sanity: the encodings we already trust must be present and must match
KNOWN = {
    0xE254: "JSR",     # 1110 0010 0101 A1AA
    0x8654: "MOVE",    # MOVE.W #xxxx,X:xxxx
}
print("\n-- spot check against already-verified words --")
for word, want in KNOWN.items():
    hits = [e for e in out if (word & e["mask"]) == e["value"]
            and e["mnem"].startswith(want)]
    print(f"  0x{word:04X}: {len(hits)} matching {want}* encoding(s)"
          + (f"  e.g. {hits[0]['mnem']} {hits[0]['operands']} [{hits[0]['bits']}]"
             if hits else "   <-- MISSING, extractor is wrong"))
