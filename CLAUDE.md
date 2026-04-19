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

### 3. `rebuild` 是字串替換，不是 regenerate
`pipeline.py rebuild` 以正則 `<div class="sub">...</div>` 定位後替換 header fallback 文字。若手動編輯 `dashboard_all.html` 破壞此標記，rebuild 會 raise。

### 4. year 檔 與 merged 檔 雙軌
`append` 同時寫兩邊；若手動改其中一邊，下次 `check` 會報 `[sync]` 錯誤。`check` 是 smoke test，append 後 / commit 前必跑。

### 5. 股名規則
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
  ├─► data/stock_data_YYYY.json   (年份分檔,sorted)
  └─► data/all_data_merged.json   (合併檔,dashboard fetch 目標)
  │
  └─► pipeline.py check           (smoke test,必跑)
         │
         └─► pipeline.py rebuild   (更新 dashboard header fallback)

dashboard_all.html:
  瀏覽器載入 → fetch ./data/all_data_merged.json
             → fetch ./data/twii_all.json
             → 前端 ALERT_THRESHOLD=170 判警戒
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
