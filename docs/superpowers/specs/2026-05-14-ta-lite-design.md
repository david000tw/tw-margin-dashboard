# TradingAgents-lite 設計（PoC 階段）

**日期**：2026-05-14
**狀態**：design
**作者**：david + Claude
**前置**：commit `f1731c0`（AI 預測閉環）

## 背景

`agents/predict.py` 已建立 walk-forward LLM 預測閉環，每日輸出 long 5 / short 5 推薦並驗證 T+5/10/20 excess return。這個 spec 在不取代既有預測流程的前提下，**互補**新增一層「對 predict.py 已選出的股票做多 agent 深度分析」的能力，靈感來自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的 multi-agent 辯論架構。

不直接安裝 TradingAgents package（不解 LangChain tool-calling 與 LLM provider 問題），而是借用其 prompt 模板與 agent 分工，用既有的 `call_llm()`（走 Claude Code 訂閱）自己編排。

## 目標

- 對 `ai_predictions.jsonl` 中某一日的 long-3 / short-3 picks（依 conviction 排序），各跑一份多 agent 深度報告
- 報告以 markdown 落地到 `data/ta_reports/<date>/<symbol>.md`，外加機讀 `summary.json`
- 嚴格 walk-forward：所有 agent prompt 內容只能來自 `< d` 的資料
- 完全手動觸發，不接 daily-fetch、不寫 cron

## 非目標

- 不取代 `agents/predict.py`
- 不做 News / Social Media / Fundamentals Analyst（沒對應資料源）
- 不做 Aggressive/Conservative 多風控辯論（PoC 只一個 Risk Manager）
- 不做 Portfolio Manager（PoC 只關心單檔深度報告）
- 不整合進 dashboard
- 不做 memory / reflection
- 不接 daily-fetch 自動觸發

## 架構

### Agent 分工（6 個 LLM call/股）

| Stage | Agent | 輸入 | 輸出 |
|---|---|---|---|
| 1 | **Market Analyst** | 60 日 K 線、RSI、MA、近期相對 TWII | 技術面短評 ~150 字 |
| 1 | **Chip Analyst** | 該股近 60 日在 bull/bear 榜出現次數、最近一次在 top5_margin_reduce_inst_buy、出現當日的 rate、過去命中歷史 | 籌碼面短評 ~150 字 |
| 2 | **Bull Researcher** | Stage 1 兩份報告 + 原始 feature 摘要 | 看多論述 ~200 字 |
| 2 | **Bear Researcher** | Stage 1 兩份報告 + 原始 feature 摘要 | 看空論述 ~200 字 |
| 3 | **Trader** | Stage 1 + Stage 2 全部 | 行動建議（buy/sell/hold） + conviction 0-1 + 理由 ~200 字 |
| 4 | **Risk Manager** | 前面全部 | 風險評估 + 建議倉位上限 % + 主要 downside ~150 字 |

Stage 1 兩個 analyst 可平行（但 PoC 階段先 sequential，簡化 debug）；Stage 2 兩位 researcher 可平行。

### 檔案結構

```
agents/
  ta_deepdive.py          主入口 CLI
  ta_features.py          預取 price/chip/past_perf feature
  ta_prompts.py           6 個 agent prompt template（繁中改寫 TA 原版）
  ta_runner.py            stage 1→4 編排 + 寫出報告

data/
  ta_reports/
    2026-05-14/
      2330.md             單檔 markdown 報告
      2317.md
      summary.json        所有股票的機讀摘要

tests/
  test_ta_features.py     feature 切片 + walk-forward 不變量
  test_ta_runner.py       stub LLM 測編排邏輯
```

### CLI

```bash
# 預設模式：讀 ai_predictions.jsonl 取該日 long-3 + short-3
python agents/ta_deepdive.py 2026-05-14

# 手動指定 symbols
python agents/ta_deepdive.py 2026-05-14 --symbols 2330,2317,2454

# 模型切換（預設 sonnet，可用 haiku 加速 PoC）
python agents/ta_deepdive.py 2026-05-14 --model haiku
```

## 資料流

