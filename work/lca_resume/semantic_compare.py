#!/usr/bin/env python3
"""Structural three-build comparison of the PSCM LCA state/torque path.

This intentionally does not diff raw firmware blobs.  It decodes instruction
records, removes relocated X/P operands, aligns semantic instruction tokens,
and uses CV6T-AH -> CV6T-AR as the same-family compilation control.

Inputs are read-only.  Outputs: semantic_compare.json and semantic_compare.txt
beside this script.
"""
from __future__ import annotations

import collections
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, asdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "work", "disasm"),
                os.path.join(ROOT, "work", "wordA")]
import dis56800e as base  # noqa: E402
import flow56800e as flow  # noqa: E402
import struct_cmp as sc  # noqa: E402

BUILDS = ("BV6T-AF", "CV6T-AH", "CV6T-AR")
STATE_FN = {"BV6T-AF": (0x26D18, 0x26EBE),
            "CV6T-AH": (0x2912E, 0x292DA),
            "CV6T-AR": (0x2ABB5, 0x2AD61)}
STATE_CELLS = {
    "BV6T-AF": {"mode": 0x23B5, "code": 0x23BF, "gate": 0x23C7,
                 "lane": 0x23E4},
    "CV6T-AH": {"mode": 0x28A7, "code": 0x28B1, "gate": 0x28B9,
                 "lane": 0x28D6},
    "CV6T-AR": {"mode": 0x2DAF, "code": 0x2DB9, "gate": 0x2DC1,
                 "lane": 0x2DDE},
}
TORQUE_FN = {"BV6T-AF": 0x27476, "CV6T-AH": 0x29586,
             "CV6T-AR": 0x2B07C}
TORQUE_CALLER = {"BV6T-AF": 0x27460, "CV6T-AH": 0x2955E,
                 "CV6T-AR": 0x2B054}
# Immediate producer/helper called before the multiply.  BV6T has one extra
# pre-helper (P:$27346); its second helper is the shape-match for AH/AR.
TORQUE_HELPER = {"BV6T-AF": 0x273BE, "CV6T-AH": 0x294CB,
                 "CV6T-AR": 0x2AF90}
TORQUE_PREHELPER_BV = 0x27346
TORQUE_FN_CALLEE = {"BV6T-AF": 0x27422, "CV6T-AH": 0x2952F,
                    "CV6T-AR": 0x2B00B}
# Discovered direct callees of STATE_FN; each ends at its first RTS, 104 words.
STATE_CALLEE = {"BV6T-AF": 0x0ECA6, "CV6T-AH": 0x0F618,
                "CV6T-AR": 0x0FACE}

TABLE = base.load_table()
IMGS = {n: sc.Img(n) for n in BUILDS}


@dataclass
class Insn:
    addr: int
    words: list[int]
    text: str
    flow: str
    target: int | None
    token: str


def is_call_word(w: int) -> bool:
    return (w & 0xFFF4) == 0xE254 or (w & 0xFFFC) == 0xE26C


def target_call(img, a: int) -> int | None:
    d = flow.decode(img, a, TABLE)
    return d.get("target") if d.get("flow") == "call" else None


def canonical(img, a: int, d: dict) -> str:
    """Compile/relocation-tolerant semantic instruction token."""
    w = img.word(a)
    m = d["text"].split()[0].upper() if d["text"].split() else "?"
    if d["flow"] == "call":
        return "CALL"
    if d["flow"] in ("cond", "jump"):
        # Preserve branch condition/type, discard relocated displacement.
        return "BR:" + m
    if m in ("RTS", "RTSD", "RTI", "RTID", "FRTID"):
        return m
    ws = [img.word(a + k) for k in range(d["len"])]
    # Absolute data addresses are relocations. The opcode still preserves the
    # operation, register and (for E68n) the small immediate being stored.
    if "X:$" in d["text"] or "Y:$" in d["text"]:
        return f"OP:{w:04X}:MEM:{d['len']}"
    # Immediate constants are semantic and therefore remain in the token.
    return "RAW:" + ":".join(f"{x:04X}" for x in ws)


