#!/usr/bin/env python3
"""Emit and statically verify the stateless FD22 snapshot handler.

Design only: reads immutable OEM images and prints raw P-space words.  It does
not modify a BIN/VBF and does not access CAN.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BLK0 = ROOT / "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin"
BLK1 = ROOT / "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin"
BLK2 = ROOT / "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk2_0x04008C00.bin"
EXPECTED_SHA256 = {
    BLK0.name: "21d095f6ce8496695951a6ad2f012d428da87ea21c781b4f4a4d0992994e5074",
    BLK1.name: "6e60963a3583993d1b872ebd88bb9b2f1acdeb397a7954a1ae75be3ce7d933b0",
    BLK2.name: "4e65a6493cf770d02a125c115a50c7b6b8d58969405bbd0292fafd896750d6c0",
}
P_BASES = ((BLK0, 0x00000), (BLK1, 0x0E000))
X_BASE = 0x04600
HANDLER_BASE = 0x33800
SOURCES = (0x2DDE, 0x2DB9, 0x2D53, 0x2D52, 0x1CB1, 0x171B, 0x2D54)
EXPECTED_OUTPUT = (
    (0, "status/format", None, "constant zero"),
    (1, "lane_state_hi", 0x2DDE, "high"),
    (2, "lane_state_lo", 0x2DDE, "low"),
    (3, "per_state_code_hi", 0x2DB9, "high"),
    (4, "per_state_code_lo", 0x2DB9, "low"),
    (5, "torque_accumulator_hi", 0x2D53, "high"),
    (6, "torque_accumulator_lo", 0x2D53, "low"),
    (7, "torque_output_hi", 0x2D52, "high"),
    (8, "torque_output_lo", 0x2D52, "low"),
    (9, "fd0e_raw_hi", 0x1CB1, "high"),
    (10, "fd0e_raw_lo", 0x1CB1, "low"),
    (11, "fd0c_raw_hi", 0x171B, "high"),
    (12, "fd0c_raw_lo", 0x171B, "low"),
    (13, "upstream_2d54_hi", 0x2D54, "high"),
    (14, "upstream_2d54_lo", 0x2D54, "low"),
)
PERMITTED_VOLATILE = frozenset({"A", "B", "X0", "Y0", "R0", "R1", "CC"})


def read_words(path: Path) -> list[int]:
    data = path.read_bytes()
    assert not (len(data) & 1), f"odd byte count: {path}"
    return list(struct.unpack(f"<{len(data) // 2}H", data))


class Image:
    def __init__(self) -> None:
        self.p = [(base, read_words(path)) for path, base in P_BASES]
        self.x = read_words(BLK2)

    def pword(self, address: int) -> int:
        for base, data in self.p:
            if base <= address < base + len(data):
                return data[address - base]
        raise KeyError(f"P:${address:05X}")

    def pwords(self, address: int, count: int) -> list[int]:
        return [self.pword(address + i) for i in range(count)]

    def xwords(self, address: int, count: int) -> list[int]:
        offset = address - X_BASE
        assert 0 <= offset <= len(self.x) - count
        return self.x[offset : offset + count]

    def find_pair(self, opcode: int, extension: int) -> list[int]:
        found: list[int] = []
        for base, data in self.p:
            for index in range(len(data) - 1):
                if data[index : index + 2] == [opcode, extension]:
                    found.append(base + index)
        return found


@dataclass(frozen=True)
class Insn:
    address: int
    words: tuple[int, ...]
    text: str
    reads: frozenset[str] = frozenset()
    writes: frozenset[str] = frozenset()
    memory_read: int | None = None
    memory_write: tuple[str, int] | None = None
    cycles_upper: int = 1
    terminal: bool = False


def build_words() -> list[int]:
    # Status/format version 0, then seven raw words in explicit network order.
    result = [0xE080, 0xD0B6]  # MOVE.W #0,A; MOVE.BP A1,X:(R2)
    for index, source in enumerate(SOURCES):
        high_offset = 1 + 2 * index
        low_offset = high_offset + 1
        result += [
            0xF07C, source,             # MOVE.W X:$source,A
            0x8110,                     # MOVE.W A1,B1 (retain the same sample)
            0x5C28,                     # LSRR.W #8,A
            0xD0E6, high_offset,        # MOVE.BP A1,X:(R2+high_offset)
            0xD1E6, low_offset,         # MOVE.BP B1,X:(R2+low_offset)
        ]
    result += [0xE58F, 0xE708]  # MOVE.W #15,Y0; RTS
    return result


def decode(raw: list[int], base: int = HANDLER_BASE) -> list[Insn]:
    out: list[Insn] = []
    i = 0
    while i < len(raw):
        address = base + i
        op = raw[i]
        if op == 0xE080:
            ins = Insn(address, (op,), "MOVE.W #0,A", writes=frozenset({"A", "CC"}))
        elif op == 0xD0B6:
            ins = Insn(address, (op,), "MOVE.BP A1,X:(R2)",
                        reads=frozenset({"A", "R2"}),
                        memory_write=("R2_scratch", 0), cycles_upper=2)
        elif op == 0xF07C:
            assert i + 1 < len(raw), "truncated absolute X load"
            source = raw[i + 1]
            ins = Insn(address, (op, source), f"MOVE.W X:${source:04X},A",
                        writes=frozenset({"A", "CC"}), memory_read=source,
                        cycles_upper=2)
        elif op == 0x8110:
            ins = Insn(address, (op,), "MOVE.W A1,B1",
                        reads=frozenset({"A"}), writes=frozenset({"B"}))
        elif op == 0x5C28:
            ins = Insn(address, (op,), "LSRR.W #8,A",
                        reads=frozenset({"A"}), writes=frozenset({"A", "CC"}))
        elif op == 0xD0E6:
            assert i + 1 < len(raw), "truncated displaced byte-pointer store"
            offset = raw[i + 1]
            ins = Insn(address, (op, offset), f"MOVE.BP A1,X:(R2+${offset:04X})",
                        reads=frozenset({"A", "R2"}),
                        memory_write=("R2_scratch", offset), cycles_upper=2)
        elif op == 0xD1E6:
            assert i + 1 < len(raw), "truncated displaced byte-pointer store"
            offset = raw[i + 1]
            ins = Insn(address, (op, offset), f"MOVE.BP B1,X:(R2+${offset:04X})",
                        reads=frozenset({"B", "R2"}),
                        memory_write=("R2_scratch", offset), cycles_upper=2)
        elif op == 0xE58F:
            ins = Insn(address, (op,), "MOVE.W #15,Y0", writes=frozenset({"Y0", "CC"}))
        elif op == 0xE708:
            ins = Insn(address, (op,), "RTS", cycles_upper=8, terminal=True)
        else:
            raise AssertionError(f"unknown/non-permitted opcode {op:04X} at P:${address:05X}")
        out.append(ins)
        i += len(ins.words)
    assert sum(len(ins.words) for ins in out) == len(raw)
    return out


def verify_oem_templates(img: Image) -> None:
    # Exact immutable templates, independent of the generated handler.
    assert img.pwords(0x2075B, 2) == [0xE080, 0xD0B6]  # zero status + R2 BP store
    assert img.pwords(0x20712, 4) == [0xF014, 0x5C28, 0xE58C, 0xD0B6]
    # A->B transfer semantics are independently fixed by this OEM min/select
    # idiom: loads B then A, conditionally executes 8110, and stores B1.
    assert img.pwords(0x13B32, 15) == [
        0xF17C, 0x0665, 0xF07C, 0x066D, 0x7886, 0xA701, 0x8110,
        0xF07C, 0x0675, 0x7886, 0xA701, 0x8110, 0xD17C, 0x17B0, 0xE708,
    ]
    # FD08 independently pairs shifted A1 (high byte) with retained B1 (low).
    assert img.pwords(0x2046C, 6) == [0x5C68, 0xE582, 0xD0B6, 0xD1E6, 1, 0xE708]
    assert (0xD0E6 ^ 0xD1E6) == 0x0100  # accumulator selector only; EA form unchanged
    assert img.pwords(0x1059D, 2) == [0xE58F, 0xE708]  # OEM FD22 length + return
    assert img.pwords(0x0EEA2, 6) == [0xFD22, 0, 0x058F, 1, 0, 0]
    assert img.xwords(0x6388, 4) == [0xFD22, 0, 1, 15]

    # FD20 independently establishes D0E6's 16-bit R2 byte displacement: its
    # twelve stores cover offsets 0..11.  The generated 12..14 extensions use
    # that same unmodified opcode/extension encoding.
    span = img.pwords(0x20710, 0x20747 - 0x20710)
    displaced = {
        span[i + 1] for i in range(len(span) - 1)
        if span[i] == 0xD0E6
    }
    assert displaced == set(range(1, 12)), displaced

    # The exact absolute-X-load opcode is independently present with every
    # chosen extension word in OEM code (not inferred from an assembler).
    for source in SOURCES:
        hits = img.find_pair(0xF07C, source)
        assert hits, f"no OEM F07C {source:04X} template"


def verify() -> tuple[list[int], list[Insn], int]:
    for path in (BLK0, BLK1, BLK2):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == EXPECTED_SHA256[path.name], (path, digest)
    img = Image()
    verify_oem_templates(img)

    raw = build_words()
    insns = decode(raw)
    assert len(raw) == 60
    assert img.pwords(HANDLER_BASE, len(raw)) == [0xE70A] * len(raw), "cave not OEM fill"

    # Finite straight-line CFG: exactly one terminal, at the end; no decoder
    # admits a branch, call, loop, indirect transfer, or fall-through past RTS.
    assert sum(ins.terminal for ins in insns) == 1
    assert insns[-1].terminal
    assert not any(ins.terminal for ins in insns[:-1])

    stores = [ins for ins in insns if ins.memory_write is not None]
    assert len(stores) == 15
    assert [ins.memory_write for ins in stores] == [
        ("R2_scratch", offset) for offset in range(15)
    ]
    assert not any(ins.memory_write and ins.memory_write[0] != "R2_scratch" for ins in insns)

    # Symbolically execute the byte-producing subset and prove byte provenance.
    a: tuple[str, int | None] | None = None
    b: tuple[str, int | None] | None = None
    emitted: list[tuple[int, str, int | None, str]] = []
    for ins in insns:
        op = ins.words[0]
        if op == 0xE080:
            a = ("constant", 0)
        elif op == 0xF07C:
            a = ("word", ins.words[1])
        elif op == 0x8110:
            assert a and a[0] == "word"
            b = a
        elif op == 0x5C28:
            assert a and a[0] == "word"
            a = ("high", a[1])
        elif op in (0xD0B6, 0xD0E6, 0xD1E6):
            value = b if op == 0xD1E6 else a
            assert value is not None and ins.memory_write is not None
            offset = ins.memory_write[1]
            if value[0] == "constant":
                emitted.append((offset, "status/format", None, "constant zero"))
            else:
                emitted.append((offset, "", value[1], "high" if value[0] == "high" else "low"))
    expected_provenance = [(o, s, p) for o, _n, s, p in EXPECTED_OUTPUT]
    actual_provenance = [(o, s, p) for o, _n, s, p in emitted]
    assert actual_provenance == expected_provenance, (actual_provenance, expected_provenance)
    assert [ins.memory_read for ins in insns if ins.memory_read is not None] == list(SOURCES)

    assert insns[-2].words == (0xE58F,) and insns[-2].text == "MOVE.W #15,Y0"
    assert not any("SP" in ins.reads | ins.writes for ins in insns), "stack touched"
    clobbers = set().union(*(ins.writes for ins in insns))
    assert clobbers <= PERMITTED_VOLATILE, clobbers - PERMITTED_VOLATILE
    assert clobbers == {"A", "B", "Y0", "CC"}
    assert not ({"R2", "C", "D", "R3", "R4", "R5", "N", "Y1"} & clobbers)

    cycles_upper = sum(ins.cycles_upper for ins in insns)
    assert cycles_upper == 68
    return raw, insns, cycles_upper


def main() -> None:
    raw, insns, cycles_upper = verify()
    print(f"FD22_STATELESS_SNAPSHOT P:${HANDLER_BASE:05X} {len(raw)} words")
    for start in range(0, len(raw), 8):
        chunk = raw[start : start + 8]
        print(f"P:${HANDLER_BASE + start:05X}  " + " ".join(f"{word:04X}" for word in chunk))
    print("\nDECODE")
    for ins in insns:
        print(f"P:${ins.address:05X}  {' '.join(f'{w:04X}' for w in ins.words):9s}  {ins.text}")
    print(f"\nPASS: 15 stores; Y0=15; stack balanced; R2/C preserved; straight-line; <= {cycles_upper} cycles")


if __name__ == "__main__":
    main()
