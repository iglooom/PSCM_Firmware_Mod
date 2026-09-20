#!/usr/bin/env python3
"""Unit tests for the deterministic FD22 snapshot VBF builder."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
import unittest

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "build_fd22_snapshot_vbf.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_fd22_snapshot_vbf", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuilderTests(unittest.TestCase):
    def test_handler_is_exactly_60_words(self):
        builder = load_builder()
        self.assertEqual(len(builder.HANDLER_WORDS), 60)
        self.assertEqual(builder.HANDLER_WORDS[:8],
                         (0xE080, 0xD0B6, 0xF07C, 0x2DDE,
                          0x8110, 0x5C28, 0xD0E6, 0x0001))
        self.assertEqual(builder.HANDLER_WORDS[-4:],
                         (0xD1E6, 0x000E, 0xE58F, 0xE708))

    def test_checksum_reference_vectors(self):
        builder = load_builder()
        self.assertEqual(builder.crc16_mcrf4xx(b"123456789"), 0x6F91)
        self.assertEqual(builder.sum16le(b"\x01\x00\x02\x00"), 3)

    def test_expect_rejects_version_drift(self):
        builder = load_builder()
        with self.assertRaisesRegex(builder.BuildError, "pointer"):
            builder.expect_bytes(b"\x00\x02", 0, b"\x00\x01", "pointer")

    def test_parse_and_stock_internal_checksums(self):
        builder = load_builder()
        stock = builder.parse_vbf(builder.DEFAULT_STOCK)
        cal = builder.parse_vbf(builder.DEFAULT_CAL)
        builder.verify_oem_inputs(stock, cal)
        block0 = stock.block_at(builder.BLK0_FLASH).data
        block1 = stock.block_at(builder.BLK1_FLASH).data
        self.assertEqual(builder.find_start_offset(block1), 0x1800)
        self.assertEqual(builder.calculate_word_a(block0, block1),
                         struct.unpack_from("<H", block1, builder.WORD_A_OFF)[0])
        self.assertEqual(builder.calculate_word_b(stock, cal, block1),
                         struct.unpack_from("<H", block1, builder.WORD_B_OFF)[0])

    def test_in_memory_build_has_only_permitted_payload_changes(self):
        builder = load_builder()
        stock = builder.parse_vbf(builder.DEFAULT_STOCK)
        cal = builder.parse_vbf(builder.DEFAULT_CAL)
        result = builder.build_bytes(stock, cal)
        built = builder.parse_vbf_bytes(result.output, Path("memory.vbf"))
        audit = builder.audit_build(stock, built, cal)
        self.assertEqual(audit.pointer_words_changed, 2)
        self.assertEqual(audit.cave_words_changed, 60)
        self.assertTrue(audit.word_a_valid)
        self.assertTrue(audit.word_b_valid)
        self.assertEqual(audit.unexpected_payload_bytes, 0)
        self.assertEqual(audit.unexpected_header_bytes, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