def decode_linear(name: str, start: int, end: int,
                  skip: tuple[int, int] | None = None) -> list[Insn]:
    img = IMGS[name]
    out = []
    a = start
    while a <= end:
        if skip and a == skip[0]:
            a = skip[1]
            continue
        d = flow.decode(img, a, TABLE)
        ln = max(1, d["len"])
        ws = [img.word(a + k) for k in range(ln)]
        out.append(Insn(a, ws, d["text"], d["flow"], d.get("target"),
                        canonical(img, a, d)))
        a += ln
    if a != end + 1:
        raise AssertionError(f"{name}: decode crossed region end: {a:X} != {end+1:X}")
    return out


def decode_n(name: str, start: int, count: int) -> list[Insn]:
    img = IMGS[name]
    out = []
    a = start
    for _ in range(count):
        d = flow.decode(img, a, TABLE)
        ln = max(1, d["len"])
        ws = [img.word(a + k) for k in range(ln)]
        out.append(Insn(a, ws, d["text"], d["flow"], d.get("target"),
                        canonical(img, a, d)))
        a += ln
    return out


def dispatch_targets(name: str, start: int, count: int) -> list[int]:
    """Decode compiler switch table stored as (low16, high3) word pairs."""
    img = IMGS[name]
    a = start
    while a < start + 80:
        d = flow.decode(img, a, TABLE)
        if d["text"].startswith("JMP") and d.get("target") is None:
            p = a + d["len"]
            targets = [((img.word(p + 2*i + 1) & 7) << 16) |
                       img.word(p + 2*i) for i in range(count)]
            if not all(start <= t < start + 1000 for t in targets):
                raise AssertionError((name, start, targets))
            return targets
        a += max(1, d["len"])
    raise AssertionError(f"{name}: dispatch table not found at {start:05X}")


def state_records(name: str) -> list[Insn]:
    lo, hi = STATE_FN[name]
    img = IMGS[name]
    # Header ends in JMP (N), followed by a 9-entry, two-word address table.
    a = lo
    while a <= hi:
        d = flow.decode(img, a, TABLE)
        if d["text"].startswith("JMP") and d.get("target") is None:
            table_lo = a + d["len"]
            table_hi = table_lo + 18
            return decode_linear(name, lo, hi, (table_lo, table_hi))
        a += max(1, d["len"])
    raise AssertionError(f"{name}: no indirect state dispatch")


def to_rts(name: str, start: int, max_words: int = 1000) -> list[Insn]:
    img = IMGS[name]
    a = start
    while a < start + max_words and img.word(a) != 0xE708:
        d = flow.decode(img, a, TABLE)
        a += max(1, d["len"])
    if img.word(a) != 0xE708:
        raise AssertionError(f"{name}: RTS not found after {start:X}")
    return decode_linear(name, start, a)


def find_consumers(name: str) -> list[int]:
    img = IMGS[name]
    lane = STATE_CELLS[name]["lane"]
    hits = []
    for s, words in img.spans:
        for i in range(len(words) - 3):
            if words[i:i+3] == [0xF07C, lane, 0x4C04]:
                hits.append(s + i)
    return hits


def call_sites(records: list[Insn]) -> list[tuple[int, int]]:
    return [(r.addr, r.target) for r in records
            if r.flow == "call" and r.target is not None]


def x_operand(r: Insn) -> int | None:
    m = re.search(r"X:\$([0-9A-Fa-f]+)", r.text)
    return int(m.group(1), 16) if m else None


