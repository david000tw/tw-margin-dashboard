# 台股開發2 Postgres 整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 法人日資料 端加 read-only PG adapter，連到 台股開發2 已建好的 Postgres，撈 OHLCV 強化 Market Analyst，可選把 OCR 結果回寫進 PG。

**Architecture:** 共用 Postgres (容器在 台股開發2 啟動，port 5433)。法人日資料 寫 `agents/pg_adapter.py` 用 psycopg read-only 讀 8 張 market.* 表。`price_features` 改造：嘗試從 PG 拉 OHLCV、算 ATR/跳空/K線/量價；PG 不可達 → fallback 到既有 close-only 模式。程度 2 加 `market.chip_ocr` 表 + 單向 export script。

**Tech Stack:** Python 3.8+、unittest、`psycopg[binary]>=3.1`、Postgres 16 (台股開發2 docker-compose)、yfinance (台股開發2 seed)。

**Spec:** `docs/superpowers/specs/2026-05-16-pg-integration-design.md`

---

## File Structure

```
agents/
  pg_adapter.py        新 — PGAdapter class, lazy connect, 8 個 get_* API
  ta_features.py       改 — price_features 嘗試 PG OHLCV, 算 ATR/跳空/量價/K線
  ta_prompts.py        改 — _format_price 顯示新指標

scripts/
  start_pg.bat         新 — Windows 一鍵啟 docker + healthcheck
  start_pg.sh          新 — Linux/Mac 同等版
  export_chip_ocr_to_pg.py  新 — 程度 2: merged.json → market.chip_ocr

tests/
  test_pg_adapter.py     新 — unit (mock psycopg) + integration (skip if PG dead)
  test_ta_features.py    改 — 加 ATR/gap/volume/candle + PG fallback 測試
  test_export_chip_ocr.py 新 — 程度 2 同步邏輯測試

台股開發2/db/
  05_chip_ocr_schema.sql  新 — market.chip_ocr 表定義 (程度 2)

.env (gitignored, 新):
  PG_DSN=postgresql://twstock:twstock_dev_pw@localhost:5433/twstock

.gitignore (改):
  + .env

requirements 或 pyproject.toml (改):
  + psycopg[binary]>=3.1
```

實作順序（依依賴）：

1. Postgres 啟動 + seed（人工執行，2-3 hr 等資料）
2. `pg_adapter.py` 連線 + stock_id 轉換 + get_ohlcv
3. `pg_adapter.py` 其他 7 個 read API
4. OHLCV 指標 helpers (ATR/gap/volume/candle) — 純函式
5. `price_features` 改造串 PG + fallback
6. `_format_price` 顯示新指標
7. **程度 2**: `market.chip_ocr` schema
8. **程度 2**: `export_chip_ocr_to_pg.py`
9. **程度 2** (optional): 整合進 daily-fetch
10. 重跑 PoC 評估 OHLCV-強化的 Market Analyst

---

### Task 1: 啟動 Postgres + seed 1971 檔 universe

**Files:** 無 code 變更，純人工執行 + 文件記錄

這是 Phase 1 的 setup task，需要在 台股開發2 那邊跑指令，等資料抓完。**這個 task 不適合 subagent 派發 — 是人工 step。**

- [ ] **Step 1: 確認 Docker 在跑**

Run:
```
docker --version
docker info
```

Expected: 看到 Docker version 與 daemon info；若 daemon 連不上需先啟 Docker Desktop。

- [ ] **Step 2: 啟動 Postgres 容器**

Run（PowerShell）:
```
cd C:\Users\yen\Desktop\台股開發2
docker-compose up -d
```

Expected:
```
[+] Running 2/2
 ✔ Network ...  Created
 ✔ Container twstock-postgres  Started
```

驗證容器健康（等 ~10 秒）：
```
docker ps --filter "name=twstock-postgres" --format "{{.Status}}"
```
Expected: `Up X seconds (healthy)`

- [ ] **Step 3: 確認 schema 已 apply**

```
docker exec -it twstock-postgres psql -U twstock -d twstock -c "\dt market.*"
```

Expected: 列出 `market.universe`, `market.prices`, `market.institutional`, `market.margin`, `market.lending`, `market.holders`, `market.valuation`, `market.monthly_revenue` 等表（DDL 從 `db/*.sql` 自動 apply 過）。

- [ ] **Step 4: 跑 seed_full_universe.py**

```
cd C:\Users\yen\Desktop\台股開發2
$env:REPO_BACKEND="postgres"
uv run python scripts/seed_full_universe.py
```

Expected:
- 列出 ~1971 檔 ticker
- 對每檔逐一抓 yfinance（每檔 3-5 秒）
- 總時間 **2-3 小時**
- 結束顯示 `seed 完成` 之類訊息

**踩到雷的話**：
- `uv` 沒裝 → 用 `pip install -e .` + `python scripts/seed_full_universe.py`
- yfinance rate limit → script 內已有 `SLEEP_SEC=1.0` 應對；偶爾失敗會 graceful skip 該股
- Docker daemon 中途斷 → 重啟 Docker、`docker-compose up -d`、重跑 seed（idempotent UPSERT）

- [ ] **Step 5: 驗證資料進去了**

```
docker exec -it twstock-postgres psql -U twstock -d twstock -c "SELECT COUNT(*) FROM market.prices;"
docker exec -it twstock-postgres psql -U twstock -d twstock -c "SELECT stock_id, MIN(date), MAX(date), COUNT(*) FROM market.prices WHERE stock_id IN ('2330','1101','2303') GROUP BY stock_id;"
```

Expected:
- `market.prices` 總筆數 ~1.5M-2M（1971 檔 × 1290 天）
- 2330/1101/2303 各 ~1290 個 row，date 範圍涵蓋 2021-01 到最近交易日

- [ ] **Step 6: 不需 commit（純執行步驟）**

但建議在 `法人日資料` 端寫一行紀錄：

```bash
echo "PG seed completed: $(date)" >> reports/pg_seed_log.txt
```

讓未來知道何時 seed 過。

---

