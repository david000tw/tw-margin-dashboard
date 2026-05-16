# 台股開發2 Postgres 整合 — OHLCV + 籌碼 + 估值 read API 設計

**日期**：2026-05-16
**狀態**：design
**作者**：david + Claude
**前置**：commit `9145a05`（lesson loop framework）
**Spec 上一篇**：`docs/superpowers/specs/2026-05-16-lesson-loop-design.md`

## 背景

法人日資料 現有 `data/stock_prices.json` 只有**收盤價**（637 檔 × 1290 個交易日，4.7 MB）。Market Analyst 無法算 ATR、跳空、K 線型態、量價背離等需要 OHLV 的技術指標。

`C:\Users\yen\Desktop\台股開發2` 有完整 Postgres schema：
- `market.prices`：OHLCV
- `market.institutional`：三大法人買賣超
- `market.margin`：融資融券
- `market.lending`：借券
- `market.holders`：千張大戶
- `market.valuation`：PE/PB/殖利率
- `market.monthly_revenue`：月營收
- `market.financials`：季財報

但目前 Docker container 沒在跑，DB 是空的（需要 seed）。

本 spec 設計：法人日資料 端加 **read-only Postgres adapter**，讓 Market Analyst 能用 OHLCV 強化指標，未來也能從 8 張表撈完整籌碼/基本面資料。

## 目標

- 啟動 台股開發2 的 Docker Postgres + seed 全市場 ~1971 檔 OHLCV + chip + valuation
- 法人日資料 端加 `agents/pg_adapter.py` 提供 8 個 read API
- `price_features` 改造：從 PG 拉 OHLCV 並算 ATR / 跳空 / 量價 / K 線型態
- PG 不可達時 graceful fallback 到現有 `stock_prices.json` close-only 模式
- 不寫雙寫同步：你手動跑 `台股開發2/scripts/daily_update.py` 維護資料

## 非目標

- 不動 台股開發2 schema 或 code
- 不取代現有 `chip_features` (bull/bear/top5 OCR 結果)
- 不做 Parquet 緩存（直連 PG ~1 ms/query 夠快）
- 不寫 admin UI / web 介面看 PG 狀態
- 不引入 connection pool library
- 不做 chip data 從 PG 反向填回 OCR（OCR data 是這專案獨特資產）

## 架構

```
┌─────────────────────────────────────────────────────────┐
│  台股開發2/  (你維護的另一個專案)                       │
│  - docker-compose up -d → Postgres 16 port 5433         │
│  - scripts/seed_full_universe.py (一次性 seed 1971 檔)  │
│  - scripts/daily_update.py        (每日手動更新)        │
│                                                         │
│  Postgres market.{8 張表}                                │
└────────────────────┬────────────────────────────────────┘
                     │ read-only, psycopg
                     ▼
┌─────────────────────────────────────────────────────────┐
│  法人日資料/  (本專案)                                  │
│                                                         │
│  agents/pg_adapter.py    PGAdapter class, 8 個 get_* API│
│  agents/ta_features.py   price_features() 改:          │
│                           1) 嘗試從 PG 拉 OHLCV         │
│                           2) 算 ATR/跳空/K線/量價       │
│                           3) PG 不可達 → fallback close │
│  agents/ta_prompts.py    _format_price 顯示新指標       │
└─────────────────────────────────────────────────────────┘
```

### Stock_id 格式對應

```
台股開發2:  stock_id = "2330" (純股號,無後綴)
法人日資料: ticker  = "2330.TW" / "1101.TW" / "5483.TWO" (yfinance ticker)

PGAdapter 內處理:
  if ticker.endswith(".TWO"): stock_id = ticker[:-4]    # 上櫃
  elif ticker.endswith(".TW"):  stock_id = ticker[:-3]  # 上市
  else:                          stock_id = ticker       # raw 號

注意:不用 .rstrip(".TW") 之類做切尾(rstrip 是 char-set 操作會誤砍)
```

## 新檔結構

```
agents/
  pg_adapter.py        新 — PGAdapter class + 8 個 read API + 連線管理
  ta_features.py       改 — price_features 嘗試從 PG 拉 OHLCV;fallback close-only
  ta_prompts.py        改 — _format_price 加 ATR/跳空/量價/K線 顯示

scripts/
  start_pg.bat         新 — Windows 一鍵啟 Docker + healthcheck
  start_pg.sh          新 — Linux/Mac 同等版

tests/
  test_pg_adapter.py   新 — unit (mock psycopg) + integration (PG 不可達 skip)
  test_ta_features.py  改 — 加 ATR/跳空/量價 測試 + PG fallback 測試

requirements 或 pyproject.toml:
  + psycopg[binary]>=3.1  (PG client, 純 Python wheel 含 binary)

.env (gitignored):
  PG_DSN=postgresql://twstock:twstock_dev_pw@localhost:5433/twstock
```

