#!/usr/bin/env python3
"""Independent self-test for did_callback_abi.py (read-only, no CAN)."""
from __future__ import annotations

import contextlib
import io

import did_callback_abi as abi


def pack_bp(byte_pointer: int, payload: bytes) -> dict[int, int]:
    """Model DSP56800E BP stores: even=word low octet, odd=high octet."""
    memory: dict[int, int] = {}
    for offset, value in enumerate(payload):
        bp = byte_pointer + offset
        wa = bp >> 1
        old = memory.get(wa, 0)
        if bp & 1:
            memory[wa] = (old & 0x00FF) | (value << 8)
        else:
            memory[wa] = (old & 0xFF00) | value
    return memory


def main() -> None:
    result = abi.verify()
    assert result["result"] == "ALL STATIC ABI CHECKS PASS"
    assert result["did_controls"]["FD0C"]["expected_length"] == 1
    assert result["did_controls"]["FD0E"]["expected_length"] == 1
    assert result["did_controls"]["FD08"]["expected_length"] == 2
    assert result["did_controls"]["FD20"]["expected_length"] == 12
    assert result["did_controls"]["FD22"]["expected_length"] == 15

    # ERM MOVE.BP example convention: sequential bytes AA BB CC starting at an
    # even byte pointer occupy words BBAA, 00CC.  Wire order remains AA BB CC.
    assert pack_bp(0x200, bytes.fromhex("AA BB CC")) == {
        0x100: 0xBBAA,
        0x101: 0x00CC,
    }
    # FD22 starts its OEM source at odd byte pointer 0x1DD9; crossing a word
    # boundary does not reorder the copied byte stream.
    assert pack_bp(0x1DD9, bytes.fromhex("11 22 33")) == {
        0x0EEC: 0x1100,
        0x0EED: 0x3322,
    }

    # Ensure normal execution has no accidental stdout from imported checks.
    with contextlib.redirect_stdout(io.StringIO()) as capture:
        abi.verify()
    assert capture.getvalue() == ""
    print("SELFTEST: ALL STATIC ABI AND BYTE-POINTER CHECKS PASS")


if __name__ == "__main__":
    main()
