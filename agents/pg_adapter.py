"""
法人日資料 端 read-only Postgres adapter。

連線到 台股開發2 啟動的 Postgres (port 5433)。提供 8 個 get_* API
分別對應 market.{prices,institutional,margin,lending,holders,valuation,
monthly_revenue,financials} 8 張表。

DSN 從環境變數 PG_DSN 取,預設值 postgresql://twstock:twstock_dev_pw@localhost:5433/twstock。
PG 不可達 → ConnectionError 拋出, caller (ta_features) 自行 fallback。
"""
from __future__ import annotations

import os
from typing import Optional

import pandas as pd
import psycopg  # type: ignore[import-not-found]

DEFAULT_DSN = "postgresql://twstock:twstock_dev_pw@localhost:5433/twstock"


class ConnectionError(Exception):
    """PG 連不上。Caller 應自行 fallback。"""


def _ticker_to_stock_id(ticker: str) -> str:
    """法人日資料 用 '2330.TW' / '5483.TWO',PG 用純股號 '2330' / '5483'。
    切尾用 endswith + slice,不用 rstrip(會誤砍)。"""
    if ticker.endswith(".TWO"):
        return ticker[:-4]
    if ticker.endswith(".TW"):
        return ticker[:-3]
    return ticker


class PGAdapter:
    """Read-only Postgres adapter。Lazy connect, reuse connection。"""

    def __init__(self, dsn: Optional[str] = None):
        self._dsn = dsn or os.environ.get("PG_DSN", DEFAULT_DSN)
        self._conn: Optional[psycopg.Connection] = None

    def _connect(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            try:
                self._conn = psycopg.connect(self._dsn, connect_timeout=5)
            except psycopg.OperationalError as e:
                raise ConnectionError(f"PG 連不上 ({self._dsn}): {e}") from e
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def get_ohlcv(
        self, ticker: str, start: str, end: str,
    ) -> pd.DataFrame:
        """讀 market.prices,回 date/open/high/low/close/volume DataFrame。"""
        return self._query_range(
            ticker, start, end, "market.prices",
            ["date", "open", "high", "low", "close", "volume"],
        )

    def get_institutional(
        self, ticker: str, start: str, end: str,
    ) -> pd.DataFrame:
        """三大法人買賣超(每日)。"""
        return self._query_range(
            ticker, start, end, "market.institutional",
            ["date", "foreign_buy", "foreign_sell", "foreign_net",
             "trust_buy", "trust_sell", "trust_net",
             "dealer_buy", "dealer_sell", "dealer_net", "total_net"],
        )

    def get_margin(
        self, ticker: str, start: str, end: str,
    ) -> pd.DataFrame:
        """融資融券餘額(每日)。"""
        return self._query_range(
            ticker, start, end, "market.margin",
            ["date", "margin_balance", "margin_buy", "margin_sell",
             "short_balance", "short_buy", "short_sell"],
        )

    def get_lending(
        self, ticker: str, start: str, end: str,
    ) -> pd.DataFrame:
        """借券餘額(每日)。"""
        return self._query_range(
            ticker, start, end, "market.lending",
            ["date", "lending_balance", "lending_short"],
        )

    def get_holders(
        self, ticker: str, start: str, end: str,
    ) -> pd.DataFrame:
        """千張大戶 + holders_count(週頻)。"""
        return self._query_range(
            ticker, start, end, "market.holders",
            ["date", "big_holders_pct", "holders_count"],
        )

    def get_valuation(
        self, ticker: str, start: str, end: str,
    ) -> pd.DataFrame:
        """PE/PB/殖利率(每日)。"""
        return self._query_range(
            ticker, start, end, "market.valuation",
            ["date", "pe", "pb", "dividend_yield"],
        )

    def get_monthly_revenue(self, ticker: str) -> pd.DataFrame:
        """月營收全歷史。"""
        stock_id = _ticker_to_stock_id(ticker)
        sql = "SELECT * FROM market.monthly_revenue WHERE stock_id = %s ORDER BY date"
        return self._exec_query(sql, (stock_id,))

    def get_financials(self, ticker: str) -> pd.DataFrame:
        """季財報全歷史(EPS/毛利率/營業利益率/淨利率)。"""
        stock_id = _ticker_to_stock_id(ticker)
        sql = "SELECT * FROM market.financials WHERE stock_id = %s ORDER BY date"
        return self._exec_query(sql, (stock_id,))

    def _query_range(
        self, ticker: str, start: str, end: str,
        table: str, columns: list[str],
    ) -> pd.DataFrame:
        """共用 SELECT ... WHERE stock_id = ? AND date BETWEEN ? AND ? ORDER BY date 樣板。"""
        stock_id = _ticker_to_stock_id(ticker)
        cols_sql = ", ".join(columns)
        sql = f"""
            SELECT {cols_sql}
            FROM {table}
            WHERE stock_id = %s AND date BETWEEN %s AND %s
            ORDER BY date
        """
        return self._exec_query(sql, (stock_id, start, end), columns=columns)

    def _exec_query(
        self, sql: str, params: tuple,
        columns: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if columns is None:
                # auto-detect from cursor.description
                desc = cur.description
                try:
                    columns = [d[0] for d in desc] if desc else []
                except TypeError:
                    columns = []
        # 若 columns 為空但 rows 有資料 (e.g. mock cursor.description 拿不到欄位),
        # 讓 pandas 自動以位置產生欄名,避免 column count mismatch。
        if not columns and rows:
            return pd.DataFrame(rows)
        return pd.DataFrame(rows, columns=columns)  # type: ignore[arg-type]
