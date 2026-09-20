#!/usr/bin/env python3
"""Reproduce the CV6T-AR lane-state selector lookup analysis.

Read-only: extracts the five 7-point tables from the 14C217 default-calibration
image, models P:$2256F's bracket/fraction outputs and the caller's Q8 linear
interpolation, and checks CV6T-AH/BV6T-AF controls.
"""
from __future__ import annotations

import argparse
import pathlib
import struct
import unittest
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[2]
P_BASE = 0xE000
AR_REL = "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin"
AH_REL = "bins/CV6T-14C217-AH/CV6T-14C217-AH_blk1_0x0001C000.bin"
BV_REL = "bins/BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin"

# The five runtime X tables are 0x15 words apart.  Their serialized OEM
# default-calibration image is contiguous at P:$0E489..$0E4F1.
X_TABLES = (0x03B7, 0x03CC, 0x03E1, 0x03F6, 0x040B)
P_TABLES = tuple(0x0E489 + 0x15 * i for i in range(5))
TABLE_WORDS = 0x15
POINTS = 7
EXPECTED_AXIS_U16 = (0xFDF4, 0xFEFA, 0xFF7D, 0x0000,
                     0x0083, 0x0106, 0x020C)
EXPECTED_VALUES = (0x0100,) * POINTS
EXPECTED_TRAILER = (0x5000, 0x966E, 0x0000, 0x0100,
                    0x0100, 0x0100, 0x0A00)
SELECTOR_PATTERN = (0xF07C, 0x2DDE, 0x4C01, 0xA203, 0xE6FF, 0x2D5E,
                    0xA907, 0x4C02, 0xA203, 0xE681, 0x2D5E,
                    0xA902, 0xE680, 0x2D5E)
HELPER_HEAD = (0x827B, 0xDD3F, 0xF016, 0x7876, 0x81A9, 0xA505,
               0x8643, 0x0000, 0x8649, 0x0000, 0xA931)
CALL_HEAD = (0x874A, 0x03B7, 0xF77C, 0x2D5F, 0x874B, 0x2D60,
             0x874C, 0x2D61, 0xE587, 0xE256, 0x256F)


def load(rel: str) -> tuple[int, ...]:
    data = (ROOT / rel).read_bytes()
    if len(data) % 2:
        raise ValueError(f"odd-sized firmware block: {rel}")
    return struct.unpack(f"<{len(data)//2}H", data)


def s16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def pwords(words: tuple[int, ...], address: int, count: int) -> tuple[int, ...]:
    start = address - P_BASE
    if start < 0 or start + count > len(words):
        raise IndexError(hex(address))
    return words[start:start + count]


def hexwords(words: tuple[int, ...]) -> str:
    return " ".join(f"{word:04X}" for word in words)


def occurrences(words: tuple[int, ...], pattern: tuple[int, ...]) -> list[int]:
    return [P_BASE + i for i in range(len(words) - len(pattern) + 1)
            if words[i:i + len(pattern)] == pattern]


@dataclass(frozen=True)
class Table:
    x_address: int
    p_address: int
    axis: tuple[int, ...]
    values: tuple[int, ...]
    trailer: tuple[int, ...]


def decode_tables(ar: tuple[int, ...]) -> list[Table]:
    tables = []
    for xa, pa in zip(X_TABLES, P_TABLES):
        raw = pwords(ar, pa, TABLE_WORDS)
        tables.append(Table(xa, pa, tuple(map(s16, raw[:POINTS])),
                            raw[POINTS:2 * POINTS], raw[2 * POINTS:]))
    return tables


def helper_2256f(axis: tuple[int, ...], x: int) -> tuple[int, int]:
    """Semantic model of P:$2256F: (lower index, unsigned Q8 fraction).

    Firmware clamps low to (0,0), high to (n-2,0xff), otherwise binary-searches
    for the bracketing pair and obtains 256*(x-lo)/(hi-lo) through P:$18A87.
    The exact divider's rounding is immaterial for these flat ordinate tables;
    floor division is used here as a deterministic representative.
    """
    if len(axis) < 2 or any(a >= b for a, b in zip(axis, axis[1:])):
        raise ValueError("axis must be strictly increasing")
    if x < axis[0]:
        return 0, 0
    if x >= axis[-1]:
        return len(axis) - 2, 0xFF
    lo, hi = 0, len(axis) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if x < axis[mid]:
            hi = mid
        else:
            lo = mid
    fraction = ((x - axis[lo]) << 8) // (axis[lo + 1] - axis[lo])
    return lo, min(fraction, 0xFF)


