"""Minimal smoke tests for pipeline.validate_record.

Run:
    python -m pytest tests/ -q     (if pytest installed)
    python tests/test_pipeline.py  (plain unittest fallback)
"""

import sys
import unittest
from pathlib import Path

# Make sibling pipeline.py importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pipeline


def valid_record():
    return {
        "date": "2026-04-18",
        "bull": "台積電,聯發科",
        "bear": "長榮,陽明",
        "rate": 172,
        "top5_margin_reduce_inst_buy": "台積電,富邦金,玉山金,中信金,國泰金",
    }


class TestValidateRecord(unittest.TestCase):
    def test_valid_passes(self):
        pipeline.validate_record(valid_record())

    def test_missing_field_raises(self):
        r = valid_record()
        del r["bull"]
        with self.assertRaises(ValueError) as cm:
            pipeline.validate_record(r)
        self.assertIn("缺少欄位", str(cm.exception))

    def test_bad_date_format_raises(self):
        for bad in ("2026/04/18", "26-04-18", "2026-4-18", "2026-13-01"):
            r = valid_record()
            r["date"] = bad
            with self.assertRaises(ValueError):
                pipeline.validate_record(r)

    def test_rate_must_be_int(self):
        r = valid_record()
        r["rate"] = "172"
        with self.assertRaises(ValueError):
            pipeline.validate_record(r)

    def test_rate_range(self):
        for bad in (99, 251, 0, -1):
            r = valid_record()
            r["rate"] = bad
            with self.assertRaises(ValueError):
                pipeline.validate_record(r)

    def test_rate_alert_field_rejected(self):
        r = valid_record()
        r["rate_alert"] = True
        with self.assertRaises(ValueError) as cm:
            pipeline.validate_record(r)
        self.assertIn("rate_alert", str(cm.exception))

    def test_bull_must_be_string(self):
        r = valid_record()
        r["bull"] = ["台積電", "聯發科"]
        with self.assertRaises(ValueError):
            pipeline.validate_record(r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
