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
        stock_id = _ticker_to_stock_id(ticker)
        sql = """
            SELECT date, open, high, low, close, volume
            FROM market.prices
            WHERE stock_id = %s AND date BETWEEN %s AND %s
            ORDER BY date
        """
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(sql, (stock_id, start, end))
            rows = cur.fetchall()
        return pd.DataFrame(
            rows, columns=["date", "open", "high", "low", "close", "volume"],  # type: ignore[arg-type]
        )
