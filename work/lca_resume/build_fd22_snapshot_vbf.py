#!/usr/bin/env python3
"""Deterministically build CV6T-14C217-AR_FD22_SNAPSHOT.VBF.

Offline only. The builder refuses non-OEM inputs by whole-file SHA-256, applies
expect-before-set checks at every edit site, repairs internal checksum word A
then word B, repairs the touched VBF block CRC, and finally repairs the header
file checksum. It never writes either input file.
"""
from __future__ import annotations

import argparse
import binascii
import hashlib
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_STOCK = ROOT / "CV6T-14C217-AR.VBF"
DEFAULT_CAL = ROOT / "CV6T-14C218-AX.VBF"
DEFAULT_OUTPUT = ROOT / "CV6T-14C217-AR_FD22_SNAPSHOT.VBF"

OEM_STOCK_SHA256 = "cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5"
OEM_CAL_SHA256 = "6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7"
OEM_BLOCK_SHA256 = {
    0x00000000: "21d095f6ce8496695951a6ad2f012d428da87ea21c781b4f4a4d0992994e5074",
    0x0001C000: "6e60963a3583993d1b872ebd88bb9b2f1acdeb397a7954a1ae75be3ce7d933b0",
    0x04008C00: "4e65a6493cf770d02a125c115a50c7b6b8d58969405bbd0292fafd896750d6c0",
}

BLK0_FLASH = 0x00000000
BLK1_FLASH = 0x0001C000
BLK1_PWORD_BASE = 0x0E000
POINTER_PWORD = 0x0EEA4
HANDLER_PWORD = 0x33800
POINTER_OFF = (POINTER_PWORD - BLK1_PWORD_BASE) * 2
HANDLER_OFF = (HANDLER_PWORD - BLK1_PWORD_BASE) * 2
WORD_A_OFF = 0x63FEA
WORD_B_OFF = 0x63FEC
SUM_B_END = 0x0007FFEC

OEM_POINTER_WORDS = (0x058F, 0x0001)
NEW_POINTER_WORDS = (0x3800, 0x0003)
OEM_CAVE_WORD = 0xE70A
OEM_WORD_A = 0xD110
OEM_WORD_B = 0x3216

HANDLER_WORDS = (
    0xE080, 0xD0B6, 0xF07C, 0x2DDE, 0x8110, 0x5C28, 0xD0E6, 0x0001,
    0xD1E6, 0x0002, 0xF07C, 0x2DB9, 0x8110, 0x5C28, 0xD0E6, 0x0003,
    0xD1E6, 0x0004, 0xF07C, 0x2D53, 0x8110, 0x5C28, 0xD0E6, 0x0005,
    0xD1E6, 0x0006, 0xF07C, 0x2D52, 0x8110, 0x5C28, 0xD0E6, 0x0007,
    0xD1E6, 0x0008, 0xF07C, 0x1CB1, 0x8110, 0x5C28, 0xD0E6, 0x0009,
    0xD1E6, 0x000A, 0xF07C, 0x171B, 0x8110, 0x5C28, 0xD0E6, 0x000B,
    0xD1E6, 0x000C, 0xF07C, 0x2D54, 0x8110, 0x5C28, 0xD0E6, 0x000D,
    0xD1E6, 0x000E, 0xE58F, 0xE708,
)

FILE_CHECKSUM_RE = re.compile(rb"file_checksum\s*=\s*0x([0-9A-Fa-f]+)")


class BuildError(RuntimeError):
    """A safety assertion rejected the build."""


class Block(NamedTuple):
    address: int
    data: bytes
    header_offset: int
    data_offset: int
    crc_offset: int
    stored_crc: int


class ParsedVbf:
    def __init__(self, path: Path, raw: bytes, data_start: int,
                 blocks: tuple[Block, ...], checksum_span: tuple[int, int]):
        self.path = path
        self.raw = raw
        self.data_start = data_start
        self.blocks = blocks
        self.checksum_span = checksum_span

    def block_at(self, address: int) -> Block:
        matches = [block for block in self.blocks if block.address == address]
        if len(matches) != 1:
            raise BuildError(f"{self.path}: expected one block at 0x{address:08X}")
        return matches[0]


class BuildResult(NamedTuple):
    output: bytes
    old_word_a: int
    new_word_a: int
    old_word_b: int
    new_word_b: int
    old_block_crc: int
    new_block_crc: int
    old_file_checksum: int
    new_file_checksum: int


