#!/usr/bin/env python3
"""Independent read-only verifier for LCA-gate-open + FD22 combined VBF."""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import sys

import verify_fd22_snapshot_vbf_independent as ind

ROOT = Path(__file__).resolve().parents[2]
STOCK = ROOT / "CV6T-14C217-AR.VBF"
CAL = ROOT / "CV6T-14C218-AX.VBF"
OUTPUT = ROOT / "CV6T-14C217-AR_LCA_GATE_FD22.VBF"
FD22_ONLY = ROOT / "CV6T-14C217-AR_FD22_SNAPSHOT.VBF"
GATE_ONLY = ROOT / "CV6T-14C217-AR_LCA.VBF"

HASHES = {
    STOCK.name: "cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5",
    CAL.name: "6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7",
    FD22_ONLY.name: "d4f8970c8c756fab6fe039455f0d0e5fb37156ba229f68410c5034b35dc8f370",
    GATE_ONLY.name: "696776f0be752a89fa47ad2c69053199eadbdf3d8ae5e97447cb16f2aa69538b",
    OUTPUT.name: "12c78350a88bc3003fa1caf1176fa5448527c0d51eb49535946dd58f3c6c5598",
}

GATE_ARM_P = 0x2A77E
OEM_ARM = (0x4C06, 0xA207, 0xF07C, 0x0904, 0x4C01, 0xA203,
           0xE684, 0x2DDE, 0xA902, 0xE680, 0x2DDE)
OPEN_ARM = (0x4C06, 0xA207, 0xE700, 0xE700, 0xE700, 0xE700,
            0xE684, 0x2DDE, 0xA902, 0xE680, 0x2DDE)
POINTER_SPAN = (0x1DD48, 0x1DD4C)
GATE_SPAN = (0x54F00, 0x54F08)
HANDLER_SPAN = (0x67000, 0x67078)
CHECKSUM_SPAN = (0x7FFEA, 0x7FFEE)


def require(value: bool, message: str) -> None:
    if not value:
        raise ind.VerifyError(message)


def payload_slice(vbf: ind.VBF, start: int, end: int) -> bytes:
    for block in vbf.blocks:
        if block.load <= start and end <= block.load + len(block.payload):
            return block.payload[start - block.load:end - block.load]
    raise ind.VerifyError(f"span 0x{start:X}..0x{end:X} is not in one block")


def verify() -> None:
    paths = (STOCK, CAL, FD22_ONLY, GATE_ONLY, OUTPUT)
    parsed: dict[str, ind.VBF] = {}
    for path in paths:
        require(path.is_file(), f"missing {path}")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == HASHES[path.name],
                f"{path.name}: SHA-256 drift")
        parsed[path.name] = ind.parse_vbf(path)
        ind.verify_container(parsed[path.name])

    stock = parsed[STOCK.name]
    cal = parsed[CAL.name]
    fd22 = parsed[FD22_ONLY.name]
    gate = parsed[GATE_ONLY.name]
    out = parsed[OUTPUT.name]

    topology = [(0x00000000, 0x9800), (0x0001C000, 0x64000), (0x04008C00, 0x7400)]
    require([(b.load, len(b.payload)) for b in stock.blocks] == topology, "stock topology drift")
    require([(b.load, len(b.payload)) for b in out.blocks] == topology, "output topology drift")
    require(stock.data_start == out.data_start == 0x492, "data_start changed")
    require(ind.normalized_header(stock.raw[:stock.header_end]) ==
            ind.normalized_header(out.raw[:out.header_end]),
            "header changed outside file_checksum")
    require(stock.blocks[0].payload == out.blocks[0].payload and
            stock.blocks[2].payload == out.blocks[2].payload,
            "untouched payload block changed")

    require(ind.p_words(stock, GATE_ARM_P, 11) == OEM_ARM, "OEM gate arm drift")
    require(ind.p_words(out, GATE_ARM_P, 11) == OPEN_ARM, "combined gate arm is not exact")
    require(ind.p_words(gate, GATE_ARM_P, 11) == OPEN_ARM,
            "combined gate arm differs from previously built gate-only artifact")
    require(OPEN_ARM[:2] == OEM_ARM[:2] and OPEN_ARM[6:] == OEM_ARM[6:],
            "outer dispatch or state stores changed")

    ind.prove_pointer(stock, out)
    handler_words, stores = ind.decode_and_prove_handler(out)
    require(payload_slice(out, *POINTER_SPAN) == payload_slice(fd22, *POINTER_SPAN),
            "combined FD22 pointer differs from validated FD22-only artifact")
    require(payload_slice(out, *HANDLER_SPAN) == payload_slice(fd22, *HANDLER_SPAN),
            "combined handler differs from validated FD22-only artifact")
    require(payload_slice(out, *GATE_SPAN) == payload_slice(gate, *GATE_SPAN),
            "combined gate differs from gate-only artifact")

    diffs = ind.payload_differences(stock, out)
    allowed = set()
    for start, end in (POINTER_SPAN, GATE_SPAN, HANDLER_SPAN, CHECKSUM_SPAN):
        allowed.update(range(start, end))
    actual = {address for address, _, _ in diffs}
    require(actual <= allowed, "payload has changes outside four allowed sites")
    require(all(any(start <= address < end for address in actual)
                for start, end in (POINTER_SPAN, GATE_SPAN, HANDLER_SPAN, CHECKSUM_SPAN)),
            "an intended change site contains no changed bytes")

    # Independently recompute both internal layers.
    b0, b1 = out.blocks[0], out.blocks[1]
    stored_a = struct.unpack_from("<H", b1.payload, ind.CRC_A_BLOCK1_END)[0]
    calc_a = ind.crc16_mcrf4xx(
        b0.payload + b1.payload[ind.CRC_A_BLOCK1_START:ind.CRC_A_BLOCK1_END])
    require(stored_a == calc_a == 0x3DD1, "internal checksum A mismatch")
    image = ind.make_linear_image(out, cal)
    stored_b = struct.unpack_from("<H", image, ind.CHECK_B_FLASH)[0]
    calc_b = ind.sum_le16(image[:ind.CHECK_B_FLASH])
    require(stored_b == calc_b == 0x0880, "internal checksum B mismatch")

    clusters = ind.cluster_differences(diffs)
    print("Independent combined LCA-gate + FD22 verification")
    print(f"SHA-256: {HASHES[OUTPUT.name]}")
    print("Container CRCs: PASS; topology/header framing: PASS")
    print(f"Internal A/B: {stored_a:04X}/{stored_b:04X} PASS")
    print(f"Payload differences: {len(diffs)} bytes in {len(clusters)} contiguous runs")
    for start, end, old, new in clusters:
        print(f"  0x{start:05X}..0x{end:05X}: {old[:12].hex()} -> {new[:12].hex()}")
    print("Gate: request-6 outer branch retained; X:$0904 guard replaced by exactly four NOPs")
    print("Gate behavior: request 6 falls through to OEM state-4 store; all other requests retain OEM branch")
    print(f"FD22: P:${ind.CAVE_P:05X}, {len(handler_words)} words, {len(stores)} scratch-byte stores")
    print("FD22 handler: byte-identical to independently validated observation handler")
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
