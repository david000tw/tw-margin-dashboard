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

## AI 預測閉環（`agents/predict.py` + `verify_predictions.py`）

每日呼叫 `claude -p` 走 Claude Code 訂閱（**免 API key 免 token 費用**），對候選股 long 5 / short 5 給推薦，落地進 `data/ai_predictions.jsonl`。

- **嚴格 walk-forward**：對日期 d 的 prediction，prompt context 只能來自 `< d` 的 record + twii + verified predictions。違反一次整批回填白做。
- backfill 模式（`scripts/backfill_predictions.py`）對歷史 1182 天逐日跑，checkpoint resume
- T+5/10/20 outcome 在 `verify_predictions.py`，excess return vs TWII
- `data/ai_predictions_summary.json` 是派生產物給 dashboard 讀

## TradingAgents-lite 多 agent 深度報告

對某一日的選定股票跑 6 個 agent 深度分析（Market/Chip Analyst → Bull/Bear Researcher → Trader → Risk Manager）。

- 借用 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的 prompt 設計，**不裝 langchain/langgraph**，重用 `predict.py:call_llm` 走訂閱
- `agents/ta_features.collect()` 預取 chip / price / past_perf / market_context 走 walk-forward
- `agents/ta_prompts.py` 6 個 build_*_prompt template（繁中改寫）
- `agents/ta_runner.py` 編排 + 寫 markdown 報告到 `data/ta_reports/<date>/<symbol>.md`
- `agents/ta_deepdive.py` CLI：`python agents/ta_deepdive.py <date> --symbols 2330,2303,... --retriever claude --model sonnet`
- spec：`docs/superpowers/specs/2026-05-14-ta-lite-design.md`

## Lesson loop（自我反思閉環，C 級 RAG）

對 ta_reports 算 T+N excess outcome → LLM 反思 Trader 決策 → 寫 lesson → 下次 deepdive 撈語意相似的 lesson 塞 prompt。**嚴格 walk-forward**（lesson.date < d）。

- `agents/ta_lesson_store.py` — JSONL append-only + walk-forward 查詢
- `agents/ta_outcome.py` — verdict 分類（right_direction / right_hold / missed_long / avoided_loss / wrong_direction）
- `agents/ta_retriever.py` — Protocol + 3 impls：
  - **ClaudeRetriever**：claude -p LLM-as-retriever（零安裝、走訂閱）
  - **EmbeddingRetriever**：sentence-transformers cosine sim（需 `pip install sentence-transformers ~1.5GB`）
  - **CompareRetriever**：兩個都跑 log 差異到 `data/retriever_compare.jsonl`
- `agents/ta_reflect.py` — Trader-only reflection（outcome → LLM → lesson + tags）
- `agents/ta_backfill.py` — 批次回填 CLI checkpoint/resume
- 落地檔：`data/ta_lessons.jsonl`, `data/ta_outcomes/<date>/<symbol>.json`
- spec：`docs/superpowers/specs/2026-05-16-lesson-loop-design.md`
- **核心不變量**：LessonStore 層守 walk-forward 過濾，retriever 不負責 date filter（即使 retriever 寫錯也不洩漏）

## Postgres 整合（讀取 `台股開發2` 的 PG）

法人日資料 read-only 連到另一個 repo `C:\Users\yen\Desktop\台股開發2` 啟動的 Postgres（port 5433），撈完整 OHLCV + 籌碼 + 估值。

- `agents/pg_adapter.py` `PGAdapter` 提供 8 個 read API（ohlcv/institutional/margin/lending/holders/valuation/monthly_revenue/financials）
- DSN 從 `PG_DSN` 環境變數取，預設 `postgresql://twstock:twstock_dev_pw@localhost:5433/twstock`
- `.env` 已 gitignored；`.env.example` 是模板
- **權威源不動**：法人日資料 的 `all_data_merged.json` 仍是 OCR 結果權威源。`market.chip_ocr` 是派生 copy（`scripts/export_chip_ocr_to_pg.py` 單向 UPSERT）
- **PG 不可達 → graceful fallback**：`price_features` 自動退回 close-only `stock_prices.json`
- spec：`docs/superpowers/specs/2026-05-16-pg-integration-design.md`
- ⚠️ **Docker Desktop 4.40+ 有 Inference Manager bug**（Windows 上 Unix socket bind 失敗）。**強制用 4.39.0 或更早**；裝最新版會炸 backend。

## 自訂 Sub-agent (`.claude/agents/`)

3 個專案領域人格化 agent：

- **chip-data-analyst** — 籌碼面老員，看 chip flow pattern，不碰 prediction / dashboard
- **signal-skeptic** — 量化反骨派，挑 overfit 與 leakage，不接受 n<5
- **ta-lite-critic** — TA-lite 報告評論人，抓「水內容」、給 prompt patch 建議

呼叫方式：
- 自動觸發：問題 match description 時自動派
- 顯式：`@chip-data-analyst <task>` 或 `Task(subagent_type="signal-skeptic", ...)`
- 改 agent.md 後要 `/reload-plugins` 才生效

## Working conventions

- Append 後 dashboard 會自動看到新資料（fetch 載入），**不需要 rebuild 才能更新資料**；`rebuild` 只更新 header fallback 文字
- 回補舊日期時，`append` 會依日期排序插入正確位置（不是 append-only）
- 所有 JSON 檔 UTF-8 + `ensure_ascii=False` + `indent=2`；中文股名不轉義
- `check` 除了驗 schema 還會偵測 year/merged 不同步、rate_alert 殘留、manual_review 待處理筆數
- Python 3.8+（用到 `os.replace`、f-string、pathlib）
