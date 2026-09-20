#!/usr/bin/env python3
"""Decode the proposed observation-only PSCM ring format.

Input is a raw little-endian dump of 7-word records, or newline-separated FD22
15-byte payloads (one status byte + one big-endian 7-word record).  This is a
format/checking utility only; it does not communicate with a vehicle.
"""
from __future__ import annotations

import argparse
import csv
import io
import struct
import sys
from pathlib import Path

FIELDS = ("loop_seq", "internal_state", "per_state", "torque_acc",
          "torque_output", "fd0e_raw", "fd0c_raw")
RECORD_WORDS = 7
RECORD_BYTES = RECORD_WORDS * 2


def s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def decode_words(values: tuple[int, ...]) -> dict[str, int]:
    if len(values) != RECORD_WORDS:
        raise ValueError("record must contain exactly 7 words")
    return {
        "loop_seq": values[0],
        "internal_state": values[1],
        "per_state": values[2],
        "torque_acc": s16(values[3]),
        "torque_output": s16(values[4]),
        "fd0e_raw": s16(values[5]),
        "fd0c_raw": s16(values[6]),
    }


def decode_raw(data: bytes) -> list[dict[str, int]]:
    if len(data) % RECORD_BYTES:
        raise ValueError("raw length %d is not a multiple of %d" %
                         (len(data), RECORD_BYTES))
    out = []
    for off in range(0, len(data), RECORD_BYTES):
        out.append(decode_words(struct.unpack_from("<7H", data, off)))
    return out


def decode_fd22_text(text: str) -> list[dict[str, int]]:
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.partition("#")[0].strip().replace(" ", "")
        if not line:
            continue
        try:
            payload = bytes.fromhex(line)
        except ValueError as exc:
            raise ValueError("line %d: invalid hex" % lineno) from exc
        if len(payload) != 15:
            raise ValueError("line %d: expected 15 payload bytes, got %d" %
                             (lineno, len(payload)))
        rec = decode_words(struct.unpack(">7H", payload[1:]))
        rec = {"status": payload[0], **rec}
        out.append(rec)
    return out


def render_csv(records: list[dict[str, int]]) -> str:
    buf = io.StringIO()
    names = (["status"] if records and "status" in records[0] else []) + list(FIELDS)
    writer = csv.DictWriter(buf, fieldnames=names, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return buf.getvalue()


def selftest() -> int:
    raw = struct.pack("<7H", 0x1234, 4, 5, 0xFFFE, 1, 0xFE00, 0x0200)
    got = decode_raw(raw)[0]
    assert got == {"loop_seq": 0x1234, "internal_state": 4, "per_state": 5,
                   "torque_acc": -2, "torque_output": 1,
                   "fd0e_raw": -512, "fd0c_raw": 512}
    page = "81" + struct.pack(">7H", 0x1234, 4, 5, 0xFFFE, 1, 0xFE00, 0x0200).hex()
    got2 = decode_fd22_text(page)[0]
    assert got2["status"] == 0x81 and all(got2[k] == v for k, v in got.items())
    assert "internal_state" in render_csv([got2])
    print("SELFTEST: ALL PASS")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", nargs="?", type=Path)
    ap.add_argument("--fd22-pages", action="store_true",
                    help="input is one 15-byte FD22 payload per hex line")
    ap.add_argument("--out", type=Path, help="CSV output (default stdout)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.input is None:
        ap.error("input is required unless --selftest is used")
    records = (decode_fd22_text(args.input.read_text()) if args.fd22_pages
               else decode_raw(args.input.read_bytes()))
    text = render_csv(records)
    if args.out:
        args.out.write_text(text)
        print("wrote %d records to %s" % (len(records), args.out))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