### Task 2: pg_adapter base + get_ohlcv

**Files:**
- Create: `agents/pg_adapter.py`
- Test: `tests/test_pg_adapter.py`
- Modify: `.gitignore` (+`.env`)
- Document: `.env.example` 新增

第一個 read API。重點：lazy connect、stock_id 格式轉換、ConnectionError 處理。

- [ ] **Step 1: 安裝 psycopg**

```
pip install "psycopg[binary]>=3.1"
```

Expected: 裝完成，`python -c "import psycopg; print(psycopg.__version__)"` 顯示版本。

- [ ] **Step 2: Write the failing test**

Create `tests/test_pg_adapter.py`:

```python
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
        # 也測試 ".TW" 結尾不是上市那種:不該被誤判
        # (這 case 在 yfinance 不會出現,但驗 endswith 邏輯嚴謹)


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
        mock_psycopg.connect.assert_called_once_with("mock://fake")
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
        import psycopg as real_psycopg
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


class TestPGAdapterIntegration(unittest.TestCase):
    """連 real PG。PG 連不上時 skip。"""

    @classmethod
    def setUpClass(cls):
        try:
            import psycopg
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
```

- [ ] **Step 3: Run test to verify it fails**

```
python -m unittest tests.test_pg_adapter -v
```

Expected: ImportError (pg_adapter not defined).

- [ ] **Step 4: Implement pg_adapter base + get_ohlcv**

Create `agents/pg_adapter.py`:

```python
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
import psycopg

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
            rows, columns=["date", "open", "high", "low", "close", "volume"],
        )
```

- [ ] **Step 5: Run test to verify it passes**

```
python -m unittest tests.test_pg_adapter -v
```

Expected:
- TestTickerConversion: 4/4 PASS
- TestPGAdapterMocked: 6/6 PASS
- TestPGAdapterIntegration: 1 PASS（若 PG alive）或 skip

- [ ] **Step 6: 加 .env.example + .gitignore**

Create `.env.example`（這個會 commit）：

```
# Postgres 連線(供 agents/pg_adapter.py 讀)
# 真實 .env 不要 commit (在 .gitignore 中)
PG_DSN=postgresql://twstock:twstock_dev_pw@localhost:5433/twstock
```

Modify `.gitignore`（加 `.env` 行）：

```
.env
```

- [ ] **Step 7: Commit**

```
git add agents/pg_adapter.py tests/test_pg_adapter.py .env.example .gitignore
git commit -m "feat(pg): PGAdapter base + get_ohlcv + lazy connect"
```

---

### Task 3: pg_adapter 剩餘 7 個 read API

**Files:**
- Modify: `agents/pg_adapter.py`（append）
- Modify: `tests/test_pg_adapter.py`（append）