def interpolate_q8(table: Table, x: int) -> tuple[int, int, int]:
    index, fraction = helper_2256f(table.axis, x)
    y0, y1 = table.values[index:index + 2]
    # Equivalent mathematical form of P:$2B11B..$2B13B and its four clones.
    value = ((0x100 - fraction) * y0 + fraction * y1) >> 8
    return index, fraction, value


class SelectorLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ar, cls.ah, cls.bv = load(AR_REL), load(AH_REL), load(BV_REL)
        cls.tables = decode_tables(cls.ar)

    def test_raw_selector_and_helper_anchors(self) -> None:
        self.assertEqual(pwords(self.ar, 0x2B0E8, len(SELECTOR_PATTERN)),
                         SELECTOR_PATTERN)
        self.assertEqual(pwords(self.ar, 0x2256F, len(HELPER_HEAD)), HELPER_HEAD)
        self.assertEqual(pwords(self.ar, 0x2B110, len(CALL_HEAD)), CALL_HEAD)

    def test_five_serialized_tables(self) -> None:
        self.assertEqual([t.p_address for t in self.tables], list(P_TABLES))
        for table in self.tables:
            self.assertEqual(tuple(v & 0xFFFF for v in table.axis), EXPECTED_AXIS_U16)
            self.assertEqual(table.values, EXPECTED_VALUES)
            self.assertEqual(table.trailer, EXPECTED_TRAILER)
        self.assertEqual(len({(t.axis, t.values, t.trailer) for t in self.tables}), 1)

    def test_requested_direct_selector_values(self) -> None:
        # Direct x=-1,0,+1 exercises both sides and the exact centre knot.
        for x in (-1, 0, 1):
            self.assertEqual([interpolate_q8(t, x)[2] for t in self.tables],
                             [0x0100] * 5)

    def test_actual_selector_times_unknown_magnitude(self) -> None:
        # X:$2D5F is selector times a runtime magnitude.  Flat ordinates make
        # the result unity for every possible signed input, including clamps.
        probes = (-0x8000, -524, -1, 0, 1, 524, 0x7FFF)
        for x in probes:
            self.assertEqual([interpolate_q8(t, x)[2] for t in self.tables],
                             [0x0100] * 5)

    def test_controls_have_neither_selector_nor_axis(self) -> None:
        self.assertEqual(occurrences(self.ar, SELECTOR_PATTERN), [0x2B0E8])
        self.assertEqual(occurrences(self.ah, SELECTOR_PATTERN), [])
        self.assertEqual(occurrences(self.bv, SELECTOR_PATTERN), [])
        self.assertEqual(occurrences(self.ar, EXPECTED_AXIS_U16), list(P_TABLES))
        self.assertEqual(occurrences(self.ah, EXPECTED_AXIS_U16), [])
        self.assertEqual(occurrences(self.bv, EXPECTED_AXIS_U16), [])


def report() -> None:
    ar = load(AR_REL)
    tables = decode_tables(ar)
    print("CV6T-AR selector raw P:$2B0E8:",
          hexwords(pwords(ar, 0x2B0E8, len(SELECTOR_PATTERN))))
    print("P:$2256F helper head:", hexwords(pwords(ar, 0x2256F, 27)))
    for table in tables:
        raw = pwords(ar, table.p_address, TABLE_WORDS)
        print(f"X:${table.x_address:04X} <- default P:${table.p_address:05X}: "
              f"{hexwords(raw)}")
        print(f"  axis={table.axis} values={table.values} trailer={table.trailer}")
    print("\nDirect helper/interpolation results:")
    for x in (-1, 0, 1):
        details = [interpolate_q8(t, x) for t in tables]
        print(f"  x={x:+d}: " + ", ".join(
            f"table{i}:index={idx},fraction={frac},out=0x{value:04X}"
            for i, (idx, frac, value) in enumerate(details)))
    print("\nControl selector hits:")
    for label, rel in (("CV6T-AR", AR_REL), ("CV6T-AH", AH_REL),
                       ("BV6T-AF", BV_REL)):
        print(f"  {label}: {[f'P:${a:05X}' for a in occurrences(load(rel), SELECTOR_PATTERN)]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SelectorLookupTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
