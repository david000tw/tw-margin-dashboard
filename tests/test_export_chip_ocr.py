"""
scripts/export_chip_ocr_to_pg.py 測試。

純測 UPSERT 邏輯,mock psycopg。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from export_chip_ocr_to_pg import build_upsert_rows, run_export   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_merged() -> list[dict]:
    return [
        {"date": "2024-01-02", "rate": 165, "bull": "2330,2454", "bear": "1101",
         "top5_margin_reduce_inst_buy": "2330"},
        {"date": "2024-01-03", "rate": 172, "bull": "2330", "bear": "1101,1102",
         "top5_margin_reduce_inst_buy": "3034"},
        {"date": "2024-01-05", "rate": 168, "bull": "", "bear": "",
         "top5_margin_reduce_inst_buy": ""},
    ]


class TestBuildUpsertRows(unittest.TestCase):
    def test_converts_to_tuples(self):
        rows = build_upsert_rows(fx_merged())
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], ("2024-01-02", 165, "2330,2454", "1101", "2330"))

    def test_handles_empty_chip_fields(self):
        rows = build_upsert_rows(fx_merged())
        self.assertEqual(rows[2], ("2024-01-05", 168, "", "", ""))

    def test_since_filter(self):
        rows = build_upsert_rows(fx_merged(), since="2024-01-03")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "2024-01-03")


class TestRunExport(unittest.TestCase):
    @patch("export_chip_ocr_to_pg.psycopg")
    def test_executes_upsert_sql(self, mock_psycopg):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_psycopg.connect.return_value = mock_conn

        stats = run_export(
            merged=fx_merged(),
            dsn="mock://fake",
            since=None,
        )

        self.assertTrue(mock_cur.executemany.called)
        self.assertTrue(mock_conn.commit.called)
        self.assertEqual(stats["upserted"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