加 `get_institutional` / `get_margin` / `get_lending` / `get_holders` / `get_valuation` / `get_monthly_revenue` / `get_financials`。每個都是 SELECT-WHERE-ORDER 的薄殼，邏輯重複，TDD 一次寫完。

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ta_pg_adapter.py` ... 等等先確認 test file 名稱是 `tests/test_pg_adapter.py`，append 到該檔。

Append:

```python
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
        adapter, mock_cur = self._mock_adapter_with_rows(
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
        df = adapter.get_monthly_revenue("2330.TW")
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
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_pg_adapter.TestPGAdapterOtherTables -v
```

Expected: AttributeError (`get_institutional` etc not defined).

- [ ] **Step 3: Implement 7 個 API**

Append to `agents/pg_adapter.py`:

```python
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
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if columns is None:
                # auto-detect from cursor.description
                columns = [desc[0] for desc in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=columns)
```

也把 `get_ohlcv` 重構成用 `_query_range`：

修改 `get_ohlcv`：

```python
    def get_ohlcv(
        self, ticker: str, start: str, end: str,
    ) -> pd.DataFrame:
        """讀 market.prices,回 date/open/high/low/close/volume DataFrame。"""
        return self._query_range(
            ticker, start, end, "market.prices",
            ["date", "open", "high", "low", "close", "volume"],
        )
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_pg_adapter -v
```

Expected: 之前 11 個 + 新 7 個 = 18/18 PASS（含 1 integration test if PG alive，否則 skip）。

- [ ] **Step 5: Commit**

```
git add agents/pg_adapter.py tests/test_pg_adapter.py
git commit -m "feat(pg): 7 個 read API (institutional/margin/lending/holders/valuation/monthly_revenue/financials)"
```

---

### Task 4: OHLCV 指標 helpers (ATR / gap / volume / candle)

**Files:**
- Modify: `agents/ta_features.py`（在 `_macd` 之後 append helpers）
- Modify: `tests/test_ta_features.py`（加新測試類別）

純函式 helper，不碰 PG。輸入 numpy/list/DataFrame，輸出單一數字或 list。

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ta_features.py`:

```python
class TestATR(unittest.TestCase):
    def test_atr_basic(self):
        from ta_features import _atr   # type: ignore[import-not-found]
        # 5 日 OHLC, true range 計算對
        # day1: H-L = 10
        # day2: max(H-L=12, |H-prev_C|=11, |L-prev_C|=2) = 12
        # day3: max(8, 10, 5) = 10
        # 平均 ~ (10+12+10) / 3 ≈ 10.67 (3 日 ATR)
        highs = [105, 112, 108]
        lows  = [95, 100, 100]
        closes= [100, 110, 102]
        atr = _atr(highs, lows, closes, period=3)
        self.assertGreater(atr, 0)
        self.assertLess(atr, 20)

    def test_atr_insufficient_data(self):
        from ta_features import _atr   # type: ignore[import-not-found]
        # 不足 period → 回 0 或 None
        atr = _atr([100], [99], [99.5], period=14)
        self.assertIsNone(atr)


class TestGapCount(unittest.TestCase):
    def test_gap_threshold(self):
        from ta_features import _gap_count   # type: ignore[import-not-found]
        # opens 跟前日 close 比偏離 > 0.5%
        opens =  [100, 105, 103, 110, 102]   # 跳空 4 次 (105 vs 100=+5%, 103 vs 110=-6%, 110 vs 103=+7%, 102 vs 110=-7%)
        closes = [100, 100, 110, 103, 110]
        # day1 open 沒 prev close 比, skip
        # day2 open 105 vs day1 close 100 → +5% → gap
        # day3 open 103 vs day2 close 100 → +3% → gap
        # day4 open 110 vs day3 close 110 → 0% → no gap
        # day5 open 102 vs day4 close 103 → -1% → gap (>0.5%)
        count = _gap_count(opens, closes, threshold=0.005)
        self.assertEqual(count, 3)

    def test_no_gaps(self):
        from ta_features import _gap_count   # type: ignore[import-not-found]
        # 所有 open ≈ 前日 close → 0 gaps
        opens =  [100, 100.1, 99.9, 100.2, 100.0]
        closes = [100, 100, 100, 100, 100]
        count = _gap_count(opens, closes, threshold=0.005)
        self.assertEqual(count, 0)


class TestVolumeRatio(unittest.TestCase):
    def test_vol_ratio_5_20(self):
        from ta_features import _vol_ratio   # type: ignore[import-not-found]
        # 5 日平均 vs 20 日平均
        vols = [1_000_000] * 15 + [2_000_000] * 5  # 後 5 日量翻倍
        avg5, avg20, ratio = _vol_ratio(vols)
        self.assertAlmostEqual(avg5, 2_000_000)
        self.assertAlmostEqual(avg20, 1_250_000)
        self.assertAlmostEqual(ratio, 1.6)

    def test_vol_ratio_insufficient_data(self):
        from ta_features import _vol_ratio   # type: ignore[import-not-found]
        avg5, avg20, ratio = _vol_ratio([100, 200])
        self.assertIsNone(avg5)


class TestCandlePattern(unittest.TestCase):
    def test_hammer(self):
        from ta_features import _candle_pattern   # type: ignore[import-not-found]
        # 錘頭:實體小 + 下影線長 + 收紅
        # open=98, close=100 (實體 2), high=101, low=90 (下影線 8)
        pattern = _candle_pattern(open_=98, high=101, low=90, close=100)
        self.assertEqual(pattern, "錘頭")

    def test_doji(self):
        from ta_features import _candle_pattern   # type: ignore[import-not-found]
        # 十字:實體 < 全長 10%
        pattern = _candle_pattern(open_=100, high=105, low=95, close=100.2)
        self.assertEqual(pattern, "十字")

    def test_normal_candle(self):
        from ta_features import _candle_pattern   # type: ignore[import-not-found]
        # 一般 K 線(無特殊型態)
        pattern = _candle_pattern(open_=100, high=103, low=99, close=102)
        self.assertIsNone(pattern)
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_ta_features.TestATR tests.test_ta_features.TestGapCount tests.test_ta_features.TestVolumeRatio tests.test_ta_features.TestCandlePattern -v
```

Expected: ImportError on 4 functions.

- [ ] **Step 3: Implement 4 helpers**

Append to `agents/ta_features.py` (after `_macd`):

```python
# ── OHLCV 指標 helpers (Task 4) ──────────────────────────────


def _atr(
    highs: list[float], lows: list[float], closes: list[float],
    period: int = 14,
) -> float | None:
    """Average True Range,需要 highs/lows/closes 各 >= period+1 個元素。
    TR = max(high-low, |high-prev_close|, |low-prev_close|)。
    """
    if len(highs) < period or len(lows) < period or len(closes) < period:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # 取最後 period 個 TR 平均
    return sum(trs[-period:]) / period


def _gap_count(
    opens: list[float], closes: list[float], threshold: float = 0.005,
) -> int:
    """窗內跳空次數(open vs 昨日 close 偏離 > threshold)。
    threshold 預設 0.5%。"""
    count = 0
    for i in range(1, len(opens)):
        prev_close = closes[i - 1]
        if prev_close == 0:
            continue
        deviation = abs(opens[i] - prev_close) / prev_close
        if deviation > threshold:
            count += 1
    return count


def _vol_ratio(
    volumes: list[float],
) -> tuple[float | None, float | None, float | None]:
    """回 (avg5, avg20, ratio)。需要 >= 20 個元素,不足回 (None, None, None)。"""
    if len(volumes) < 20:
        return None, None, None
    avg5 = sum(volumes[-5:]) / 5
    avg20 = sum(volumes[-20:]) / 20
    ratio = avg5 / avg20 if avg20 > 0 else None
    return avg5, avg20, ratio


def _candle_pattern(
    open_: float, high: float, low: float, close: float,
) -> str | None:
    """單根 K 線型態識別:錘頭 / 十字 / None。

    錘頭(hammer): 實體 < 30% 全長 + 下影線 > 60% 全長 + 收紅(close > open)
    十字(doji): 實體 < 10% 全長
    其他: None
    """
    total_range = high - low
    if total_range <= 0:
        return None
    body = abs(close - open_)
    body_pct = body / total_range

    if body_pct < 0.10:
        return "十字"

    upper_shadow = high - max(open_, close)
    lower_shadow = min(open_, close) - low
    lower_shadow_pct = lower_shadow / total_range

    if body_pct < 0.30 and lower_shadow_pct > 0.60 and close > open_:
        return "錘頭"

    return None
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_ta_features -v 2>&1 | tail -10
```

Expected: 既有 29 tests + 9 個新 = 38/38 PASS。

- [ ] **Step 5: Commit**

```
git add agents/ta_features.py tests/test_ta_features.py
git commit -m "feat(ta-lite): OHLCV indicator helpers — ATR/gap/volume/candle pattern"
```

---

### Task 5: price_features OHLCV 整合 + fallback

**Files:**
- Modify: `agents/ta_features.py`（修 `price_features`）
- Modify: `tests/test_ta_features.py`（加 OHLCV integration + fallback tests）

`price_features` 改造：嘗試從 PG 拉 OHLCV，算新指標；PG 不可達退回現有 close-only 模式。

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ta_features.py`:

```python
class TestPriceFeaturesOHLCV(unittest.TestCase):
    """price_features 加 OHLCV 後的新欄位 + PG fallback 測試。"""

    def _mock_pg_returning_ohlcv(self):
        """Mock PGAdapter 回 5 日 OHLCV DataFrame。"""
        from unittest.mock import MagicMock
        import pandas as pd
        adapter = MagicMock()
        adapter.get_ohlcv.return_value = pd.DataFrame({
            "date": pd.to_datetime([
                "2024-01-02", "2024-01-03", "2024-01-04",
                "2024-01-05", "2024-01-08",
            ]).date,
            "open":   [600, 605, 612, 610, 615],
            "high":   [610, 613, 618, 622, 625],
            "low":    [598, 603, 608, 605, 612],
            "close":  [605, 612, 615, 618, 622],
            "volume": [1_000_000] * 5,
        })
        return adapter

    def test_price_features_with_ohlcv_has_new_fields(self):
        from ta_features import price_features   # type: ignore[import-not-found]
        adapter = self._mock_pg_returning_ohlcv()
        f = price_features(
            "2330.TW", "2024-01-10", fx_prices(), fx_twii(),
            window=5, pg_adapter=adapter,
        )
        # OHLCV 路徑時應有這些新欄位
        self.assertTrue(f["ohlcv_available"])
        self.assertIsNotNone(f["atr14"])  # 雖然 ATR 5 日不足 14 但回 None ok
        self.assertIn("gap_count_window", f)
        self.assertIn("vol_avg_5", f)
        self.assertIn("candle_pattern", f)

    def test_price_features_fallback_to_close_only(self):
        from ta_features import price_features   # type: ignore[import-not-found]
        from unittest.mock import MagicMock
        from pg_adapter import ConnectionError as PGConnError   # type: ignore[import-not-found]
        adapter = MagicMock()
        adapter.get_ohlcv.side_effect = PGConnError("fake fail")
        f = price_features(
            "2330.TW", "2024-01-15", fx_prices(), fx_twii(),
            window=5, pg_adapter=adapter,
        )
        # Fallback: ohlcv_available=False, OHLCV-only 欄位是 None
        self.assertFalse(f["ohlcv_available"])
        self.assertIsNone(f["atr14"])
        self.assertIsNone(f["candle_pattern"])
        # 但既有的 close-only 欄位仍正常
        self.assertEqual(f["closes"], [615, 612, 618, 620, 625])
        self.assertAlmostEqual(f["ma5"], 618.0, places=2)

    def test_price_features_no_pg_adapter_uses_close_only(self):
        from ta_features import price_features   # type: ignore[import-not-found]
        # 不傳 pg_adapter → 等同 PG 不可達, 走 close-only
        f = price_features(
            "2330.TW", "2024-01-15", fx_prices(), fx_twii(),
            window=5,
        )
        self.assertFalse(f["ohlcv_available"])
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_ta_features.TestPriceFeaturesOHLCV -v
```

Expected: TypeError (`price_features` 不接受 `pg_adapter` 參數) or KeyError (`ohlcv_available` 不存在).

- [ ] **Step 3: Modify price_features**

In `agents/ta_features.py`，修 `price_features` signature 加 `pg_adapter` param，並修 body 加 OHLCV path：

```python
def price_features(
    ticker: str, d: str, prices: dict, twii: dict[str, float],
    *, window: int = 60, pg_adapter=None,
) -> dict | None:
    """
    對 ticker 在 d 之前 window 個交易日的價格特徵。

    嘗試從 pg_adapter 拉 OHLCV (若提供)。失敗 / 缺 → fallback close-only。

    回傳 (見既有 docstring 加上):
      ohlcv_available    True=用 PG OHLCV 路徑; False=用既有 close-only
      atr14              ATR(14), len<14 回 None
      atr_pct_of_close   ATR / 現價 * 100
      gap_count_window   窗內跳空次數
      vol_avg_5, vol_avg_20, vol_ratio_5_20
      candle_pattern     最後一根 K 線型態
    """
    # 先嘗試 OHLCV 路徑
    if pg_adapter is not None:
        try:
            return _price_features_from_ohlcv(
                ticker, d, pg_adapter, twii, window=window,
                prices=prices,  # fallback 用
            )
        except Exception:
            pass  # PG 失敗 → fallback close-only

    # Fallback: close-only (既有邏輯)
    result = _price_features_close_only(ticker, d, prices, twii, window=window)
    if result is None:
        return None
    # 補上 OHLCV-only 欄位 = None
    result.update({
        "ohlcv_available": False,
        "atr14": None,
        "atr_pct_of_close": None,
        "gap_count_window": None,
        "vol_avg_5": None,
        "vol_avg_20": None,
        "vol_ratio_5_20": None,
        "candle_pattern": None,
    })
    return result


def _price_features_close_only(
    ticker: str, d: str, prices: dict, twii: dict[str, float],
    *, window: int = 60,
) -> dict | None:
    """既有 close-only 邏輯,從 prices dict 拉。
    (這就是 Task 7 之前 price_features 整個函式 body — 不動)"""
    # 把既有 price_features 函式 body 整段搬進來,return 整個 dict
    # ... (原邏輯不動,只是搬位置 + 改名)


def _price_features_from_ohlcv(
    ticker: str, d: str, pg_adapter, twii: dict[str, float],
    *, window: int = 60, prices: dict,
) -> dict:
    """OHLCV 路徑:從 PG 拉 OHLCV 算所有指標。"""
    # 抓 window+5 天 buffer 給 ATR 用
    from datetime import datetime, timedelta
    end_dt = datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)
    start_dt = end_dt - timedelta(days=int(window * 1.5))   # 多抓避免遇假日
    ohlcv = pg_adapter.get_ohlcv(
        ticker, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"),
    )
    if ohlcv.empty or len(ohlcv) < window:
        # PG 沒夠資料 → fallback
        result = _price_features_close_only(ticker, d, prices, twii, window=window)
        if result is None:
            return None
        result.update({
            "ohlcv_available": False,
            "atr14": None, "atr_pct_of_close": None,
            "gap_count_window": None,
            "vol_avg_5": None, "vol_avg_20": None, "vol_ratio_5_20": None,
            "candle_pattern": None,
        })
        return result

    ohlcv = ohlcv.tail(window).reset_index(drop=True)
    opens   = ohlcv["open"].tolist()
    highs   = ohlcv["high"].tolist()
    lows    = ohlcv["low"].tolist()
    closes  = ohlcv["close"].tolist()
    volumes = [float(v) for v in ohlcv["volume"].tolist()]

    # 既有指標 (從 closes 算)
    ma5 = _mean(closes[-5:]) if len(closes) >= 5 else _mean(closes)
    ma20 = _mean(closes[-20:]) if len(closes) >= 20 else _mean(closes)
    ma_window = _mean(closes)
    return_window = (closes[-1] / closes[0]) - 1 if closes[0] else 0.0
    window_start = ohlcv["date"].iloc[0].isoformat()
    window_end = ohlcv["date"].iloc[-1].isoformat()
    bias_ma20 = ((closes[-1] - ma20) / ma20 * 100) if ma20 else None
    macd = _macd(closes)
    macd_dif, macd_signal, macd_hist = (macd if macd is not None else (None, None, None))

    twii_start_v = twii.get(window_start)
    twii_end_v = twii.get(window_end)
    twii_return_window: float | None
    excess_return_window: float | None
    if twii_start_v and twii_end_v:
        twii_return_window = (twii_end_v / twii_start_v) - 1
        excess_return_window = return_window - twii_return_window
    else:
        twii_return_window = None
        excess_return_window = None

    # OHLCV-only 新指標
    atr14 = _atr(highs, lows, closes, period=14)
    atr_pct = (atr14 / closes[-1] * 100) if (atr14 is not None and closes[-1]) else None
    gap_count = _gap_count(opens, closes, threshold=0.005)
    vol_avg_5, vol_avg_20, vol_ratio = _vol_ratio(volumes)
    last_pattern = _candle_pattern(
        opens[-1], highs[-1], lows[-1], closes[-1],
    )

    return {
        "window_start": window_start,
        "window_end": window_end,
        "closes": closes,
        "ma5": ma5, "ma20": ma20, "ma_window": ma_window,
        "return_window": return_window,
        "twii_return_window": twii_return_window,
        "excess_return_window": excess_return_window,
        "bias_ma20": bias_ma20,
        "macd_dif": macd_dif, "macd_signal": macd_signal, "macd_hist": macd_hist,
        "ohlcv_available": True,
        "atr14": atr14,
        "atr_pct_of_close": atr_pct,
        "gap_count_window": gap_count,
        "vol_avg_5": vol_avg_5, "vol_avg_20": vol_avg_20,
        "vol_ratio_5_20": vol_ratio,
        "candle_pattern": last_pattern,
    }
