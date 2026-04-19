# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

台股（Taiwan stock market）每日法人 / 借券 / 融資資料收錄與視覺化。原始資料由 scantrader.com 手動擷取（見 `scraper_guide.md`），透過 `pipeline.py` 落地成 JSON，最終 inline 進 `dashboard_all.html` 離線檢視。

## Commands

All commands are run from the repo root.

```bash
# 新增一天資料（從 JSON 檔或直接 CLI 參數）
python pipeline.py append <json_file>
python pipeline.py append --date 2026-04-18 --rate 172 \
  --bull "..." --bear "..." --top5 "..."

# 更新 dashboard_all.html header 文字（資料由 fetch 動態載入，rebuild 只同步 fallback 文字）
python pipeline.py rebuild

# 驗證資料完整性（schema、年份檔 vs merged 同步、TWII 缺漏、rate_alert 殘留）
python pipeline.py check

# 顯示資料概況（筆數、日期範圍、警戒日、TWII 缺漏）
python pipeline.py status

# 列出所有已收錄的日期
python pipeline.py dates
```

Windows 一鍵腳本：
- `啟動Dashboard.bat` — 啟動 `python -m http.server 8899` 並用 Chrome 開啟 dashboard
- `CommitAndPush.bat` — `git add -A && commit && push`（commit message 為寫死字串，若要重用須先改訊息）

No test suite, no linter, no build step — pure data pipeline + static HTML。`check` 指令扮演 smoke test 角色。

## Architecture

### Data flow

```
單日 JSON record
   │
   ▼
pipeline.py append （含 schema 驗證）
   │
   ├─► data/stock_data_YYYY.json      （依日期年份分檔，排序後寫入）
   └─► data/all_data_merged.json      （全歷史合併檔，Dashboard 的資料源）

Dashboard 由瀏覽器直接 fetch 資料：
   dashboard_all.html ──fetch──► data/all_data_merged.json
                      ──fetch──► data/twii_all.json

pipeline.py rebuild 只更新 HTML header 的 fallback 文字
（`<div class="sub">...</div>`），供 JS 載入前顯示。
```

**Dashboard 必須透過 HTTP server 開啟**：`file://` 協議會因 CORS 擋住 fetch。`啟動Dashboard.bat` 會起 `python -m http.server 8899` 並開 Chrome。

`rebuild` 的標記（`<div class="sub">...</div>`）若被改壞會 raise RuntimeError，不再靜默失敗。

### Record schema

單筆資料格式（append 時的 JSON 檔內容；儲存於 year 檔 與 merged 檔）：

```json
{
  "date": "2026-04-15",
  "bull": "台積電,聯發科,鴻海",
  "bear": "長榮,陽明",
  "rate": 172,
  "top5_margin_reduce_inst_buy": "台積電,富邦金,玉山金,中信金,國泰金"
}
```

- `bull` / `bear`：股名或股號，**逗號分隔、無空格**。歷史資料（2021–2024）多為股號，近期（2026）改用股名。
- 無 `rate_alert` 欄位 — 警戒由 Dashboard 直接從 `rate >= 170` 推導。append 時若 record 仍含此欄位，`validate_record()` 會 raise。
- 股名保留特殊後綴 `*`（如 `可寧衛*`）與 `-KY`（如 `中美-KY`）。

### Files to know

- `pipeline.py` — 唯一的 Python 入口，~150 行，四個指令全在這裡。
- `dashboard_all.html` — 單檔 dashboard，含 Chart.js CDN + 內嵌 RAW/TWII 資料，可離線開啟。
- `data/all_data_merged.json` — Dashboard 的實際資料源，**所有年份檔合併後的排序陣列**。
- `data/stock_data_YYYY.json` — 依年份分檔，結構為 `{"year": 2026, "trading_days": N, "data": [...]}`。
- `data/twii_all.json` — 加權指數收盤價（日期 → 數值 dict）。
- `scraper_guide.md` — scantrader.com 的資料擷取流程（手動操作瀏覽器 + OCR 圖片），不是自動化腳本。
- `PROGRESS.md` — 歷史回補進度紀錄（2024 / 2023 回補中，2022 / 2021 待建），時間戳記為 2026-04-12。

## Working conventions

- 新增資料時同時更新 **year 檔與 merged 檔**（`pipeline.py append` 會一次處理，但手動改 JSON 時兩邊都要改，否則 `pipeline.py check` 會報 sync 錯）。
- Append 後 dashboard 會自動看到新資料（fetch 載入），**不需要 rebuild 才能更新資料**；`rebuild` 只更新 header fallback 文字。
- 回補舊日期時，`append` 會依日期排序插入正確位置（不是 append-only）。
- 所有 JSON 檔以 UTF-8 讀寫、`ensure_ascii=False`、`indent=2`；中文股名不轉義。
- `pipeline.py check` 作為 smoke test：append 後、commit 前跑一次，可擋 schema 錯誤、檔案不同步、rate_alert 殘留。
