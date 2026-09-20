#!/usr/bin/env python3
"""Independent, read-only verifier for the FD22 snapshot VBF.

This module deliberately has no dependency on the builder, its tests, its VBF
parser, or the handler-word generator.  It reads the three named VBF files,
implements each integrity algorithm locally, and decodes the emitted handler
from the output payload.  It never writes a VBF or binary image.
"""

from __future__ import annotations

import hashlib
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
STOCK_PATH = ROOT / "CV6T-14C217-AR.VBF"
OUTPUT_PATH = ROOT / "CV6T-14C217-AR_FD22_SNAPSHOT.VBF"
CAL_PATH = ROOT / "CV6T-14C218-AX.VBF"

EXPECTED_FILE_SHA256 = {
    STOCK_PATH.name: "cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5",
    OUTPUT_PATH.name: "d4f8970c8c756fab6fe039455f0d0e5fb37156ba229f68410c5034b35dc8f370",
    CAL_PATH.name: "6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7",
}
EXPECTED_STOCK_BLOCK_SHA256 = (
    "21d095f6ce8496695951a6ad2f012d428da87ea21c781b4f4a4d0992994e5074",
    "6e60963a3583993d1b872ebd88bb9b2f1acdeb397a7954a1ae75be3ce7d933b0",
    "4e65a6493cf770d02a125c115a50c7b6b8d58969405bbd0292fafd896750d6c0",
)

FD22_RECORD_P = 0x0EEA2
CAVE_P = 0x33800
CAVE_WORDS = 60
CHECK_A_FLASH = 0x7FFEA
CHECK_B_FLASH = 0x7FFEC
CRC_A_BLOCK1_START = 0x1800
CRC_A_BLOCK1_END = 0x63FEA
SOURCES = (0x2DDE, 0x2DB9, 0x2D53, 0x2D52, 0x1CB1, 0x171B, 0x2D54)


class VerifyError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerifyError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def crc16_ccitt_false(data: bytes) -> int:
    """Bit-at-a-time CCITT-FALSE: poly 0x1021, init 0xffff."""
    crc = 0xFFFF
    for octet in data:
        crc ^= octet << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc32_iso_hdlc(data: bytes) -> int:
    """Bit-at-a-time reflected CRC-32 used by the VBF header."""
    crc = 0xFFFFFFFF
    for octet in data:
        crc ^= octet
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


def crc16_mcrf4xx(data: bytes) -> int:
    """Separate reflected CRC-16/MCRF4XX implementation."""
    state = 0xFFFF
    for octet in data:
        state ^= octet
        bit_count = 8
        while bit_count:
            state = (state >> 1) ^ 0x8408 if state & 1 else state >> 1
            bit_count -= 1
    return state & 0xFFFF


def sum_le16(data: bytes) -> int:
    """Add little-endian words modulo 2^16 without struct iteration."""
    require(len(data) % 2 == 0, "internal-B range is not word aligned")
    total = 0
    for pos in range(0, len(data), 2):
        total = (total + data[pos] + (data[pos + 1] << 8)) & 0xFFFF
    return total


@dataclass(frozen=True)
class Block:
    load: int
    payload: bytes
    crc: int
    frame_offset: int
    data_offset: int
    crc_offset: int


@dataclass(frozen=True)
class VBF:
    path: Path
    raw: bytes
    header_end: int
    data_start: int
    header_text: str
    file_checksum: int
    blocks: tuple[Block, ...]


def matching_header_end(raw: bytes) -> int:
    opening = raw.find(b"{")
    require(opening >= 0, "VBF header has no opening brace")
    depth = 0
    for pos in range(opening, len(raw)):
        if raw[pos] == ord("{"):
            depth += 1
        elif raw[pos] == ord("}"):
            depth -= 1
            require(depth >= 0, "VBF header braces underflow")
            if depth == 0:
                return pos + 1
    raise VerifyError("VBF header has no matching closing brace")


def walk_frames(raw: bytes, offset: int) -> tuple[Block, ...] | None:
    blocks: list[Block] = []
    cursor = offset
    while cursor < len(raw):
        if cursor + 10 > len(raw):
            return None
        load, length = struct.unpack_from(">II", raw, cursor)
        end = cursor + 8 + length
        if end + 2 > len(raw):
            return None
        payload = raw[cursor + 8 : end]
        stored_crc = struct.unpack_from(">H", raw, end)[0]
        blocks.append(Block(load, payload, stored_crc, cursor, cursor + 8, end))
        cursor = end + 2
    return tuple(blocks) if blocks and cursor == len(raw) else None