```

**重要**：保留既有 `price_features` 函式 body 搬進 `_price_features_close_only`，function 名換但邏輯一字不改。

也修 `collect()` 在 `agents/ta_features.py` 末段加 `pg_adapter` 參數傳遞：

```python
def collect(
    *, symbol: str, ticker: str, d: str,
    merged: list[dict], prices: dict, twii: dict[str, float],
    prediction_rows: list[dict],
    chip_window: int = 60, price_window: int = 60, market_window: int = 30,
    lessons: list[dict] | None = None,
    pg_adapter=None,   # 新增
) -> SymbolFeatures:
    """組裝單一 symbol 在 d 的完整 feature。嚴格 walk-forward。
    pg_adapter: 若提供,price_features 嘗試從 PG 拉 OHLCV 算 ATR/跳空 等。"""
    return SymbolFeatures(
        symbol=symbol,
        ticker=ticker,
        target_date=d,
        chip=chip_features(symbol, d, merged, window=chip_window),
        price=price_features(
            ticker, d, prices, twii, window=price_window, pg_adapter=pg_adapter,
        ),
        past_perf=past_perf(symbol, d, prediction_rows),
        market_context=market_context(d, merged, twii, n_recent=market_window),
        lessons=lessons or [],
    )
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_ta_features -v 2>&1 | tail -10
```

Expected: 既有 38 + 新 3 = 41/41 PASS。

- [ ] **Step 5: Commit**

```
git add agents/ta_features.py tests/test_ta_features.py
git commit -m "feat(ta-lite): price_features 串 pg_adapter OHLCV path with close-only fallback"
```

---

### Task 6: _format_price 顯示新指標

**Files:**
- Modify: `agents/ta_prompts.py`（修 `_format_price`）

讓 Market Analyst 看得到 ATR / 跳空 / 量價 / K 線型態。

- [ ] **Step 1: Sanity check 既有 prompt 不破**

```
python -c "
import sys; sys.path.insert(0, 'agents'); sys.path.insert(0, 'scripts')
from ta_features import collect
from ta_prompts import build_market_analyst_prompt
from pipeline import load_json
m = load_json('data/all_data_merged.json')
p = load_json('data/stock_prices.json')
t = {k: float(v) for k, v in load_json('data/twii_all.json').items()}
f = collect(symbol='2330', ticker='2330.TW', d='2026-05-04',
            merged=m, prices=p, twii=t, prediction_rows=[])
