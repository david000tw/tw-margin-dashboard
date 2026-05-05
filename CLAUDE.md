# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

使用方式、檔案清單、安裝排程等使用者面向的說明在 `README.md`，本檔只寫 Claude 在這個 codebase 需要特別知道的**不從 code 即可看出的規則與慣例**。

## Project Overview

台股每日法人 / 借券 / 融資資料收錄與視覺化。scantrader.com 原始資料透過 OCR 或手動匯入，落地為 JSON，`dashboard_all.html` 以 `fetch('./data/*.json')` 呈現。

## 非常重要的不變量（違反會靜默壞資料）

### 1. `rate_alert` 欄位不可寫入
警戒由 `rate >= 170` 在 dashboard 與 `pipeline.py` 即時推導。record 中含此欄位會被 `validate_record()` raise 擋下。

### 2. Dashboard 必須透過 HTTP server 開啟
`fetch('./data/...')` 在 `file://` 下會被 CORS 擋。使用者開 `啟動Dashboard.bat`，自動起 `python -m http.server 8899`。

### 3. merged 是 single source of truth；year 檔為派生備份
`all_data_merged.json` 是 dashboard 唯一資料來源。`stock_data_YYYY.json` 由 `append_record` 從 merged 派生（保留是因為 commit diff 較小、便於人工瀏覽）。
- 不要手動編輯 year 檔；下次 `append` 寫到該年時會被覆蓋。要改資料就改 merged，再跑 `python pipeline.py regen-years`。
- `check` 偵測到 year ↔ merged 不一致會 WARN（不是 ERROR），提示跑 `regen-years`。

### 4. 股名規則
- 逗號分隔、**不加空格、不加引號**
- 保留 `*`（如 `可寧衛*`）、`-KY`（如 `中美-KY`）
- 歷史資料（2021–2024）多為股號，近期（2026）改用股名。混用合法，兩者都要支援。

## Architecture（資料流向）

```
record (dict/json)
  │
  ▼
pipeline.py append  ──► validate_record()
  │                       │ 失敗 raise ValueError
  │                       ▼
  └─► data/all_data_merged.json   (single source of truth, dashboard 讀這份)
        │
        └─► 派生 ──► data/stock_data_YYYY.json  (備份,僅作 commit diff)

pipeline.py check          smoke test,append 後 / commit 前必跑
pipeline.py regen-years    從 merged 重建所有年份檔(手動編輯 merged 後跑)

dashboard_all.html:
  瀏覽器載入 → fetch ./data/all_data_merged.json
             → fetch ./data/twii_all.json
             → 前端 ALERT_THRESHOLD=170 判警戒
             → updateHeader() 載完即時改寫 header
```

## Record schema

```json
{
  "date": "2026-04-15",
  "bull": "台積電,聯發科,鴻海",
  "bear": "長榮,陽明",
  "rate": 172,
  "top5_margin_reduce_inst_buy": "台積電,富邦金,玉山金,中信金,國泰金"
}
```

`rate` 是 int 100–250；`bull/bear/top5_margin_reduce_inst_buy` 是字串。

## 分析層（`scripts/analyze_signals.py`）

訊號的歷史驗證、篩選、回測產出。**完整邏輯與限制在 `docs/SIGNAL_ANALYSIS.md`，這裡只記不從 code 看出的慣例**：

- `data/backtest_summary.json` 是**派生產物**（跟 year 檔同性質），dashboard 直接讀。**不要手改**；下次 `analyze_signals.py` 會覆蓋。
- `data/stock_prices.json` 與 `data/stock_fetch_log.json` 由 `scripts/fetch_prices.py` 產出（yfinance），同樣派生不可手改。
- Python 端的 `read_price` / `get_close_on_or_before` / `get_close_n_days_later` 必須與 `dashboard_all.html:586-...` 的 JS 邏輯**完全一致**（不一致 → Python 報告與 dashboard 數字對不起來）；`tests/test_analyze_signals.py` 有 fixture 守護。
- Bear 訊號用反向邏輯篩選（`train_avg ≤ -threshold`、用敗率而非勝率），grid search 也是 per-side 各跑一次，不要混在一起跑。
- Train/test 切點 `2024-12-31`：不要用全期跑篩選後再評估同一段，那是 overfit。
- 重大訊號發現要更新 `docs/SIGNAL_ANALYSIS.md` 的「實際結論」節（commit 進 git）；舊報告保留在 `reports/signal_validation_YYYY-MM-DD.md` 不覆蓋。

## 自動化擷取（`/daily-fetch`）

- 指令定義在 `.claude/commands/daily-fetch.md`
- 排程由 `DailyFetch.bat` 呼叫 `claude -p` headless 執行
- OCR 信心不足的日期會寫入 `data/manual_review.txt`，下次執行自動跳過（避免排程被同一個讀錯日卡住）
- 資料規範與 OCR 規則集中在 `scraper_guide.md`，`daily-fetch.md` 不重複這些細節

## Working conventions

- Append 後 dashboard 會自動看到新資料（fetch 載入），**不需要 rebuild 才能更新資料**；`rebuild` 只更新 header fallback 文字
- 回補舊日期時，`append` 會依日期排序插入正確位置（不是 append-only）
- 所有 JSON 檔 UTF-8 + `ensure_ascii=False` + `indent=2`；中文股名不轉義
- `check` 除了驗 schema 還會偵測 year/merged 不同步、rate_alert 殘留、manual_review 待處理筆數
- Python 3.8+（用到 `os.replace`、f-string、pathlib）