def align(a: list[Insn], b: list[Insn]) -> dict:
    sm = difflib.SequenceMatcher(None, [x.token for x in a],
                                 [x.token for x in b], autojunk=False)
    equal = inserted = deleted = replaced = 0
    edits = []
    addr_deltas = collections.Counter()
    aligned_pairs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            equal += i2 - i1
            for x, y in zip(a[i1:i2], b[j1:j2]):
                aligned_pairs.append((x, y))
                xa, xb = x_operand(x), x_operand(y)
                if xa is not None and xb is not None:
                    addr_deltas[xb - xa] += 1
        else:
            deleted += i2 - i1
            inserted += j2 - j1
            if tag == "replace":
                replaced += max(i2 - i1, j2 - j1)
            edits.append({
                "tag": tag,
                "a": [{"addr": f"0x{x.addr:05X}", "text": x.text,
                       "words": " ".join(f"{w:04X}" for w in x.words)}
                      for x in a[i1:i2]],
                "b": [{"addr": f"0x{x.addr:05X}", "text": x.text,
                       "words": " ".join(f"{w:04X}" for w in x.words)}
                      for x in b[j1:j2]],
            })
    return {
        "a_insn": len(a), "b_insn": len(b), "equal": equal,
        "deleted": deleted, "inserted": inserted, "replaced_span": replaced,
        "edits": edits,
        "xaddr_delta_hist": {f"{k:+#x}": v for k, v in addr_deltas.most_common()},
        "aligned_pairs": aligned_pairs,
    }


def serialise_alignment(x: dict) -> dict:
    return {k: v for k, v in x.items() if k != "aligned_pairs"}


def raw_word_diffs(a: list[Insn], b: list[Insn], alignment: dict) -> dict:
    """Classify raw differences only *after* semantic alignment."""
    relocation = call_target = branch_disp = other = same = 0
    examples = []
    for x, y in alignment["aligned_pairs"]:
        if x.words == y.words:
            same += 1
            continue
        if x.flow == "call":
            call_target += 1
            kind = "call target relocation"
        elif x.flow in ("cond", "jump"):
            branch_disp += 1
            kind = "branch displacement relocation"
        elif x_operand(x) is not None and x_operand(y) is not None and x.words[0] == y.words[0]:
            relocation += 1
            kind = "X-address relocation"
        else:
            other += 1
            kind = "OTHER"
        if len(examples) < 12:
            examples.append({"kind": kind, "a": f"0x{x.addr:05X} " +
                             " ".join(f"{w:04X}" for w in x.words),
                             "b": f"0x{y.addr:05X} " +
                             " ".join(f"{w:04X}" for w in y.words)})
    return {"same": same, "xaddr_relocation": relocation,
            "call_target_relocation": call_target,
            "branch_displacement": branch_disp, "other": other,
            "examples": examples}


def long_branch_rts_check(name: str, recs: list[Insn]) -> list[dict]:
    """The shared decoder uses PC+2 for long relative branches; this routine's
    compiler idiom proves PC+1: every E16C exit lands on the final RTS then.
    Record both so the report does not silently trust the disputed decoder.
    """
    img = IMGS[name]
    rts = STATE_FN[name][1]
    out = []
    for r in recs:
        w = r.words[0]
        if (w & 0xFFFC) != 0xE16C:
            continue
        off = ((w & 3) << 16) | img.word(r.addr + 1)
        if off & 0x20000:
            off -= 0x40000
        out.append({"site": f"0x{r.addr:05X}",
                    "pc_plus_1": f"0x{(r.addr + 1 + off) & 0xFFFFF:05X}",
                    "pc_plus_2": f"0x{(r.addr + 2 + off) & 0xFFFFF:05X}",
                    "rts": f"0x{rts:05X}"})
    return out