print(build_market_analyst_prompt(f)[-400:])
print('---')
print('ohlcv_available:', f.price.get('ohlcv_available'))
"
```

Expected: prompt 結尾正常；`ohlcv_available: False`（因為沒傳 pg_adapter）。

- [ ] **Step 2: Modify `_format_price`**

In `agents/ta_prompts.py`，修 `_format_price`（既有）末尾加 OHLCV 段：

```python
def _format_price(price: dict | None) -> str:
    if price is None:
        return "- 無價格資料(可能是新上市或下市)"
    twii_ret = price["twii_return_window"]
    excess_ret = price["excess_return_window"]
    if twii_ret is None or excess_ret is None:
        rel_line = (
            f"- 累積報酬 {price['return_window']*100:+.2f}% "
            "(TWII anchor 缺,無相對表現可算)"
        )
    else:
        rel_line = (
            f"- 累積報酬 {price['return_window']*100:+.2f}% "
            f"vs TWII {twii_ret*100:+.2f}% "
            f"→ 相對表現 {excess_ret*100:+.2f}%"
        )
    bias = price.get("bias_ma20")
    bias_line = (
        f"- 月均線乖離: {bias:+.2f}% (距 MA20 多遠;>0=站上 MA20)\n"
        if bias is not None else ""
    )
    dif = price.get("macd_dif")
    hist = price.get("macd_hist")
    signal = price.get("macd_signal")
    if dif is None or signal is None or hist is None:
        macd_line = "- MACD(12,26,9): 資料不足 35 日,無法計算\n"
    else:
        cross = "黃金交叉(多)" if hist > 0 else "死亡交叉(空)"
        macd_line = (
            f"- MACD(12,26,9): DIF={dif:+.3f}  Signal={signal:+.3f}  "
            f"Hist={hist:+.3f} → {cross}\n"
        )

    # OHLCV-only 新指標 (Task 6 新增)
    ohlcv_lines = []
    if price.get("ohlcv_available"):
        atr = price.get("atr14")
        atr_pct = price.get("atr_pct_of_close")
        if atr is not None and atr_pct is not None:
            volatility_label = (
                "高波動" if atr_pct > 3 else
                "中等波動" if atr_pct > 1.5 else "低波動"
            )
            ohlcv_lines.append(
                f"- ATR14: {atr:.2f} (佔現價 {atr_pct:.2f}%, {volatility_label})"
            )
        gap_n = price.get("gap_count_window")
        if gap_n is not None:
            ohlcv_lines.append(f"- 窗內跳空 {gap_n} 次 (gap > 0.5%)")
        v5 = price.get("vol_avg_5")
        v20 = price.get("vol_avg_20")
        vr = price.get("vol_ratio_5_20")
        if v5 and v20 and vr:
            trend = (
                "量增" if vr > 1.2 else
                "量縮" if vr < 0.8 else "量平"
            )
            ohlcv_lines.append(
                f"- 量能: 5 日平均 {v5/1e6:.1f}M vs 20 日平均 {v20/1e6:.1f}M, "
                f"量比 {vr:.2f} → {trend}"
            )
        pattern = price.get("candle_pattern")
        if pattern:
            ohlcv_lines.append(f"- 最近 K 線型態: {pattern}")
    ohlcv_block = "\n".join(ohlcv_lines) + ("\n" if ohlcv_lines else "")

    return (
        f"- 回看窗: {price['window_start']} ~ {price['window_end']}\n"
        f"- 收盤序列(後 5 筆): {price['closes'][-5:]}\n"
        f"- MA5={price['ma5']:.2f}  MA20={price['ma20']:.2f}\n"
        f"{bias_line}"
        f"{macd_line}"
        f"{ohlcv_block}"
        f"{rel_line}"
    )