def parse_vbf(path: Path) -> VBF:
    raw = path.read_bytes()
    header_end = matching_header_end(raw)
    candidates: list[tuple[int, tuple[Block, ...]]] = []
    for candidate in range(header_end, min(header_end + 8, len(raw))):
        walked = walk_frames(raw, candidate)
        if walked is not None:
            candidates.append((candidate, walked))
    require(len(candidates) == 1, f"{path.name}: expected one exact-to-EOF block walk, got {len(candidates)}")
    data_start, blocks = candidates[0]
    try:
        header_text = raw[:header_end].decode("ascii")
    except UnicodeDecodeError as exc:
        raise VerifyError(f"{path.name}: non-ASCII header") from exc
    match = re.search(r"(?m)\bfile_checksum\s*=\s*(0x[0-9A-Fa-f]+)\s*;", header_text)
    require(match is not None, f"{path.name}: missing file_checksum")
    dfi = re.search(r"(?m)\bdata_format_identifier\s*=\s*(0x[0-9A-Fa-f]+)\s*;", header_text)
    require(dfi is None or int(dfi.group(1), 16) == 0, f"{path.name}: compressed payload is outside this verifier")
    return VBF(path, raw, header_end, data_start, header_text, int(match.group(1), 16), blocks)


def verify_container(vbf: VBF) -> None:
    for index, block in enumerate(vbf.blocks):
        actual = crc16_ccitt_false(block.payload)
        require(actual == block.crc, f"{vbf.path.name}: block {index} CRC is {block.crc:04X}, computed {actual:04X}")
    actual_file = crc32_iso_hdlc(vbf.raw[vbf.data_start :])
    require(actual_file == vbf.file_checksum, f"{vbf.path.name}: file CRC is {vbf.file_checksum:08X}, computed {actual_file:08X}")


def words_at_flash(blocks: Iterable[Block], flash: int, count: int) -> tuple[int, ...]:
    size = count * 2
    for block in blocks:
        local = flash - block.load
        if 0 <= local and local + size <= len(block.payload):
            return struct.unpack_from("<" + "H" * count, block.payload, local)
    raise VerifyError(f"flash range 0x{flash:X}..0x{flash + size:X} is not in one block")


def p_words(vbf: VBF, p_address: int, count: int) -> tuple[int, ...]:
    return words_at_flash(vbf.blocks, p_address * 2, count)


def payload_differences(before: VBF, after: VBF) -> list[tuple[int, int, int]]:
    require([(b.load, len(b.payload)) for b in before.blocks] == [(b.load, len(b.payload)) for b in after.blocks],
            "stock/output block topology differs")
    differences: list[tuple[int, int, int]] = []
    for old_block, new_block in zip(before.blocks, after.blocks):
        for offset, (old, new) in enumerate(zip(old_block.payload, new_block.payload)):
            if old != new:
                differences.append((old_block.load + offset, old, new))
    return differences


def cluster_differences(differences: list[tuple[int, int, int]]) -> list[tuple[int, int, bytes, bytes]]:
    require(differences, "stock/output payloads are identical")
    clusters: list[tuple[int, int, bytes, bytes]] = []
    start = previous = differences[0][0]
    old = bytearray([differences[0][1]])
    new = bytearray([differences[0][2]])
    for address, old_byte, new_byte in differences[1:]:
        if address != previous + 1:
            clusters.append((start, previous + 1, bytes(old), bytes(new)))
            start = address
            old = bytearray()
            new = bytearray()
        old.append(old_byte)
        new.append(new_byte)
        previous = address
    clusters.append((start, previous + 1, bytes(old), bytes(new)))
    return clusters


def normalized_header(header: bytes) -> bytes:
    pattern = re.compile(rb"(\bfile_checksum\s*=\s*)0x[0-9A-Fa-f]+(\s*;)")
    matches = list(pattern.finditer(header))
    require(len(matches) == 1, "header does not contain exactly one file_checksum field")
    match = matches[0]
    value_start = match.start() + len(match.group(1))
    value_end = match.end() - len(match.group(2))
    return header[:value_start] + (b"#" * (value_end - value_start)) + header[value_end:]


