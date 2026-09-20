#!/usr/bin/env python3
"""Read-only evidence extractor for the CV6T-14C217-AR DID callback ABI.

This script reads the extracted OEM P/X images, validates the exact instruction
and table anchors used by did_callback_abi_report.md, and prints a compact JSON
record.  It never writes firmware or opens a CAN interface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BLK0 = ROOT / "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin"
BLK1 = ROOT / "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin"
BLK2 = ROOT / "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk2_0x04008C00.bin"
P_BASES = ((BLK0, 0x00000), (BLK1, 0x0E000))
X_BASE = 0x04600
TABLE = 0x0ED5E
TABLE_COUNT = 57

EXPECTED_SHA256 = {
    BLK0.name: "21d095f6ce8496695951a6ad2f012d428da87ea21c781b4f4a4d0992994e5074",
    BLK1.name: "6e60963a3583993d1b872ebd88bb9b2f1acdeb397a7954a1ae75be3ce7d933b0",
    BLK2.name: "4e65a6493cf770d02a125c115a50c7b6b8d58969405bbd0292fafd896750d6c0",
}


def words(path: Path) -> list[int]:
    data = path.read_bytes()
    if len(data) & 1:
        raise AssertionError(f"odd-sized word image: {path}")
    return list(struct.unpack(f"<{len(data)//2}H", data))


class Image:
    def __init__(self) -> None:
        self.p_spans = [(base, words(path)) for path, base in P_BASES]
        self.x = words(BLK2)

    def pword(self, address: int) -> int:
        for base, data in self.p_spans:
            if base <= address < base + len(data):
                return data[address - base]
        raise KeyError(f"P:${address:05X}")

    def pwords(self, address: int, count: int) -> list[int]:
        return [self.pword(address + i) for i in range(count)]

    def xwords(self, address: int, count: int) -> list[int]:
        off = address - X_BASE
        if off < 0 or off + count > len(self.x):
            raise KeyError(f"X:${address:04X}")
        return self.x[off : off + count]


def expect(img: Image, address: int, expected: list[int], label: str) -> None:
    got = img.pwords(address, len(expected))
    if got != expected:
        raise AssertionError(
            f"{label} P:${address:05X}: expected "
            f"{' '.join(f'{x:04X}' for x in expected)}, got "
            f"{' '.join(f'{x:04X}' for x in got)}"
        )


def parse_table(img: Image) -> dict[int, dict[str, int]]:
    result: dict[int, dict[str, int]] = {}
    for index in range(TABLE_COUNT):
        address = TABLE + 6 * index
        q = img.pwords(address, 6)
        result[q[0]] = {
            "index": index,
            "p_address": address,
            "flags": q[1],
            "read": q[2] | (q[3] << 16),
            "write": q[4] | (q[5] << 16),
        }
    return result


def verify() -> dict[str, object]:
    for path in (BLK0, BLK1, BLK2):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == EXPECTED_SHA256[path.name], (path, digest)

    img = Image()
    table = parse_table(img)
    wanted = {
        0xFD08: (0x1C084, 2, 0x6348, 0x0000),
        0xFD0C: (0x1C0C0, 1, 0x6358, 0x0001),
        0xFD0E: (0x1C0C6, 1, 0x6360, 0x0001),
        0xFD20: (0x1C0F9, 12, 0x6380, 0x0001),
        0xFD22: (0x1058F, 15, 0x6388, 0x0001),
    }
    controls: dict[str, object] = {}
    for did, (handler, length, meta_address, access_word) in wanted.items():
        entry = table[did]
        assert entry["read"] == handler
        metadata = img.xwords(meta_address, 4)
        assert metadata[0] == did
        assert metadata[2] == access_word
        assert metadata[3] == length
        controls[f"{did:04X}"] = {
            "table_p": f"0x{entry['p_address']:05X}",
            "read_handler_p": f"0x{handler:05X}",
            "metadata_x": f"0x{meta_address:04X}",
            "metadata_access_word": metadata[2],
            "expected_length": metadata[3],
        }

    # Service selector and direct P-table lookup/read-pointer load.
    expect(img, 0x103DA, [0x5DC2, 0x0022, 0xA204], "service 0x22 selector")
    expect(img, 0x103E1, [0x5DC2, 0x002E, 0xA204], "service 0x2E selector")
    expect(img, 0x103E8, [0x8748, 0x2646, 0xE091, 0xD0E4, 0x00A2,
                           0xE580], "unsupported service failure")
    expect(img, 0x103EF, [0x8748, 0x2646, 0xE092, 0xD0E4, 0x00A2,
                           0xE580], "DID lookup failure")
    expect(img, 0x103B0, [0xE086, 0x6C05, 0x8016, 0x8810, 0x8220, 0xED5E,
                           0x8068], "DID table lookup")
    expect(img, 0x10511, [0x8369, 0x7C07, 0x8369, 0x7C01, 0xE021],
           "read callback pointer load")

    # Callback call: form a stack-backed byte pointer in R2, call through R0,
    # compare configured length C with returned Y0, append on equality.
    expect(img, 0x10522, [0x80B6, 0x8A32, 0xFFE8, 0xE020, 0xE614, 0x7956,
                           0xA208], "read callback call and length check")
    expect(img, 0x10529, [0x80B6, 0x8A32, 0xFFE8, 0x8512, 0xE257, 0x2056,
                           0xE581], "successful payload append")
    expect(img, 0x10531, [0x8748, 0x2646, 0xE093, 0xD0E4, 0x00A2, 0xE580],
           "length mismatch failure")
    expect(img, 0x10538, [0x8748, 0x2646, 0xE092, 0xD0E4, 0x00A2, 0xE580],
           "null read callback failure")

    # OEM controls.  20553/20565 copy R2 to saved R5, write one byte through
    # R5, return Y0=1.  FD22 copies R2 to R1 and performs 15 BP copies.
    expect(img, 0x20553, [0x827B, 0xDD3F, 0x8139, 0xE255, 0xC177,
                           0xD5BD, 0xE581, 0xFD3B, 0xE708], "FD0C leaf")
    expect(img, 0x20565, [0x827B, 0xDD3F, 0x8139, 0xE255, 0xC1A7,
                           0xD5BD, 0xE581, 0xFD3B, 0xE708], "FD0E leaf")
    expect(img, 0x1058F, [0x8748, 0x1DAC, 0xE180, 0xE129, 0x8220, 0x002D,
                           0x8931, 0xA903, 0xF8A0, 0xD0A1, 0x7083, 0x4C8F,
                           0xA17B, 0xE700, 0xE58F, 0xE708], "FD22 leaf")

    # Independent multi-byte controls: FD08 returns 2 and emits high byte then
    # low byte; FD20 returns 12 and repeats the same explicit high/low ordering.
    expect(img, 0x2046C, [0x5C68, 0xE582, 0xD0B6, 0xD1E6, 0x0001, 0xE708],
           "FD08 two-byte packing")
    expect(img, 0x20710, [0x8748, 0x2519, 0xF014, 0x5C28, 0xE58C,
                           0xD0B6, 0xF014, 0x8748, 0x251A, 0xD0E6, 0x0001],
           "FD20 first high/low pair")
    assert img.pword(0x20744) == 0xD0E6 and img.pword(0x20745) == 0x000B
    assert img.pword(0x20746) == 0xE708

    fd22 = table[0xFD22]
    assert fd22["p_address"] == 0x0EEA2
    assert img.pwords(0x0EEA2, 6) == [0xFD22, 0, 0x058F, 1, 0, 0]

    return {
        "result": "ALL STATIC ABI CHECKS PASS",
        "source_sha256": EXPECTED_SHA256,
        "did_controls": controls,
        "abi": {
            "output_pointer": "R2 byte pointer into dispatcher stack scratch",
            "callback_return": "Y0 exact payload byte count",
            "configured_length_register_at_return": "C (compared with Y0)",
            "dispatcher_success_return": "Y0=1",
            "dispatcher_failure_return": "Y0=0",
            "length_mismatch_nrc": "0x13",
            "null_read_pointer_nrc": "0x12",
            "fd22_pointer_words": ["0x058F", "0x0001"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true",
                        help="verify all evidence anchors (default also verifies)")
    parser.add_argument("--json", action="store_true", help="pretty JSON output")
    args = parser.parse_args()
    result = verify()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["result"])
        for did, item in result["did_controls"].items():
            print(f"{did}: {item}")


if __name__ == "__main__":
    main()
