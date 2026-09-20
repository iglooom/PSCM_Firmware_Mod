#!/usr/bin/env python3
import csv
import io
import unittest

import fd22_snapshot_logger as logger


class DecodeTests(unittest.TestCase):
    def test_decodes_complete_positive_response(self):
        raw = bytes.fromhex("62 FD 22 00 0004 0005 FFFE 8000 7FFF 0000 1234")
        row = logger.decode_response(raw)
        self.assertEqual(row["format"], 0)
        self.assertEqual(row["lane_state"], 4)
        self.assertEqual(row["per_state_code"], 5)
        self.assertEqual(row["torque_accumulator_raw"], 0xFFFE)
        self.assertEqual(row["torque_accumulator_signed"], -2)
        self.assertEqual(row["upstream_intermediate_raw"], 0x1234)

    def test_rejects_negative_or_malformed_response(self):
        for raw in (None, b"", bytes.fromhex("7F2231"), bytes.fromhex("62FD2200"),
                    bytes.fromhex("62FD2201" + "0000" * 7)):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    logger.decode_response(raw)


class LoggingTests(unittest.TestCase):
    def test_records_success_timeout_and_error_without_sending_other_service(self):
        replies = iter([
            bytes.fromhex("62FD22000004000500010002000300040005"),
            None,
            bytes.fromhex("7F2231"),
        ])
        requests = []
        def request(payload, timeout):
            requests.append((payload, timeout))
            return next(replies)

        out = io.StringIO()
        logger.collect(request, out, rate_hz=10, count=3, label="LCA-left",
                       clock_ns=iter((1_000_000_000, 1_100_000_000, 1_200_000_000)).__next__,
                       sleeper=lambda _: None)
        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual([r["status"] for r in rows], ["ok", "timeout", "error"])
        self.assertEqual(rows[0]["label"], "LCA-left")
        self.assertEqual(rows[0]["lane_state"], "4")
        self.assertEqual(rows[0]["raw_response"], "62fd22000004000500010002000300040005")
        self.assertEqual(requests, [("22FD22", 0.5)] * 3)

    def test_count_zero_is_rejected(self):
        with self.assertRaises(ValueError):
            logger.collect(lambda *_: None, io.StringIO(), 10, 0, "")


if __name__ == "__main__":
    unittest.main()