```

- [ ] **Step 3: Sanity check 新 prompt 顯示新欄位**

```
python -c "
import sys; sys.path.insert(0, 'agents')
from ta_prompts import _format_price
fake_price = {
    'window_start': '2026-03-01', 'window_end': '2026-05-04',
    'closes': [600, 605, 610, 615, 620],
    'ma5': 615.0, 'ma20': 600.0,
    'return_window': 0.05, 'twii_return_window': 0.02, 'excess_return_window': 0.03,
    'bias_ma20': 3.33, 'macd_dif': 5.0, 'macd_signal': 3.0, 'macd_hist': 2.0,
    'ohlcv_available': True,
    'atr14': 12.5, 'atr_pct_of_close': 2.02,
    'gap_count_window': 4,
    'vol_avg_5': 5_000_000, 'vol_avg_20': 3_000_000, 'vol_ratio_5_20': 1.67,
    'candle_pattern': '錘頭',
}
print(_format_price(fake_price))
"
```

Expected: 看到 ATR14、跳空 4 次、量比 1.67 → 量增、K 線型態錘頭 各行都印出。

- [ ] **Step 4: 跑既有測試確認沒 break**

```
python -m unittest tests.test_ta_features tests.test_ta_runner -v 2>&1 | tail -5
```

Expected: 全 PASS（41 features + 12 runner = 53）。

- [ ] **Step 5: Commit**

```
git add agents/ta_prompts.py
git commit -m "feat(ta-lite): _format_price 顯示 ATR/跳空/量價/K線型態 給 Market Analyst"
```

---

### Task 7: 程度 2 — market.chip_ocr schema

**Files:**
- Create: `C:\Users\yen\Desktop\台股開發2\db\05_chip_ocr_schema.sql`
- 然後在 法人日資料 端 sanity check schema applied

加新表跨專案。注意這個 task 跨 git repo。

- [ ] **Step 1: 寫 schema**

Create `C:\Users\yen\Desktop\台股開發2\db\05_chip_ocr_schema.sql`:

```sql
-- scantrader OCR 出來的全市場警戒結構 (法人日資料 專案派生)
-- PK = date 因為這是「全市場當日警戒」一個 row 涵蓋多檔股票名單
-- bull/bear/top5 是逗號分隔股名/股號

