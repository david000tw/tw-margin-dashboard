"""
agents/pg_adapter.py 測試。

Unit tests 用 mock psycopg 不需 PG。
Integration test 用 PG_DSN 環境變數連 real PG,連不上自動 skip。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from pg_adapter import PGAdapter, ConnectionError as PGConnError, _ticker_to_stock_id   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


class TestTickerConversion(unittest.TestCase):
    def test_twse_suffix(self):
        self.assertEqual(_ticker_to_stock_id("2330.TW"), "2330")

    def test_tpex_suffix(self):
        self.assertEqual(_ticker_to_stock_id("5483.TWO"), "5483")

    def test_no_suffix_returns_as_is(self):
        # 容錯:若 caller 已經傳純股號
        self.assertEqual(_ticker_to_stock_id("2330"), "2330")

    def test_rstrip_pitfall_avoided(self):
        # 確保不會用 rstrip 誤砍 — "5483.TWO" 不能被砍成 "5483.T" 之類
        self.assertEqual(_ticker_to_stock_id("5483.TWO"), "5483")


class TestPGAdapterMocked(unittest.TestCase):
    """用 mock psycopg, 不打實際 PG。"""

    @patch("pg_adapter.psycopg")
    def test_lazy_connect_first_query(self, mock_psycopg):
        # 第一次呼叫 get_ohlcv 才 connect
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            ("2024-01-02", 600.0, 605.0, 598.0, 602.0, 1000000),
            ("2024-01-03", 602.0, 610.0, 600.0, 608.0, 1200000),
        ]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_psycopg.connect.return_value = mock_conn

        adapter = PGAdapter(dsn="mock://fake")
        # connect 不應在 __init__ 觸發
        mock_psycopg.connect.assert_not_called()

        df = adapter.get_ohlcv("2330.TW", "2024-01-01", "2024-01-31")
        mock_psycopg.connect.assert_called_once_with("mock://fake", connect_timeout=5)
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df.columns), ["date", "open", "high", "low", "close", "volume"])

    @patch("pg_adapter.psycopg")
    def test_get_ohlcv_passes_stock_id_not_ticker(self, mock_psycopg):
        # 確認 SQL execute 時用 "2330",不是 "2330.TW"
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_psycopg.connect.return_value = mock_conn

        adapter = PGAdapter(dsn="mock://fake")
        adapter.get_ohlcv("2330.TW", "2024-01-01", "2024-01-31")

        # 驗 execute 第二個 arg (parameters) 第一個 = "2330" not "2330.TW"
        call_args = mock_cur.execute.call_args
        params = call_args[0][1]
        self.assertEqual(params[0], "2330")

    @patch("pg_adapter.psycopg")
    def test_empty_result_returns_empty_dataframe(self, mock_psycopg):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_psycopg.connect.return_value = mock_conn

        adapter = PGAdapter(dsn="mock://fake")
        df = adapter.get_ohlcv("9999.TW", "2024-01-01", "2024-01-31")
        self.assertEqual(len(df), 0)
        self.assertEqual(list(df.columns), ["date", "open", "high", "low", "close", "volume"])

    @patch("pg_adapter.psycopg")
    def test_connection_failure_raises_pgconnerror(self, mock_psycopg):
        import psycopg as real_psycopg  # type: ignore[import-not-found]
        # psycopg.connect raise → adapter 該 raise ConnectionError
        mock_psycopg.connect.side_effect = real_psycopg.OperationalError("fake fail")
        mock_psycopg.OperationalError = real_psycopg.OperationalError

        adapter = PGAdapter(dsn="mock://fake")
        with self.assertRaises(PGConnError):
            adapter.get_ohlcv("2330.TW", "2024-01-01", "2024-01-31")

    def test_dsn_from_env(self):
        with patch.dict(os.environ, {"PG_DSN": "postgresql://test@localhost/test"}):
            adapter = PGAdapter()
            self.assertEqual(adapter._dsn, "postgresql://test@localhost/test")

    def test_dsn_default_when_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            adapter = PGAdapter()
            self.assertIn("localhost:5433", adapter._dsn)


class TestPGAdapterOtherTables(unittest.TestCase):
    """驗其他 7 個 read API 的 SQL + DataFrame schema。"""

    def _mock_adapter_with_rows(self, mock_psycopg, rows):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_psycopg.connect.return_value = mock_conn
        return PGAdapter(dsn="mock://fake"), mock_cur

    @patch("pg_adapter.psycopg")
    def test_get_institutional(self, mock_psycopg):
        adapter, _ = self._mock_adapter_with_rows(
            mock_psycopg,
            [("2024-01-02", 1000, 500, 500, 200, 100, 100, 50, 30, 20, 620)],
        )
        df = adapter.get_institutional("2330.TW", "2024-01-01", "2024-01-31")
        self.assertEqual(len(df), 1)
        for col in ("date", "foreign_net", "trust_net", "dealer_net", "total_net"):
            self.assertIn(col, df.columns)

    @patch("pg_adapter.psycopg")
    def test_get_margin(self, mock_psycopg):
        adapter, _ = self._mock_adapter_with_rows(
            mock_psycopg,
            [("2024-01-02", 5000, 100, 50, 1000, 20, 10)],
        )
        df = adapter.get_margin("2330.TW", "2024-01-01", "2024-01-31")
        for col in ("date", "margin_balance", "margin_buy", "short_balance"):
            self.assertIn(col, df.columns)

    @patch("pg_adapter.psycopg")
    def test_get_lending(self, mock_psycopg):
        adapter, _ = self._mock_adapter_with_rows(
            mock_psycopg,
            [("2024-01-02", 200000, 5000)],
        )
        df = adapter.get_lending("2330.TW", "2024-01-01", "2024-01-31")
        for col in ("date", "lending_balance", "lending_short"):
            self.assertIn(col, df.columns)

    @patch("pg_adapter.psycopg")
    def test_get_holders(self, mock_psycopg):
        adapter, _ = self._mock_adapter_with_rows(
            mock_psycopg,
            [("2024-01-02", 0.65, 1234)],
        )
        df = adapter.get_holders("2330.TW", "2024-01-01", "2024-01-31")
        for col in ("date", "big_holders_pct", "holders_count"):
            self.assertIn(col, df.columns)

    @patch("pg_adapter.psycopg")
    def test_get_valuation(self, mock_psycopg):
        adapter, _ = self._mock_adapter_with_rows(
            mock_psycopg,
            [("2024-01-02", 25.0, 5.2, 1.8)],
        )
        df = adapter.get_valuation("2330.TW", "2024-01-01", "2024-01-31")
        for col in ("date", "pe", "pb", "dividend_yield"):
            self.assertIn(col, df.columns)

    @patch("pg_adapter.psycopg")
    def test_get_monthly_revenue(self, mock_psycopg):
        # monthly_revenue 沒 date range, 全歷史
        adapter, mock_cur = self._mock_adapter_with_rows(
            mock_psycopg,
            [("2024-01-31", 200000000)],
        )
        adapter.get_monthly_revenue("2330.TW")
        # SQL execute 應該只有 stock_id 一個參數
        call_args = mock_cur.execute.call_args
        self.assertEqual(len(call_args[0][1]), 1)

    @patch("pg_adapter.psycopg")
    def test_get_financials(self, mock_psycopg):
        adapter, _ = self._mock_adapter_with_rows(
            mock_psycopg,
            [("2024-03-31", 10.5, 0.65, 0.5, 0.45)],
        )
        df = adapter.get_financials("2330.TW")
        self.assertEqual(len(df), 1)


class TestPGAdapterIntegration(unittest.TestCase):
    """連 real PG。PG 連不上時 skip。"""

    @classmethod
    def setUpClass(cls):
        try:
            import psycopg  # type: ignore[import-not-found]
            dsn = os.environ.get("PG_DSN",
                                  "postgresql://twstock:twstock_dev_pw@localhost:5433/twstock")
            conn = psycopg.connect(dsn, connect_timeout=2)
            conn.close()
            cls._pg_alive = True
        except Exception:
            cls._pg_alive = False

    def setUp(self):
        if not self.__class__._pg_alive:
            self.skipTest("PG 不可達, 跳過 integration test")

    def test_real_ohlcv_query_for_2330(self):
        adapter = PGAdapter()
        df = adapter.get_ohlcv("2330.TW", "2024-01-01", "2024-12-31")
        self.assertGreater(len(df), 0, "2330 should have data in 2024")
        self.assertIn("close", df.columns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