## PGAdapter API 設計

```python
# agents/pg_adapter.py

class PGAdapter:
    """法人日資料 端 read-only PG client。

    DSN 從環境變數 PG_DSN 取,或建構時傳入。
    Lazy connect:首次呼叫 get_* 才建連線。
    PG 不可達 → 拋 ConnectionError, caller 自行 fallback。
    """

    def __init__(self, dsn: str | None = None): ...

    def _conn(self): ...  # lazy connect, cached

    def _stock_id(self, ticker: str) -> str:
        """2330.TW → 2330  /  5483.TWO → 5483"""

    # 8 個 read API,全部回 pandas DataFrame
    def get_ohlcv(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """SELECT date, open, high, low, close, volume FROM market.prices
           WHERE stock_id = %s AND date BETWEEN %s AND %s ORDER BY date"""

    def get_institutional(self, ticker, start, end) -> pd.DataFrame:
        """法人三大買賣超明細"""

    def get_margin(self, ticker, start, end) -> pd.DataFrame:
        """融資融券餘額"""

    def get_lending(self, ticker, start, end) -> pd.DataFrame:
        """借券餘額"""

    def get_holders(self, ticker, start, end) -> pd.DataFrame:
        """千張大戶比例"""

    def get_valuation(self, ticker, start, end) -> pd.DataFrame:
        """PE/PB/殖利率"""

    def get_monthly_revenue(self, ticker) -> pd.DataFrame:
        """月營收(沒 date range,全歷史)"""

    def get_financials(self, ticker) -> pd.DataFrame:
        """季財報(全歷史)"""

    def close(self): ...
```

### 錯誤行為

```python
PG 不可達 (容器沒跑 / 網路斷)   →  raise ConnectionError("PG 連不上: ...")
ticker 在 PG 找不到 (新股 / 不在 universe)  →  回空 DataFrame
date range 超出 PG 有的範圍       →  回部分資料 (不 raise)
DSN 設錯 / 認證失敗               →  raise ConnectionError
```

## price_features 改造

新增 7-8 個欄位（在現有的 closes/MA/MACD 之上）：

```python
{
  # 既有 (Task 7 階段):
  "closes": [...], "ma5", "ma20", "ma_window",
  "return_window", "twii_return_window", "excess_return_window",
  "bias_ma20", "macd_dif", "macd_signal", "macd_hist",

  # 本次新增 (要 OHLCV 才能算):
  "ohlcv_available": True,            # False 代表退回 close-only 模式
  "atr14": 35.2,                      # 平均真實波幅 (14 日)
  "atr_pct_of_close": 1.56,           # ATR / close, 波動率代理
  "gap_count_window": 3,              # 窗內跳空 (open vs 昨日 close 偏離 > 0.5%)
  "vol_avg_5": 12345678,              # 近 5 日平均量
  "vol_avg_20": 8901234,              # 近 20 日平均量
  "vol_ratio_5_20": 1.39,             # 短中期量比, >1 量增 <1 量縮
  "candle_pattern": "錘頭",           # 最後一根 K 線型態 (錘頭/吞噬/十字/None)
}
```

### 計算邏輯（基本原理）

- **ATR14**：True Range = max(high-low, |high-prev_close|, |low-prev_close|);取 14 日平均
- **跳空**：今日 open 跟昨日 close 偏離 > 0.5% (參數可調)
- **量比**：vol_avg_5 / vol_avg_20
- **K 線型態**：實體 / 上下影線比例 + 收紅/黑判斷
  - 錘頭：實體小、下影線長、收紅
  - 吞噬：今日實體覆蓋昨日實體
  - 十字：實體 < 全長 10%

### Fallback 邏輯

```python
def price_features(ticker, d, ...):
    # 嘗試 PG 拉 OHLCV
    try:
        ohlcv = pg.get_ohlcv(ticker, start, end)
        if not ohlcv.empty:
            return _from_ohlcv(ohlcv, ...)
    except ConnectionError:
        pass

    # Fallback: 現有 close-only 模式 (stock_prices.json)
    return _from_close_only(ticker, d, ...)
```

確保 PG 沒啟動時，現有 ta_deepdive / backfill 仍能跑（degraded mode）。

## _format_price 顯示

