---
description: 從 scantrader.com 補齊「目前最新資料的次日」到「昨天」之間所有可取得的交易日資料，寫入 data/ 與 dashboard、驗證、commit、push
argument-hint: "(空=補齊) | YYYY-MM-DD(單日) | --dry-run | --dry-run YYYY-MM-DD"
---

你的任務：把 merged 資料補齊到**小於今天**的最新日。此指令可能由排程器呼叫（非互動），也可能由使用者手動執行。不論哪種情境，**完全遵守這份文件**，不要假設對話歷史裡有額外資訊。

---

# Step 0：建立上下文（必做）

先讀以下檔案建立專案知識（**不要略過**）：

1. `CLAUDE.md` — 整體架構、資料流向、schema 規則
2. `scraper_guide.md` — scantrader 網站結構、4 張圖的解讀規則、OCR 常見錯誤
3. `pipeline.py` — 確認 `validate_record`、`append`、`check`、`rebuild` 介面

---

# Step 1：解析輸入與決定目標日期清單

先解析 `$ARGUMENTS`，可能為：

| 輸入 | 模式 | 行為 |
|---|---|---|
| 空字串 | **補齊（預設）** | 從 merged 最新日的次日補到「昨天」 |
| `YYYY-MM-DD` | 單日 | 只抓該日 |
| `--dry-run` | dry-run 補齊 | 補齊模式，但只 OCR 印結果不寫檔 |
| `--dry-run YYYY-MM-DD` | dry-run 單日 | 單日 OCR 印結果不寫檔 |
| 其他 | 錯誤 | stop 並要求使用者重下指令 |

**dry-run 模式**：只跑 Step 3–5（找文章 + OCR + 組 record），**不寫入、不 commit**，record 印在終端供人工比對。

## 1a. 取得當前狀態

```bash
python pipeline.py status
```
- 解析輸出拿到 `最新一筆` 日期（記為 `LATEST`）
- 取得今日（台北時區）：`TZ=Asia/Taipei date +%Y-%m-%d`（記為 `TODAY`）
- 合法範圍：`YESTERDAY = TODAY - 1 天`

## 1b. 計算目標清單

- **補齊模式**：`targets = [LATEST+1, LATEST+2, …, YESTERDAY]` 所有日期（含週末，後面會由「網站有無文章」過濾）
- **單日模式**：`targets = [指定日期]`
  - 若指定日期 `> YESTERDAY`（例如使用者下錯日期）→ stop 並回報「不可抓取今日或未來資料（資料通常 23:00 後才發布）」
  - 若指定日期已在 `python pipeline.py dates` 輸出中：
    * **dry-run 模式** → 印警告「{日期} 已存在，正式跑會跳過；dry-run 繼續讓你驗證 OCR」，繼續 Step 3（用於 OCR 驗證）
    * **正式模式** → 回報「已存在，跳過」並 exit 0

## 1c. 若補齊模式且 `targets` 為空
表示資料已最新到昨天，回報「已是最新（截至 {YESTERDAY}）」並 **exit 0**。

---

# Step 2：前置檢查

**Working tree 乾淨嗎**：
```bash
git status --porcelain data/ dashboard_all.html
```
- **正式模式**：若有未 commit 變動 → stop 並回報「working tree 不乾淨，拒絕執行」，避免把別人的改動混進本次 commit
- **dry-run 模式**：印提醒「working tree 不乾淨，若是正式跑會被擋」後繼續（dry-run 不寫檔、不 commit，不會污染）

---

# Step 3：一次取得 scantrader 文章清單

（比「每個日期各找一次文章」快很多。）

1. chrome-devtools MCP `new_page` 導航到 `https://scantrader.com/u/9769/articles`
2. `wait_for` 等待 `a[href*="/article/"]` 出現
3. `evaluate_script`：
   ```js
   Array.from(document.querySelectorAll('a[href*="/article/"]'))
     .map(a => {
       const m = a.innerText.trim().match(/^(\d{2}-\d{2})\s*台股[\-\s]*借券賣出/);
       return m ? { mmdd: m[1], href: a.href } : null;
     })
     .filter(Boolean);
   ```
4. 若最舊的 `mmdd` 還晚於 `targets` 清單中最早日期 → 用 `drag` 手勢觸發 infinite scroll（JS `window.scrollTo` 對此站無效，必須模擬滑鼠）。每次 drag 後 `wait_for` 1.5 秒、重跑上面 script，最多 10 次。
5. 取得全部文章清單，建 `{mmdd: href}` 對照表（注意：MM-DD 不含年份，若跨年要小心）。

## 3a. 交叉比對

- 對 `targets` 每個 `YYYY-MM-DD`，取 `MM-DD` 到對照表查
- 有 → 加入 `to_fetch = [(date, url), ...]`
- 無 → 歸入 `skipped_no_article`（可能休市 / 尚未發布）

## 3b. 若 `to_fetch` 為空

