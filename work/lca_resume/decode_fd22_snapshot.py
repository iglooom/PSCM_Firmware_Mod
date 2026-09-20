#!/usr/bin/env python3
"""Decode a CV6T PSCM FD22 stateless snapshot response.

Accepts either the 15-byte payload or the complete positive UDS response
62 FD 22 + 15 payload bytes. Values are reported as raw unsigned and signed
16-bit integers; no physical scaling is assumed.
"""
from __future__ import annotations

import argparse
import re
import sys

FIELDS = (
    ("lane_state", "X:$2DDE"),
    ("per_state_code", "X:$2DB9"),
    ("torque_accumulator", "X:$2D53"),
    ("torque_output_result", "X:$2D52"),
    ("fd0e_raw_source", "X:$1CB1"),
    ("fd0c_raw_source", "X:$171B"),
    ("upstream_intermediate", "X:$2D54"),
)


def parse_hex(text: str) -> bytes:
    compact = re.sub(r"(?:0x)?|[\s:_-]", "", text, flags=re.IGNORECASE)
    if not compact or len(compact) % 2 or re.search(r"[^0-9a-f]", compact, re.I):
        raise ValueError("input must contain an even number of hexadecimal digits")
    raw = bytes.fromhex(compact)
    if len(raw) == 18:
        if raw[:3] != b"\x62\xfd\x22":
            raise ValueError("18-byte input is not a positive 62 FD 22 response")
        raw = raw[3:]
    if len(raw) != 15:
        raise ValueError(f"expected 15-byte payload or 18-byte UDS response, got {len(raw)} bytes")
    if raw[0] != 0:
        raise ValueError(f"unsupported format/status byte 0x{raw[0]:02X}; expected 0x00")
    return raw


def decode(raw: bytes) -> list[tuple[str, str, int, int]]:
    result = []
    for index, (name, source) in enumerate(FIELDS):
        unsigned = int.from_bytes(raw[1 + 2 * index:3 + 2 * index], "big")
        signed = unsigned if unsigned < 0x8000 else unsigned - 0x10000
        result.append((name, source, unsigned, signed))
    return result


def selftest() -> None:
    payload = bytes.fromhex("00 0004 0005 FFFE 8000 7FFF 0000 1234")
    assert [row[2:] for row in decode(parse_hex(payload.hex()))] == [
        (4, 4), (5, 5), (0xFFFE, -2), (0x8000, -32768),
        (0x7FFF, 32767), (0, 0), (0x1234, 0x1234),
    ]
    assert parse_hex("62 FD 22 " + payload.hex()) == payload
    for bad in ("", "00", "62FD2300" + "00" * 14, "01" + "00" * 14):
        try:
            parse_hex(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid input: {bad!r}")
    print("SELFTEST PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("response", nargs="?", help="hex payload or complete 62FD22 response")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    if args.response is None:
        parser.error("response is required unless --selftest is used")
    try:
        payload = parse_hex(args.response)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"format/status: 0x{payload[0]:02X}")
    for name, source, unsigned, signed in decode(payload):
        print(f"{name:24s} {source:8s} raw=0x{unsigned:04X} unsigned={unsigned:5d} signed={signed:6d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