class Audit(NamedTuple):
    pointer_words_changed: int
    cave_words_changed: int
    checksum_words_changed: int
    word_a_valid: bool
    word_b_valid: bool
    unexpected_payload_bytes: int
    unexpected_header_bytes: int


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def crc16_mcrf4xx(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def sum16le(data: bytes) -> int:
    return sum(struct.unpack_from("<H", data, offset)[0]
               for offset in range(0, len(data) - 1, 2)) & 0xFFFF


def expect_bytes(data: bytes | bytearray, offset: int, expected: bytes, label: str) -> None:
    found = bytes(data[offset:offset + len(expected)])
    if found != expected:
        raise BuildError(
            f"{label} expectation failed at +0x{offset:X}: "
            f"found {found.hex().upper()}, expected {expected.hex().upper()}")


def _header_end(raw: bytes) -> int:
    depth = 0
    entered = False
    for offset, byte in enumerate(raw):
        if byte == ord("{"):
            depth += 1
            entered = True
        elif byte == ord("}") and entered:
            depth -= 1
            if depth == 0:
                return offset + 1
    raise BuildError("VBF header has no balanced outer braces")


def parse_vbf_bytes(raw: bytes, path: Path) -> ParsedVbf:
    header_end = _header_end(raw)
    candidates: list[tuple[int, tuple[Block, ...]]] = []
    for data_start in range(header_end, min(header_end + 8, len(raw))):
        offset = data_start
        blocks: list[Block] = []
        valid = True
        while offset < len(raw):
            if offset + 10 > len(raw):
                valid = False
                break
            address, length = struct.unpack_from(">II", raw, offset)
            data_offset = offset + 8
            crc_offset = data_offset + length
            if crc_offset + 2 > len(raw):
                valid = False
                break
            stored_crc = struct.unpack_from(">H", raw, crc_offset)[0]
            blocks.append(Block(address, raw[data_offset:crc_offset], offset,
                                data_offset, crc_offset, stored_crc))
            offset = crc_offset + 2
        if valid and offset == len(raw) and blocks:
            candidates.append((data_start, tuple(blocks)))
    if len(candidates) != 1:
        raise BuildError(f"{path}: expected one exact VBF block walk, found {len(candidates)}")
    data_start, blocks = candidates[0]
    match = FILE_CHECKSUM_RE.search(raw[:data_start])
    if not match:
        raise BuildError(f"{path}: file_checksum field not found")
    return ParsedVbf(path, raw, data_start, blocks, match.span(1))


def parse_vbf(path: Path) -> ParsedVbf:
    try:
        return parse_vbf_bytes(path.read_bytes(), path)
    except OSError as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc


def verify_container(parsed: ParsedVbf) -> None:
    for block in parsed.blocks:
        calculated = binascii.crc_hqx(block.data, 0xFFFF)
        if calculated != block.stored_crc:
            raise BuildError(
                f"{parsed.path}: block 0x{block.address:08X} CRC mismatch: "
                f"stored {block.stored_crc:04X}, calculated {calculated:04X}")
    stored_file = int(parsed.raw[slice(*parsed.checksum_span)], 16)
    calculated_file = binascii.crc32(parsed.raw[parsed.data_start:]) & 0xFFFFFFFF
    if stored_file != calculated_file:
        raise BuildError(
            f"{parsed.path}: file checksum mismatch: stored {stored_file:08X}, "
            f"calculated {calculated_file:08X}")


def verify_oem_inputs(stock: ParsedVbf, calibration: ParsedVbf) -> None:
    actual_stock = sha256(stock.raw)
    actual_cal = sha256(calibration.raw)
    if actual_stock != OEM_STOCK_SHA256:
        raise BuildError(f"stock OEM SHA-256 mismatch: {actual_stock} != {OEM_STOCK_SHA256}")
    if actual_cal != OEM_CAL_SHA256:
        raise BuildError(f"calibration OEM SHA-256 mismatch: {actual_cal} != {OEM_CAL_SHA256}")
    verify_container(stock)
    verify_container(calibration)
    for address, expected in OEM_BLOCK_SHA256.items():
        actual = sha256(stock.block_at(address).data)
        if actual != expected:
            raise BuildError(
                f"stock block 0x{address:08X} SHA-256 mismatch: {actual} != {expected}")
    if [block.address for block in stock.blocks] != [0, BLK1_FLASH, 0x04008C00]:
        raise BuildError("unexpected stock block map")
    if len(calibration.blocks) != 1 or calibration.blocks[0].address != 0x00009800:
        raise BuildError("unexpected paired calibration block map")
    if sum16le(calibration.blocks[0].data) != 0xFFFF:
        raise BuildError("paired calibration internal sum is not 0xFFFF")


def find_start_offset(block1: bytes) -> int:
    end_immediate = struct.pack("<HH", 0xFFF5, 0x0003)
    hits = [match.start() for match in re.finditer(re.escape(end_immediate), block1)]
    starts: set[int] = set()
    for hit in hits:
        for offset in range(max(0, hit - 64), min(len(block1) - 3, hit + 64), 2):
            low, high = struct.unpack_from("<HH", block1, offset)
            value = (high << 16) | low
            if 0x0000E000 <= value <= 0x0000F000:
                relative = value * 2 - BLK1_FLASH
                if 0 <= relative < WORD_A_OFF:
                    starts.add(relative)
    if starts != {0x1800}:
        raise BuildError(f"self-check START expectation failed: found {sorted(starts)}")
    return starts.pop()


def calculate_word_a(block0: bytes, block1: bytes) -> int:
    start = find_start_offset(block1)
    return crc16_mcrf4xx(block0 + block1[start:WORD_A_OFF])


def calculate_word_b(stock: ParsedVbf, calibration: ParsedVbf,
                     replacement_block1: bytes) -> int:
    linear = bytearray(b"\xFF" * SUM_B_END)
    for block in stock.blocks:
        data = replacement_block1 if block.address == BLK1_FLASH else block.data
        if block.address < SUM_B_END:
            length = min(len(data), SUM_B_END - block.address)
            linear[block.address:block.address + length] = data[:length]
    for block in calibration.blocks:
        if block.address < SUM_B_END:
            length = min(len(block.data), SUM_B_END - block.address)
            linear[block.address:block.address + length] = block.data[:length]
    return sum16le(linear)


def _u16le(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def build_bytes(stock: ParsedVbf, calibration: ParsedVbf) -> BuildResult:
    verify_oem_inputs(stock, calibration)
    block0 = stock.block_at(BLK0_FLASH).data
    old_block1_record = stock.block_at(BLK1_FLASH)
    block1 = bytearray(old_block1_record.data)

    expect_bytes(block1, POINTER_OFF, struct.pack("<2H", *OEM_POINTER_WORDS),
                 "FD22 callback pointer")
    expect_bytes(block1, HANDLER_OFF, struct.pack("<60H", *([OEM_CAVE_WORD] * 60)),
                 "FD22 handler cave")
    expect_bytes(block1, WORD_A_OFF, struct.pack("<H", OEM_WORD_A), "internal word A")
    expect_bytes(block1, WORD_B_OFF, struct.pack("<H", OEM_WORD_B), "internal word B")

    block1[POINTER_OFF:POINTER_OFF + 4] = struct.pack("<2H", *NEW_POINTER_WORDS)
    block1[HANDLER_OFF:HANDLER_OFF + 120] = struct.pack("<60H", *HANDLER_WORDS)

    old_a = _u16le(block1, WORD_A_OFF)
    new_a = calculate_word_a(block0, block1)
    struct.pack_into("<H", block1, WORD_A_OFF, new_a)

    old_b = _u16le(block1, WORD_B_OFF)
    new_b = calculate_word_b(stock, calibration, bytes(block1))
    struct.pack_into("<H", block1, WORD_B_OFF, new_b)

    output = bytearray(stock.raw)
    output[old_block1_record.data_offset:old_block1_record.crc_offset] = block1
    new_block_crc = binascii.crc_hqx(block1, 0xFFFF)
    struct.pack_into(">H", output, old_block1_record.crc_offset, new_block_crc)

    old_file_checksum = int(stock.raw[slice(*stock.checksum_span)], 16)
    new_file_checksum = binascii.crc32(output[stock.data_start:]) & 0xFFFFFFFF
    checksum_width = stock.checksum_span[1] - stock.checksum_span[0]
    encoded_checksum = f"{new_file_checksum:0{checksum_width}X}".encode("ascii")
    if len(encoded_checksum) != checksum_width:
        raise BuildError("new file checksum does not fit the OEM header field")
    output[slice(*stock.checksum_span)] = encoded_checksum

    return BuildResult(bytes(output), old_a, new_a, old_b, new_b,
                       old_block1_record.stored_crc, new_block_crc,
                       old_file_checksum, new_file_checksum)


def audit_build(stock: ParsedVbf, built: ParsedVbf, calibration: ParsedVbf) -> Audit:
    verify_container(built)
    if built.data_start != stock.data_start:
        raise BuildError("VBF data_start changed")
    if [(b.address, len(b.data), b.header_offset, b.data_offset, b.crc_offset)
            for b in built.blocks] != [
                (b.address, len(b.data), b.header_offset, b.data_offset, b.crc_offset)
                for b in stock.blocks]:
        raise BuildError("VBF block topology or offsets changed")

    stock_b1 = stock.block_at(BLK1_FLASH).data
    built_b1 = built.block_at(BLK1_FLASH).data
    expected_pointer = struct.pack("<2H", *NEW_POINTER_WORDS)
    expected_handler = struct.pack("<60H", *HANDLER_WORDS)
    expect_bytes(built_b1, POINTER_OFF, expected_pointer, "built FD22 callback pointer")
    expect_bytes(built_b1, HANDLER_OFF, expected_handler, "built FD22 handler")

    allowed: set[tuple[int, int]] = set()
    for offset in range(POINTER_OFF, POINTER_OFF + 4):
        allowed.add((BLK1_FLASH, offset))
    for offset in range(HANDLER_OFF, HANDLER_OFF + 120):
        allowed.add((BLK1_FLASH, offset))
    for offset in range(WORD_A_OFF, WORD_A_OFF + 2):
        allowed.add((BLK1_FLASH, offset))
    for offset in range(WORD_B_OFF, WORD_B_OFF + 2):
        allowed.add((BLK1_FLASH, offset))

    differing: set[tuple[int, int]] = set()
    for old_block, new_block in zip(stock.blocks, built.blocks):
        for offset, (old, new) in enumerate(zip(old_block.data, new_block.data)):
            if old != new:
                differing.add((old_block.address, offset))
    unexpected_payload = len(differing - allowed)
    if unexpected_payload:
        raise BuildError(f"payload audit found {unexpected_payload} unexpected changed byte(s)")

    pointer_changed = sum(
        stock_b1[POINTER_OFF + 2 * index:POINTER_OFF + 2 * index + 2] !=
        built_b1[POINTER_OFF + 2 * index:POINTER_OFF + 2 * index + 2]
        for index in range(2))
    cave_changed = sum(
        stock_b1[HANDLER_OFF + 2 * index:HANDLER_OFF + 2 * index + 2] !=
        built_b1[HANDLER_OFF + 2 * index:HANDLER_OFF + 2 * index + 2]
        for index in range(60))
    checksum_changed = sum(
        stock_b1[offset:offset + 2] != built_b1[offset:offset + 2]
        for offset in (WORD_A_OFF, WORD_B_OFF))
    if pointer_changed != 2 or cave_changed != 60:
        raise BuildError(
            f"word diff cardinality mismatch: pointer={pointer_changed}, cave={cave_changed}")

    word_a_valid = _u16le(built_b1, WORD_A_OFF) == calculate_word_a(
        built.block_at(BLK0_FLASH).data, built_b1)
    word_b_valid = _u16le(built_b1, WORD_B_OFF) == calculate_word_b(
        built, calibration, built_b1)
    if not word_a_valid or not word_b_valid:
        raise BuildError(
            f"internal checksum audit failed: word_A={word_a_valid}, word_B={word_b_valid}")

    checksum_bytes = set(range(*stock.checksum_span))
    header_diffs = {offset for offset in range(stock.data_start)
                    if stock.raw[offset] != built.raw[offset]}
    unexpected_header = len(header_diffs - checksum_bytes)
    if unexpected_header:
        raise BuildError(f"header audit found {unexpected_header} unexpected changed byte(s)")

    # Block framing is immutable. Only blk1's CRC field may differ.
    for old_block, new_block in zip(stock.blocks, built.blocks):
        if stock.raw[old_block.header_offset:old_block.data_offset] != \
                built.raw[new_block.header_offset:new_block.data_offset]:
            raise BuildError(f"block 0x{old_block.address:08X} framing changed")
        if old_block.address != BLK1_FLASH and old_block.stored_crc != new_block.stored_crc:
            raise BuildError(f"untouched block 0x{old_block.address:08X} CRC changed")

    return Audit(pointer_changed, cave_changed, checksum_changed,
                 word_a_valid, word_b_valid, unexpected_payload, unexpected_header)


def selftest(stock_path: Path = DEFAULT_STOCK, cal_path: Path = DEFAULT_CAL) -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, condition: bool) -> None:
        checks.append((name, bool(condition)))
        print(f"  {'PASS' if condition else 'FAIL'}  {name}")

    try:
        check("CRC-16/MCRF4XX reference vector", crc16_mcrf4xx(b"123456789") == 0x6F91)
        check("sum16le reference vector", sum16le(b"\x01\x00\x02\x00") == 3)
        check("handler is exactly 60 words", len(HANDLER_WORDS) == 60)
        stock = parse_vbf(stock_path)
        calibration = parse_vbf(cal_path)
        verify_oem_inputs(stock, calibration)
        check("strict OEM SHA-256 and container checks", True)
        block0 = stock.block_at(BLK0_FLASH).data
        block1 = stock.block_at(BLK1_FLASH).data
        check("AR self-check START is blk1+0x1800", find_start_offset(block1) == 0x1800)
        check("stock word A reproduces", calculate_word_a(block0, block1) == OEM_WORD_A)
        check("stock word B reproduces", calculate_word_b(stock, calibration, block1) == OEM_WORD_B)
        first = build_bytes(stock, calibration)
        second = build_bytes(stock, calibration)
        check("two builds are byte-identical", first.output == second.output)
        built = parse_vbf_bytes(first.output, Path("selftest-output.vbf"))
        audit = audit_build(stock, built, calibration)
        check("exactly 2 pointer words changed", audit.pointer_words_changed == 2)
        check("exactly 60 cave words changed", audit.cave_words_changed == 60)
        check("only permitted payload/header bytes changed",
              audit.unexpected_payload_bytes == audit.unexpected_header_bytes == 0)
        check("internal checksum words A/B are valid", audit.word_a_valid and audit.word_b_valid)
    except (BuildError, OSError, ValueError, struct.error) as exc:
        print(f"  FAIL  exception: {exc}")
        checks.append(("exception", False))
    passed = all(condition for _, condition in checks)
    print(f"SELFTEST: {'ALL PASS' if passed else 'FAILURES'}")
    return 0 if passed else 1


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true", help="run checks without writing output")
    parser.add_argument("--stock", type=Path, default=DEFAULT_STOCK)
    parser.add_argument("--cal", type=Path, default=DEFAULT_CAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest(args.stock, args.cal)
    try:
        output_resolved = args.output.resolve()
        if output_resolved in {args.stock.resolve(), args.cal.resolve()}:
            raise BuildError("output must not overwrite a stock or calibration VBF")
        stock = parse_vbf(args.stock)
        calibration = parse_vbf(args.cal)
        result = build_bytes(stock, calibration)
        built = parse_vbf_bytes(result.output, args.output)
        audit = audit_build(stock, built, calibration)
        write_atomic(args.output, result.output)
        # Read back the exact target before reporting success.
        written = parse_vbf(args.output)
        readback_audit = audit_build(stock, written, calibration)
        if readback_audit != audit or written.raw != result.output:
            raise BuildError("output read-back differs from the audited build")
        print(f"wrote: {args.output}")
        print(f"sha256: {sha256(result.output)}")
        print(f"pointer: 058F 0001 -> 3800 0003 ({audit.pointer_words_changed} words)")
        print(f"handler: P:$33800 ({audit.cave_words_changed} words)")
        print(f"word A: {result.old_word_a:04X} -> {result.new_word_a:04X}")
        print(f"word B: {result.old_word_b:04X} -> {result.new_word_b:04X}")
        print(f"block CRC: {result.old_block_crc:04X} -> {result.new_block_crc:04X}")
        print(f"file checksum: {result.old_file_checksum:08X} -> {result.new_file_checksum:08X}")
        print("AUDIT: only 2 pointer + 60 cave + valid A/B checksum words differ in payload")
        return 0
    except (BuildError, OSError, ValueError, struct.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