CREATE TABLE IF NOT EXISTS market.chip_ocr (
    date                        DATE PRIMARY KEY,
    rate                        INTEGER,
    bull                        TEXT,
    bear                        TEXT,
    top5_margin_reduce_inst_buy TEXT,
    source                      TEXT DEFAULT 'scantrader',
    updated_at                  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chip_ocr_rate ON market.chip_ocr(rate);
```

- [ ] **Step 2: Apply schema 進 running PG**

```
docker exec -i twstock-postgres psql -U twstock -d twstock < "C:/Users/yen/Desktop/台股開發2/db/05_chip_ocr_schema.sql"
```

Expected: `CREATE TABLE\nCREATE INDEX`

- [ ] **Step 3: 驗證表存在**

```
docker exec -it twstock-postgres psql -U twstock -d twstock -c "\d market.chip_ocr"
```

Expected: 看到表結構與 idx_chip_ocr_rate 索引。

- [ ] **Step 4: Commit 在 台股開發2 那邊**

```
cd C:\Users\yen\Desktop\台股開發2
git add db/05_chip_ocr_schema.sql
git commit -m "schema: add market.chip_ocr table for 法人日資料 OCR data"
```

- [ ] **Step 5: 法人日資料 端不需 commit（只是 PG 操作）**

但建議在 README 加一行記錄：

```
echo "PG market.chip_ocr schema applied: $(date)" >> reports/pg_schema_log.txt
git add reports/pg_schema_log.txt
git commit -m "docs: 記錄 PG schema 05 applied"
```

---

### Task 8: 程度 2 — export_chip_ocr_to_pg.py

**Files:**
- Create: `scripts/export_chip_ocr_to_pg.py`
- Test: `tests/test_export_chip_ocr.py`

把 `data/all_data_merged.json` 全量 UPSERT 進 `market.chip_ocr`。支援 `--since YYYY-MM-DD` 增量。

- [ ] **Step 1: Write the failing test**

Create `tests/test_export_chip_ocr.py`:

```python
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
        # 01-05 全空 chip
        self.assertEqual(rows[2], ("2024-01-05", 168, "", "", ""))

    def test_since_filter(self):
        rows = build_upsert_rows(fx_merged(), since="2024-01-03")
        self.assertEqual(len(rows), 2)   # 01-02 被過濾
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

        # 應該 executemany 一次 UPSERT
        self.assertTrue(mock_cur.executemany.called)
        # 應該 commit
        self.assertTrue(mock_conn.commit.called)
        self.assertEqual(stats["upserted"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_export_chip_ocr -v
```

Expected: ImportError.

- [ ] **Step 3: Implement export script**

Create `scripts/export_chip_ocr_to_pg.py`:

```python
"""
把 data/all_data_merged.json 全量(或增量) UPSERT 進 PG market.chip_ocr。

用法:
    python scripts/export_chip_ocr_to_pg.py            # 全量
    python scripts/export_chip_ocr_to_pg.py --since 2026-05-01

權威源:all_data_merged.json (法人日資料 OCR 結果)。
PG 端 market.chip_ocr 是單向派生 copy。改錯永遠改 merged.json。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

sys.path.insert(0, str(BASE))

DEFAULT_DSN = "postgresql://twstock:twstock_dev_pw@localhost:5433/twstock"


def build_upsert_rows(
    merged: list[dict], since: str | None = None,
) -> list[tuple]:
    """把 merged record list 轉成 UPSERT 用的 tuple list。"""
    rows = []
    for r in merged:
        d = r.get("date", "")
        if since and d < since:
            continue
        rows.append((
            d,
            r.get("rate", 0),
            r.get("bull", "") or "",
            r.get("bear", "") or "",
            r.get("top5_margin_reduce_inst_buy", "") or "",
        ))
    return rows


UPSERT_SQL = """
INSERT INTO market.chip_ocr (date, rate, bull, bear, top5_margin_reduce_inst_buy, source, updated_at)
VALUES (%s, %s, %s, %s, %s, 'scantrader', NOW())
ON CONFLICT (date) DO UPDATE SET
    rate = EXCLUDED.rate,
    bull = EXCLUDED.bull,
    bear = EXCLUDED.bear,
    top5_margin_reduce_inst_buy = EXCLUDED.top5_margin_reduce_inst_buy,
    updated_at = NOW()
"""


def run_export(
    *, merged: list[dict], dsn: str, since: str | None = None,
) -> dict:
    """執行 UPSERT。回 stats dict。"""
    rows = build_upsert_rows(merged, since=since)
    if not rows:
        return {"upserted": 0}

    conn = psycopg.connect(dsn, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
        conn.commit()
    finally:
        conn.close()

    return {"upserted": len(rows)}


def main() -> int:
    from pipeline import load_json  # type: ignore[import-not-found]

    ap = argparse.ArgumentParser(description="Export merged.json chip OCR to PG")
    ap.add_argument("--since", help="只 export date >= since 的 record (YYYY-MM-DD)")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN", DEFAULT_DSN),
                     help="PG connection string")
    args = ap.parse_args()

    merged = load_json(DATA / "all_data_merged.json")
    print(f"讀進 {len(merged)} 筆 merged record")
    print(f"PG DSN: {args.dsn}")
    if args.since:
        print(f"增量模式 since={args.since}")

    try:
        stats = run_export(merged=merged, dsn=args.dsn, since=args.since)
        print(f"UPSERT 完成: {stats['upserted']} 筆")
        return 0
    except psycopg.OperationalError as e:
        print(f"[ERR] PG 連不上: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_export_chip_ocr -v
```

Expected: 4/4 PASS。

- [ ] **Step 5: 跑一次 real export 驗證**

(假設 Task 1 + 7 已完成、PG running、schema applied)

```
python scripts/export_chip_ocr_to_pg.py
```

Expected: `UPSERT 完成: 1183 筆`

驗證:
```
docker exec -it twstock-postgres psql -U twstock -d twstock -c "SELECT COUNT(*) FROM market.chip_ocr;"
docker exec -it twstock-postgres psql -U twstock -d twstock -c "SELECT * FROM market.chip_ocr WHERE date = '2026-05-04';"
```

Expected: 總 count = 1183，2026-05-04 那筆 rate=181 + bull/bear/top5 對得上 merged.json。

- [ ] **Step 6: Commit**

```
git add scripts/export_chip_ocr_to_pg.py tests/test_export_chip_ocr.py
git commit -m "feat(pg-integration): 程度 2 — export_chip_ocr_to_pg.py 單向 UPSERT"
```

---

### Task 9: 程度 2 (optional) — 整合進 daily-fetch

**Files:**
- Modify: `.claude/commands/daily-fetch.md`

加一行：daily-fetch 完成 append + commit 之後，呼叫 export script 同步當日新增 record 進 PG。

- [ ] **Step 1: 讀現有 daily-fetch.md 找對的位置**

```
cat .claude/commands/daily-fetch.md | head -100
```

找到 commit & push 之前的最後一個步驟。

- [ ] **Step 2: Modify daily-fetch.md**

在 commit step 之前加：

```markdown
## Step X: (Optional) 同步 OCR 結果進 PG

對剛 append 的新 record(s) 同步進 `market.chip_ocr`:

```
python scripts/export_chip_ocr_to_pg.py --since {first_new_date}
```

如果 PG 不可達(容器沒跑、台股開發2 那邊 Docker 沒啟):
- print 警告但**不要擋 daily-fetch 主流程**
- exit code 1 視為非 fatal,繼續 commit
```

- [ ] **Step 3: 手動測試 daily-fetch 流程**

跑 dry-run daily-fetch 看新加的 step 不會 crash（假設沒新資料 export 應該為 0）：

```
python scripts/export_chip_ocr_to_pg.py --since 2030-01-01
```

Expected: `UPSERT 完成: 0 筆` （since 太大沒符合 record）

- [ ] **Step 4: Commit**

```
git add .claude/commands/daily-fetch.md
git commit -m "docs(daily-fetch): 加 Step — export chip OCR to PG (optional)"
```

---

### Task 10: 重跑 PoC 評估 OHLCV-強化 Market Analyst

**Files:** 無 code 變更，跑 PoC 觀察輸出

- [ ] **Step 1: 修 ta_deepdive 接 pg_adapter**

In `agents/ta_deepdive.py`，加 import + wire:

Find existing imports（在 Tasks 1-10 已加的 retriever 那塊附近）:
```python
from ta_lesson_store import LessonStore   # type: ignore[import-not-found]
from ta_retriever import make_retriever   # type: ignore[import-not-found]
```

Add after:
```python
from pg_adapter import PGAdapter, ConnectionError as PGConnError   # type: ignore[import-not-found]
```

In `main()` 找 `collect(...)` 呼叫處（per-symbol loop 內），改成：
```python
        # PG adapter (try; if fail, pass None)
        try:
            pg = PGAdapter()  # uses PG_DSN env or default
            # quick connectivity check
            pg.get_ohlcv("2330.TW", "2024-01-01", "2024-01-02")
        except PGConnError:
            print(f"    [WARN] PG 不可達, fallback close-only")
            pg = None

        features = collect(
            symbol=sym, ticker=ticker, d=d,
            merged=merged, prices=prices, twii=twii,
            prediction_rows=prediction_rows,
            lessons=lessons,
            pg_adapter=pg,
        )
```

- [ ] **Step 2: 跑 1-2 個 symbol 觀察新指標**

```
python agents/ta_deepdive.py 2026-05-04 --symbols 2330,2303 --retriever none
```

Expected:
- 不 crash
- prompt 內 Market Analyst section 應該看到 `ohlcv_available=True` 路徑（前提是 PG 有 2330/2303 資料）
- 輸出 report 內 Market Analyst 開始引用 ATR / 跳空 / 量價 / K 線型態
- 跑 2 檔約 4 分鐘

- [ ] **Step 3: 對比 PG 啟 vs 沒啟兩種輸出**

A. 先停掉 PG（`docker stop twstock-postgres`），跑：
```
python agents/ta_deepdive.py 2026-05-04 --symbols 2330 --retriever none
```
觀察報告 — 應 fallback 到 close-only，沒有 ATR 等指標。

B. 啟 PG（`docker start twstock-postgres`），跑同樣指令。觀察報告 — 應該多出 ATR/跳空/量價/K 線型態 section。

- [ ] **Step 4: 寫 PoC 評估報告**

新增 `reports/pg_integration_poc_2026-05-16.md`，~300 字：
- PG seed 完成的 universe size 與時間
- 抽 2-3 檔報告對比「OHLCV 啟 vs 沒啟」品質差異
- 抓 Market Analyst 是否真的引用 ATR / 跳空 / K 線型態
- 估計這次升級對 Market 論述品質的提升
- 後續是否要做程度 2（如果 Task 7-9 還沒做）

- [ ] **Step 5: Commit 整體 PoC**

```
git add agents/ta_deepdive.py
git add reports/pg_integration_poc_2026-05-16.md
git add data/ta_reports/2026-05-04/  # 新跑出來的報告
git commit -m "report: PG 整合 PoC — OHLCV 強化 Market Analyst (2026-05-16)"
```

---

## Self-Review (post-write)

### Spec coverage
- [x] §1 共用 Postgres 啟動 → Task 1
- [x] §1 pg_adapter 8 個 read API → Task 2 + Task 3
- [x] §2 price_features OHLCV 整合 → Task 5
- [x] §2 OHLCV 指標 helpers → Task 4
- [x] §3 _format_price 顯示新指標 → Task 6
- [x] §4 程度 2 chip_ocr schema → Task 7
- [x] §4 程度 2 export script → Task 8
- [x] §4 程度 2 daily-fetch 整合 → Task 9
- [x] §5 PoC 驗證 → Task 10
- [x] §6 PG 不可達 fallback → Task 2 (PGAdapter raise) + Task 5 (price_features fallback)
- [x] §7 .env / .gitignore → Task 2

### Placeholder scan
- 無 TBD / TODO / implement later
- 每 Step 都有完整 code block 或 exact command

### Type / 名稱一致性
- `PGAdapter` → Task 2/3/5/10 名稱一致
- `ConnectionError` → Task 2 定義,Task 5/10 import 一致
- `get_ohlcv(ticker, start, end)` → Task 2/3/5 簽名一致
- `_ticker_to_stock_id` → Task 2 定義,Task 3 透過 `_query_range` reuse
- `price_features(..., pg_adapter=None)` → Task 5 加參數,Task 10 傳值
- `collect(..., pg_adapter=None)` → Task 5 加參數,Task 10 傳值

### Scope check
- 兩階段(程度 1 = Task 1-6,程度 2 = Task 7-9)清楚分界
- 各階段可獨立 ship,不互相阻塞
- 程度 2 可選擇性 skip(若你只想要 OHLCV 不想雙向同步)
- Task 10 PoC 評估在所有 code 完成後跑