```
- 回看窗: 2026-03-01 ~ 2026-05-04 (60 日)
- 收盤序列 (後 5 筆): [2215.0, 2180.0, 2135.0, 2275.0, 2250.0]
- MA5=2211.00  MA20=2085.75
- 月均線乖離: +1.13%
- MACD(12,26,9): DIF=+5.42  Signal=+3.21  Hist=+2.21 → 黃金交叉(多)
- 累積報酬 +20.97% vs TWII +10.55% → 相對表現 +10.42%

# 新增:
- ATR14: 35.2 (佔現價 1.56%, 中等波動)
- 窗內跳空 3 次 (gap > 0.5%)
- 量能: 5 日平均 12.3M vs 20 日平均 8.9M, 量比 1.39 → 量增
- 最近 K 線型態: 錘頭 (下影線長,可能止跌)
```

## 連線管理

- **DSN** 從環境變數 `PG_DSN` 取
- 預設值（dev）：`postgresql://twstock:twstock_dev_pw@localhost:5433/twstock`
- **`.env` 檔加進 `.gitignore`** —— 不上 git
- PGAdapter 用 lazy connect：第一次 get_* 才開連線、之後 reuse
- 程式結束時自動 close
- 不用 connection pool（單機本地用，open/close 開銷 < 10 ms）

## seed 流程

```bash
# Step 1: 啟 Postgres
cd "C:\Users\yen\Desktop\台股開發2"
docker-compose up -d
# 等 ~10 秒 healthcheck pass

# Step 2: seed 全市場 (~2-3 hr)
$env:REPO_BACKEND="postgres"  # PowerShell
uv run python scripts/seed_full_universe.py
```

Seed 完成後 8 張表都有 ~1971 檔 × 5+ 年資料。**你以後手動跑 `daily_update.py` 維護**（這邊不寫自動排程）。

## 測試策略

### `test_pg_adapter.py`（~7 tests）

**Unit tests (mock psycopg)**：
- `test_dsn_from_env`：從 PG_DSN 環境變數讀取
- `test_stock_id_conversion`：2330.TW → 2330, 5483.TWO → 5483
- `test_get_ohlcv_returns_dataframe`：mock cursor 回 fake rows, 驗 schema
- `test_get_ohlcv_empty_when_no_match`：mock 0 rows → 回空 DataFrame
- `test_connection_failure_raises`：mock psycopg raise → adapter raise ConnectionError

**Integration test**（標記 `@unittest.skipUnless(pg_alive())`)：
- `test_real_ohlcv_query`：實連 PG, 查 2330 最近 5 日, 驗回傳 5 rows

### `test_ta_features.py`（補 ~4 tests）

- `test_price_features_with_ohlcv`：mock pg_adapter 回 fake OHLCV, 驗 ATR/跳空/量價計算
- `test_price_features_fallback_to_close_only`：mock pg_adapter raise ConnectionError, 驗退回 close-only 路徑
- `test_atr_calculation`：純函式 unit test 給定 5 日 OHLC 驗 ATR=N
- `test_candle_pattern_detection`：給不同 K 線形狀驗識別「錘頭/吞噬/十字/None」

## 錯誤處理

| 失敗點 | 行為 |
|---|---|
| PG 容器沒跑 / 網路斷 | adapter raise ConnectionError → ta_features fallback close-only + print warning |
| ticker 在 PG 找不到 | adapter 回空 DataFrame → ta_features 視為「無資料」回 None |
| seed 中途 crash | `seed_full_universe.py` 是 idempotent UPSERT，重跑會從 skip 已有的繼續 |
| volume 為 0 | 量價指標 (vol_avg/ratio) 該日跳過, ATR 不受影響 |
| `.env` 漏設 PG_DSN | 用 default DSN; 若也連不到 → 同 PG 不可達 |
| `psycopg` 未安裝 | `pip install psycopg[binary]` 加進 setup;若漏裝 ta_features 進 fallback path |

## 開放議題

無已知。

## 不做（YAGNI 明列）

- ❌ **不寫雙寫同步邏輯**（法人日資料 → 台股開發2 PG 的 chip 資料同步）
- ❌ **不取代** chip_features 的 OCR 結果（OCR data 是這專案獨特資產）
- ❌ **不裝 connection pool**（psycopg 本地直連夠快）
- ❌ **不做 PG → Parquet 緩存**
- ❌ **不寫 web admin UI** 看 PG 狀態
- ❌ **不自動排程** daily_update（你手動跑）
- ❌ **不整合** valuation / monthly_revenue / financials 進 ta_features（這些是給未來 Fundamentals Analyst 用，目前只提供 read API）

## 預估工作量

| 階段 | 估時 |
|---|---|
| Phase 1: 啟 Postgres + seed (人工執行 + 等抓資料) | **2-3 hr** |
| Phase 2: `pg_adapter.py` + 7 個測試 | **2-3 hr** |
| Phase 3: `price_features` 改造 + 4 個測試 | **2 hr** |
| Phase 4: `_format_price` 更新 + 跑 PoC 驗證 | **1-2 hr** |

**總計 ~半天到一天 implementation + ~2-3 hr 等資料**。
