"""
agents/ta_backfill.py 測試。實際 LLM call 不打,純驗 logic。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from ta_backfill import select_dates, read_checkpoint, write_checkpoint   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_merged():
    return [
        {"date": "2026-04-28", "rate": 175, "bull": "x", "bear": "y", "top5_margin_reduce_inst_buy": ""},
        {"date": "2026-04-29", "rate": 165, "bull": "x", "bear": "y", "top5_margin_reduce_inst_buy": ""},
        {"date": "2026-04-30", "rate": 180, "bull": "x", "bear": "y", "top5_margin_reduce_inst_buy": ""},
        {"date": "2026-05-04", "rate": 181, "bull": "x", "bear": "y", "top5_margin_reduce_inst_buy": ""},
    ]


class TestSelectDates(unittest.TestCase):
    def test_alert_only_filters_below_170(self):
        result = select_dates(
            fx_merged(), from_date="2026-04-01", to_date="2026-05-31",
            alert_only=True,
        )
        # 04-29 (rate=165) 不入選
        self.assertEqual(result, ["2026-04-28", "2026-04-30", "2026-05-04"])

    def test_no_alert_filter_returns_all_in_range(self):
        result = select_dates(
            fx_merged(), from_date="2026-04-01", to_date="2026-05-31",
            alert_only=False,
        )
        self.assertEqual(len(result), 4)

    def test_date_range_inclusive(self):
        result = select_dates(
            fx_merged(), from_date="2026-04-30", to_date="2026-04-30",
            alert_only=True,
        )
        self.assertEqual(result, ["2026-04-30"])


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "ck.json"

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()
        self.tmp.parent.rmdir()

    def test_no_checkpoint_returns_none(self):
        self.assertIsNone(read_checkpoint(self.tmp))

    def test_write_and_read_roundtrip(self):
        write_checkpoint(self.tmp, "2026-04-29", {"appended": 5})
        ck = read_checkpoint(self.tmp)
        self.assertEqual(ck["last_completed"], "2026-04-29")
        self.assertEqual(ck["stats"]["appended"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