正常結束，回報：
```
📭 無可抓取文章
  目標範圍: {LATEST+1} ~ {YESTERDAY}
  網站上無對應文章的日期: {skipped_no_article}
  可能原因: 休市日 / 網站尚未發布
```
**exit 0**（非錯誤；排程會明日再試）。

---

# Step 4~8：對 `to_fetch` 每個 `(date, url)` 逐一處理

**重要**：每一筆都完整跑 Step 4–8 且成功（含 commit）後才處理下一筆。避免中途失敗留下混合狀態。

對每個 `(date, url)`：

## 4. 進入文章頁取 4 張圖片 URL

1. `navigate_page` 到 `url`
2. `wait_for` 等待 `img[src*="storage.googleapis.com"]`
3. `evaluate_script`：
   ```js
   const imgs = Array.from(document.querySelectorAll('img[src*="storage.googleapis.com"]'))
                     .map(i => i.src);
   const last4 = imgs.slice(-4);
   ({ bull: last4[0], bear: last4[1], rate: last4[2], fusion: last4[3], total: imgs.length });
   ```
4. 若 `total < 4` → 再 navigate 一次（此站偶爾要二次導航）；仍不足就 stop 並回報該日失敗

## 5. 讀 4 張圖（OCR）

對每張圖：
- `new_page` 打開 PNG URL
- `take_screenshot`（full page, PNG）
- 用你的 vision 能力解讀
- `close_page`

### 解讀規則

| 圖 | 擷取內容 | 欄位 |
|---|---|---|
| **bull**（借券賣出減少排行） | 中間欄「同步偏多標的」有色股名 5–10 個 | `bull` |
| **bear**（借券賣出增加排行） | 中間欄「同步偏空標的」有色股名 5–10 個 | `bear` |
| **rate**（融資多空走勢圖） | 底部表格**最右欄**（當日日期）的百分比整數 | `rate` |
| **fusion**（融資增減排行） | 右半部「法人欄為正數（藍色）」的前 5 個股名 | `top5_margin_reduce_inst_buy` |

### 股名規則

- 逗號分隔、**不加空格、不加引號**
- 保留 `*`（如 `可寧衛*`）、`-KY`（如 `中美-KY`）
- OCR 易錯：`可等衛*` → `可寧衛*`、`緒創` → `緯創`
- 任何欄位信心 < 80% → 該日改為 stop 並要求人工確認，**不要猜**

## 6. 組 record

產生 `tmp_record.json`（repo root 暫存檔）：
```json
{
  "date": "YYYY-MM-DD",
  "bull": "...",
  "bear": "...",
  "rate": 172,
  "top5_margin_reduce_inst_buy": "..."
}
```
**不要**寫 `rate_alert` 欄位。`rate` 必須 `int`。

## 7. 寫入、驗證、rebuild（非 dry-run 才跑）

```bash
python pipeline.py append tmp_record.json
python pipeline.py check
python pipeline.py rebuild
rm tmp_record.json
```
任一步失敗 → rollback 並停止整批處理（已成功 commit 的前幾天保留）：
```bash
git checkout -- data/ dashboard_all.html
rm -f tmp_record.json
```

**dry-run 模式**：略過此步，把 record 內容印到終端即可。

## 8. Commit + push（非 dry-run 才跑）

```bash
git add data/ dashboard_all.html
git commit -m "data: YYYY-MM-DD"
git push
```

注意：
- commit message 只有 `data: 日期`
- 不加 `Co-Authored-By`
- **絕對不要** `--force`
- push 失敗（網路 / 衝突）→ stop 並回報，**不要** reset 或 force

---

# Step 9：最終報告

**成功時**（補齊模式可能處理多天）：
```
✅ 補齊完成
  處理範圍: {LATEST+1} ~ {YESTERDAY}
  新增 {N} 天:
    - 2026-04-18  rate=172%  (commit abc1234)
    - 2026-04-21  rate=175% ⚠️ (commit def5678)
  跳過（網站無文章）: [...]
  目前總筆數: {N}
```

**失敗時**：
```
❌ 補齊中斷
  已成功: [2026-04-18]
  失敗於: 2026-04-21
  卡在步驟: {N}
  錯誤: {具體訊息}
  已恢復: {git status 是否乾淨}
  建議: {下一步}
```

**無事可做時**：
```
📭 已是最新 (截至 {YESTERDAY})
```

---

# 錯誤處理原則

1. **寧缺勿錯**：OCR 信心不足 → stop 並要求人工，不要猜
2. **檔案一致性**：append 後 check 失敗 → **必須 rollback**
3. **禁用**：`--force` push、`git reset --hard`、`rm -rf data/`
4. **網站問題 vs 資料問題分開**：
   - 找不到文章 / 圖片載入失敗 → 網站問題，exit 0（排程會明日再試）
   - OCR 讀錯、schema 驗證失敗 → 資料問題，exit 1（需人工介入）
5. **不自己維護休市日曆**；以「網站上是否有對應 MM-DD 文章」為唯一事實
6. **批次中任一天失敗**：已成功 commit 的保留，失敗的那天 rollback，**不繼續**處理後續日期