```
date d + symbol
  │
  ▼
ta_features.collect(d, symbol):
  ├─ price_features:  從 stock_prices.json 切到 < d 的 60 日 OHLCV
  │                    算 MA5/MA20/MA60、RSI14、近 20 日 vs TWII 相對表現
  ├─ chip_features:   從 all_data_merged.json (record.date < d) 統計
  │                    - bull 榜出現次數（近 60 天）
  │                    - bear 榜出現次數（近 60 天）
  │                    - 最近一次出現 top5_margin_reduce_inst_buy 的日期 + 當日 rate
  │                    - 出現於 bull 當日的平均 rate（衡量警戒環境參與度）
  ├─ market_context:  近 30 天 merged（複用 predict.py.slice_merged_strict）
  │                    + TWII 趨勢摘要
  └─ past_perf:       該 symbol 在 ai_predictions.jsonl 過去出現過幾次、
                       命中率（用 verify_predictions 已寫好的 outcome）
  │
  ▼
ta_runner.run_pipeline(features):
  Stage 1: market_report, chip_report  ← Market Analyst, Chip Analyst
  Stage 2: bull_view, bear_view        ← Bull Researcher, Bear Researcher
  Stage 3: trader_decision             ← Trader
  Stage 4: risk_assessment             ← Risk Manager
  │
  ▼
ta_runner.write_report(date, symbol, all_outputs):
  - 寫 data/ta_reports/<date>/<symbol>.md（包含 6 個 agent 的 section）
  - append 一筆 entry 到 data/ta_reports/<date>/summary.json
```

### Walk-forward 不變量

- `ta_features.collect()` **嚴格只讀 `< d` 的資料**，與 `predict.py` 一致
- agent prompt **不放 d 當天或之後任何資訊**，連 today's price 都不放
- Risk Manager 的「建議倉位」是給人看的 narrative，不會回灌成回測訊號

## 錯誤處理

| 失敗點 | 行為 |
|---|---|
| 單一 agent LLM call timeout / 非空回應失敗 | 該 stage 該 agent section 寫 `[LLM failed: <reason>]`，pipeline 繼續 |
| Agent 連續兩次 timeout（含一次重試） | 同上 |
| 該檔股票 6 個 call 全失敗 | 該檔 markdown 寫錯誤標頭，summary.json status=`failed`，繼續下一檔 |
| 預取階段 symbol 找不到 ticker 或無價格 | 跳過該檔，summary.json status=`skipped` |
| 全部 N 檔都失敗 | exit code 1，summary.json 寫 status=`all_failed` |
| 部份失敗 | exit code 0 |

## 測試策略

- **`test_ta_features.py`**：純資料切片，餵 fixture merged + prices，斷言：
  - `< d` 切片不包含 `>= d` 的 record
  - chip features 計算對於已知輸入有預期輸出
  - 缺價 / 缺資料的 symbol 不會 raise
  - 複用 `tests/test_predict.py` 的 walk-forward fixtures
- **`test_ta_runner.py`**：注入 stub `_llm_call`，驗證：
  - stage 順序正確（1→2→3→4）
  - 前面結果有傳遞給後面 agent（檢查 prompt 內容含上個 stage output）
  - 單一 agent 失敗時 markdown 內含 `[LLM failed]` 標記、pipeline 不中斷
  - summary.json 的 status 對於 failed / skipped / ok 各情境正確

## PoC 驗證計畫

跑 2-3 個歷史警戒日（rate ≥ 170），例如：
- 2026-05-04（最新警戒日，rate=181）
- 2026-04-30（rate=176）
- 2026-04-29（rate=176）

每日跑 long-3 + short-3 共 6 檔，看 markdown 輸出品質：
- 籌碼面有沒有抓到有用的 pattern（e.g.「這檔近 60 天 4 次出現 bull 榜且都在 rate≥170 環境」）
- Bull/Bear 辯論有沒有實質對話，還是只是 paraphrase
- Trader 的 conviction 與 predict.py 給的 conviction 是否一致 / 互相印證

PoC 通過標準（軟性）：
- 至少一半的 6 檔報告讀起來有 chip insight，不是空話
- Trader 給的 action 與 predict.py 推薦的方向一致率 ≥ 70%

PoC 通過後再評估：
- 是否要接 daily-fetch（subscription rate limit 還可不可接受）
- 是否整合 dashboard
- 是否加 News / Sentiment 角色（需要先建外部資料源）

## 開放議題

無已知開放議題。執行時若 `call_llm()` 在連續 36 次呼叫下踩到 subscription 速率限制，會在錯誤處理路徑被吸收（標 `[LLM failed]`），不會崩潰。實際發生再評估退避策略。