def prove_container_layout_and_diff(stock: VBF, output: VBF) -> list[tuple[int, int, bytes, bytes]]:
    require(stock.data_start == output.data_start == 0x492, "unexpected application data_start")
    expected_topology = [(0x00000000, 0x9800), (0x0001C000, 0x64000), (0x04008C00, 0x7400)]
    require([(b.load, len(b.payload)) for b in stock.blocks] == expected_topology, "unexpected stock topology")
    require([(b.load, len(b.payload)) for b in output.blocks] == expected_topology, "unexpected output topology")
    require([b.frame_offset for b in stock.blocks] == [b.frame_offset for b in output.blocks], "block framing moved")
    require(normalized_header(stock.raw[:stock.header_end]) == normalized_header(output.raw[:output.header_end]),
            "application header changed outside file_checksum")
    require(stock.blocks[0].payload == output.blocks[0].payload and stock.blocks[2].payload == output.blocks[2].payload,
            "an untouched application payload block changed")
    require(stock.blocks[0].crc == output.blocks[0].crc and stock.blocks[2].crc == output.blocks[2].crc,
            "an untouched application block CRC field changed")

    differences = payload_differences(stock, output)
    # Exact changed bytes within the three edited word-site spans.  Three bytes
    # happen to retain their OEM octet value: the high octet of the pointer's
    # high word and two octets in the cave.  They remain part of changed words,
    # but must not be falsely counted as byte differences.
    expected_addresses = (
        (set(range(0x1DD48, 0x1DD4C)) - {0x1DD4B})
        | (set(range(0x67000, 0x67078)) - {0x67052, 0x67077})
        | set(range(0x7FFEA, 0x7FFEE))
    )
    require({address for address, _, _ in differences} == expected_addresses,
            "payload changed outside (or failed to match) the three exact edited word-site spans")
    raw_clusters = cluster_differences(differences)
    require([(start, end) for start, end, _, _ in raw_clusters] ==
            [(0x1DD48, 0x1DD4B), (0x67000, 0x67052), (0x67053, 0x67077), (0x7FFEA, 0x7FFEE)],
            "contiguous changed-byte runs do not match exactly")
    require(len(differences) == 125, "unexpected changed-byte total")

    old_words = p_words(stock, FD22_RECORD_P + 2, 2) + p_words(stock, CAVE_P, CAVE_WORDS)
    new_words = p_words(output, FD22_RECORD_P + 2, 2) + p_words(output, CAVE_P, CAVE_WORDS)
    require(all(old != new for old, new in zip(old_words, new_words)),
            "not all 62 functional pointer/cave words changed")
    require(p_words(stock, CHECK_A_FLASH // 2, 2) != p_words(output, CHECK_A_FLASH // 2, 2),
            "internal checksum words did not both change")

    # Report the three intentional edited word-site spans, retaining unchanged
    # coincident octets in their before/after excerpts.
    sites: list[tuple[int, int, bytes, bytes]] = []
    for start, end in ((0x1DD48, 0x1DD4C), (0x67000, 0x67078), (0x7FFEA, 0x7FFEE)):
        old = next(b.payload[start-b.load:end-b.load] for b in stock.blocks if b.load <= start and end <= b.load + len(b.payload))
        new = next(b.payload[start-b.load:end-b.load] for b in output.blocks if b.load <= start and end <= b.load + len(b.payload))
        sites.append((start, end, old, new))
    return sites


def find_pair_locations(words: tuple[int, ...], first: int, second: int) -> list[int]:
    return [index for index in range(len(words) - 1) if words[index] == first and words[index + 1] == second]


def prove_oem_templates(stock: VBF) -> dict[int, list[int]]:
    require(p_words(stock, 0x2075B, 2) == (0xE080, 0xD0B6), "OEM zero/base-store template drift")
    require(p_words(stock, 0x20712, 4) == (0xF014, 0x5C28, 0xE58C, 0xD0B6), "OEM shift/high-store template drift")
    require(p_words(stock, 0x2046C, 6) == (0x5C68, 0xE582, 0xD0B6, 0xD1E6, 0x0001, 0xE708),
            "OEM two-byte packing/RTS template drift")
    require(p_words(stock, 0x1059D, 2) == (0xE58F, 0xE708), "OEM Y0=15/RTS template drift")
    require(p_words(stock, 0x13B32, 15) ==
            (0xF17C, 0x0665, 0xF07C, 0x066D, 0x7886, 0xA701, 0x8110,
             0xF07C, 0x0675, 0x7886, 0xA701, 0x8110, 0xD17C, 0x17B0, 0xE708),
            "OEM min/select evidence for the 8110 A1-to-B1 transfer drifted")

    all_words: list[int] = []
    # Only even-length application blocks containing executable P words are scanned.
    for block in stock.blocks:
        if block.load < 0x80000:
            all_words.extend(struct.unpack("<" + "H" * (len(block.payload) // 2), block.payload))
    packed = tuple(all_words)
    locations: dict[int, list[int]] = {}
    for source in SOURCES:
        hits = find_pair_locations(packed, 0xF07C, source)
        require(hits, f"no immutable OEM F07C absolute-X-load template for X:${source:04X}")
        locations[source] = hits
    return locations


@dataclass(frozen=True)
class Store:
    offset: int
    source: int | None
    byte_kind: str


def decode_and_prove_handler(output: VBF) -> tuple[tuple[int, ...], list[Store]]:
    words = p_words(output, CAVE_P, CAVE_WORDS)
    cursor = 0
    stores: list[Store] = []
    loaded_sources: list[int] = []
    written_registers = {"A", "CCR"}

    require(words[cursor:cursor + 2] == (0xE080, 0xD0B6), "handler does not start with zero-A/base scratch store")
    cursor += 2
    stores.append(Store(0, None, "constant-zero"))

    expected_offset = 1
    while cursor < CAVE_WORDS - 2:
        require(cursor + 8 <= CAVE_WORDS - 2, "truncated handler field group")
        opcode, source, transfer, shift, high_store, high_offset, low_store, low_offset = words[cursor:cursor + 8]
        require(opcode == 0xF07C, f"unknown/non-load opcode 0x{opcode:04X} at cave word {cursor}")
        require(transfer == 0x8110, f"missing A1-to-B1 retention at cave word {cursor + 2}")
        require(shift == 0x5C28, f"missing logical right shift by 8 at cave word {cursor + 3}")
        require(high_store == 0xD0E6 and low_store == 0xD1E6,
                f"non-scratch or unknown store encoding in group at cave word {cursor}")
        require(high_offset == expected_offset and low_offset == expected_offset + 1,
                f"scratch offsets out of sequence in group at cave word {cursor}")
        loaded_sources.append(source)
        stores.extend((Store(high_offset, source, "high"), Store(low_offset, source, "low")))
        written_registers.update(("A", "B", "CCR"))
        expected_offset += 2
        cursor += 8

    require(words[cursor:] == (0xE58F, 0xE708), "handler does not terminate with Y0=15 then RTS")
    written_registers.add("Y0")
    require(cursor + 2 == CAVE_WORDS, "decoder did not consume exactly 60 cave words")
    require(tuple(loaded_sources) == SOURCES, "source cells or source order differ from the payload contract")
    require(len(loaded_sources) == 7 and len(set(loaded_sources)) == 7, "each source must be loaded exactly once")
    require(len(stores) == 15 and [store.offset for store in stores] == list(range(15)),
            "scratch stores are not exactly offsets 0..14 once each")
    require(stores[0] == Store(0, None, "constant-zero"), "payload byte zero is not the zero format byte")
    for index, source in enumerate(SOURCES):
        high, low = stores[1 + index * 2 : 3 + index * 2]
        require((high.source, high.byte_kind, low.source, low.byte_kind) == (source, "high", source, "low"),
                f"source X:${source:04X} is not emitted high byte then retained low byte")
    require(written_registers == {"A", "B", "Y0", "CCR"}, "handler clobber set is not the proven subset")

    # Exhaustive grammar above consumed every word. Its only memory-writing
    # productions are D0B6/D0E6/D1E6, all byte-pointer stores based on unchanged
    # R2 at the proved offsets. It has no branch/call/stack production; E708 is
    # the sole terminal transfer and appears only as the final instruction.
    return words, stores


def prove_pointer(stock: VBF, output: VBF) -> None:
    require(p_words(stock, FD22_RECORD_P, 6) == (0xFD22, 0x0000, 0x058F, 0x0001, 0x0000, 0x0000),
            "stock FD22 table record drift")
    record = p_words(output, FD22_RECORD_P, 6)
    require(record == (0xFD22, 0x0000, 0x3800, 0x0003, 0x0000, 0x0000), "output FD22 table record is not the exact pointer-only edit")
    pointer = record[2] | (record[3] << 16)
    require(pointer == CAVE_P, f"FD22 pointer targets P:${pointer:05X}, not P:${CAVE_P:05X}")


def make_linear_image(app: VBF, calibration: VBF) -> bytes:
    intervals = [(block.load, block.load + len(block.payload), block.payload) for block in (*app.blocks, *calibration.blocks) if block.load < 0x80000]
    intervals.sort(key=lambda item: item[0])
    require([(start, end) for start, end, _ in intervals] ==
            [(0x00000, 0x09800), (0x09800, 0x1C000), (0x1C000, 0x80000)],
            "application/calibration do not form the exact contiguous 0x00000..0x80000 image")
    return b"".join(payload for _, _, payload in intervals)


def prove_internal_checksums(stock: VBF, output: VBF, calibration: VBF) -> tuple[tuple[int, int], tuple[int, int]]:
    results: list[tuple[int, int]] = []
    for app, expected_a, expected_b in ((stock, 0xD110, 0x3216), (output, 0x1ED4, 0x3507)):
        block0, block1 = app.blocks[0], app.blocks[1]
        stored_a = struct.unpack_from("<H", block1.payload, CRC_A_BLOCK1_END)[0]
        calculated_a = crc16_mcrf4xx(block0.payload + block1.payload[CRC_A_BLOCK1_START:CRC_A_BLOCK1_END])
        require((stored_a, calculated_a) == (expected_a, expected_a), f"{app.path.name}: internal A mismatch")

        image = make_linear_image(app, calibration)
        require(len(image) == 0x80000, "linear application/calibration image has wrong size")
        stored_b = struct.unpack_from("<H", image, CHECK_B_FLASH)[0]
        require(struct.unpack_from("<H", image, CHECK_A_FLASH)[0] == stored_a, "linear image does not contain internal A at 0x7FFEA")
        calculated_b = sum_le16(image[:CHECK_B_FLASH])
        require((stored_b, calculated_b) == (expected_b, expected_b), f"{app.path.name}: internal B mismatch")
        results.append((calculated_a, calculated_b))
    return results[0], results[1]


def verify() -> None:
    parsed: dict[str, VBF] = {}
    for path in (STOCK_PATH, OUTPUT_PATH, CAL_PATH):
        require(path.is_file(), f"missing input: {path}")
        raw = path.read_bytes()
        actual_hash = sha256(raw)
        require(actual_hash == EXPECTED_FILE_SHA256[path.name], f"{path.name}: SHA-256 drift: {actual_hash}")
        parsed[path.name] = parse_vbf(path)

    stock = parsed[STOCK_PATH.name]
    output = parsed[OUTPUT_PATH.name]
    calibration = parsed[CAL_PATH.name]
    for vbf in (stock, output, calibration):
        verify_container(vbf)

    require([sha256(block.payload) for block in stock.blocks] == list(EXPECTED_STOCK_BLOCK_SHA256),
            "stock application block hash drift")
    require([(b.load, len(b.payload)) for b in calibration.blocks] == [(0x9800, 0x12800)],
            "unexpected calibration topology")
    clusters = prove_container_layout_and_diff(stock, output)
    prove_pointer(stock, output)
    template_locations = prove_oem_templates(stock)
    handler_words, stores = decode_and_prove_handler(output)
    stock_checks, output_checks = prove_internal_checksums(stock, output, calibration)

    print("Independent FD22 snapshot verification")
    for path in (STOCK_PATH, CAL_PATH, OUTPUT_PATH):
        print(f"SHA-256 {path.name}: {EXPECTED_FILE_SHA256[path.name]}")
    print("Container CRCs: PASS (stock 3 blocks; calibration 1 block; output 3 blocks; all file CRC-32 values)")
    print(f"Internal A/B: stock {stock_checks[0]:04X}/{stock_checks[1]:04X}; output {output_checks[0]:04X}/{output_checks[1]:04X}")
    changed_byte_count = len(payload_differences(stock, output))
    print(f"Payload diff: {len(clusters)} exact edited word-site clusters, {changed_byte_count} differing bytes")
    for start, end, old, new in clusters:
        print(f"  0x{start:05X}..0x{end:05X} ({end-start} bytes): {old[:12].hex()} -> {new[:12].hex()}")
    print(f"FD22 pointer: P:${CAVE_P:05X}; cave: {len(handler_words)} raw words")
    print("Handler: 15 R2 scratch byte stores at offsets 0..14; sources=" + ",".join(f"X:${source:04X}" for source in SOURCES))
    print("Byte order: zero format byte, then high/low for each source; each source loaded once")
    print("Termination/control: Y0=15; final RTS; no branch, call, loop, stack access, R2 write, or OEM-state write")
    print("OEM load-template hits: " + "; ".join(f"X:${source:04X}={len(template_locations[source])}" for source in SOURCES))
    print("READY FOR CONTROLLED TEST")


def main() -> int:
    try:
        verify()
    except Exception as exc:
        print(f"VERIFY FAILURE: {exc}", file=sys.stderr)
        print("DO NOT FLASH", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