def main() -> int:
    state = {n: state_records(n) for n in BUILDS}
    torque = {n: to_rts(n, TORQUE_FN[n]) for n in BUILDS}
    caller = {n: to_rts(n, TORQUE_CALLER[n]) for n in BUILDS}
    helper = {n: to_rts(n, TORQUE_HELPER[n]) for n in BUILDS}
    torque_callee = {n: to_rts(n, TORQUE_FN_CALLEE[n]) for n in BUILDS}
    callee = {n: to_rts(n, STATE_CALLEE[n]) for n in BUILDS}

    for n in BUILDS:
        h = find_consumers(n)
        assert len(h) == 4, (n, h)
        assert all(STATE_FN[n][0] <= x <= STATE_FN[n][1] for x in h)
        assert len(callee[n]) > 1 and sum(len(r.words) for r in callee[n]) == 104
    # 107-word body cited in the consolidated report, plus the final RTS.
    assert [sum(len(r.words) for r in torque[n]) for n in BUILDS] == [108, 108, 108]

    groups = {"state_machine": state, "state_immediate_callee": callee,
              "torque_caller": caller, "torque_caller_helper": helper,
              "torque_function": torque,
              "torque_function_callee": torque_callee}
    result = {"builds": list(BUILDS), "consumers": {}, "groups": {},
              "long_branch_exit_check": {},
              "bv_extra_prehelper": {"entry": f"0x{TORQUE_PREHELPER_BV:05X}"},
              "consumer_neighborhoods": {}, "active_mode_dispatch": {},
              "multiply_shift": {}}
    for n in BUILDS:
        result["consumers"][n] = [f"0x{x:05X}" for x in find_consumers(n)]
        result["long_branch_exit_check"][n] = long_branch_rts_check(n, state[n])
        result["consumer_neighborhoods"][n] = [
            {"entry": f"0x{x:05X}",
             "tokens": [r.token for r in decode_n(n, x, 9)]}
            for x in find_consumers(n)]
        ht = dispatch_targets(n, TORQUE_HELPER[n], 6)
        tt = dispatch_targets(n, TORQUE_FN_CALLEE[n], 6)
        result["active_mode_dispatch"][n] = {
            "helper_code2": f"0x{ht[2]:05X}", "helper_code5": f"0x{ht[5]:05X}",
            "torque_callee_code2": f"0x{tt[2]:05X}",
            "torque_callee_code5": f"0x{tt[5]:05X}",
            "helper_code2_head": [r.text for r in decode_n(n, ht[2], 3)],
            "helper_code5_head": [r.text for r in decode_n(n, ht[5], 3)],
            "torque_callee_code2_head": [r.text for r in decode_n(n, tt[2], 2)],
            "torque_callee_code5_head": [r.text for r in decode_n(n, tt[5], 2)],
        }
        shifts = [(r.addr, r.text) for r in caller[n]
                  if r.text.startswith("ASRR.L")]
        result["multiply_shift"][n] = [[f"0x{a:05X}", t] for a, t in shifts]

    # The first nine decoded instructions cover lane test, gate test and the
    # condition before the writes. They must match at every consumer.
    neigh = result["consumer_neighborhoods"]
    for i in range(4):
        assert neigh["BV6T-AF"][i]["tokens"] == neigh["CV6T-AH"][i]["tokens"] \
            == neigh["CV6T-AR"][i]["tokens"]

    for g, records in groups.items():
        result["groups"][g] = {}
        for other in ("CV6T-AH", "BV6T-AF"):
            al = align(records[other], records["CV6T-AR"])
            result["groups"][g][f"{other}_to_CV6T-AR"] = {
                "alignment": serialise_alignment(al),
                "post_alignment_raw_classification": raw_word_diffs(
                    records[other], records["CV6T-AR"], al),
                "calls_a": [[f"0x{a:05X}", f"0x{t:05X}"] for a, t in call_sites(records[other])],
                "calls_b": [[f"0x{a:05X}", f"0x{t:05X}"] for a, t in call_sites(records["CV6T-AR"])],
            }

    jp = os.path.join(HERE, "semantic_compare.json")
    with open(jp, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    lines = []
    lines.append("STRUCTURAL SEMANTIC COMPARISON")
    for n in BUILDS:
        lines.append(f"{n} LCA consumers: {', '.join(result['consumers'][n])}")
    for g in groups:
        lines.append(f"\n[{g}]")
        for other in ("CV6T-AH", "BV6T-AF"):
            z = result["groups"][g][f"{other}_to_CV6T-AR"]
            a = z["alignment"]
            r = z["post_alignment_raw_classification"]
            lines.append(f"  {other} -> CV6T-AR: {a['a_insn']} vs {a['b_insn']} insns; "
                         f"semantic equal {a['equal']}; edits {len(a['edits'])}; "
                         f"raw-after-align other={r['other']}")
            lines.append(f"    X-address deltas: {a['xaddr_delta_hist']}")
            if a["edits"]:
                for e in a["edits"]:
                    lines.append(f"    {e['tag']}: A={e['a']} B={e['b']}")
    tp = os.path.join(HERE, "semantic_compare.txt")
    with open(tp, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nWROTE {jp}\nWROTE {tp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
