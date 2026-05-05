---
description: 從 scantrader.com 補齊「目前最新資料的次日」到「昨天」之間所有可取得的交易日資料，寫入 data/ 與 dashboard、驗證、commit、push
argument-hint: "(空=補齊) | YYYY-MM-DD(單日) | --dry-run | --dry-run YYYY-MM-DD"
---

你的任務：把 merged 資料補齊到**小於今天**的最新日。此指令可能由排程器呼叫（非互動），也可能由使用者手動執行。不論哪種情境，**完全遵守這份文件**，不要假設對話歷史裡有額外資訊。

---

# Step 0：建立上下文（必做）

先讀以下檔案建立專案知識（**不要略過**）：

1. `CLAUDE.md` — 整體架構、資料流向、schema 規則
2. `scraper_guide.md` — **OCR 解讀規則與股名規範的唯一事實來源**（本檔不重複這些規則）
3. `pipeline.py` — 確認 `validate_record`、`append`、`check` 介面

---

# Step 1：解析輸入與決定目標日期清單

`$ARGUMENTS` 可能為：

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

- **補齊模式**：`targets = [LATEST+1, …, YESTERDAY]` 所有日期
- **單日模式**：`targets = [指定日期]`
  - 若指定日期 `> YESTERDAY` → stop 並回報「不可抓取今日或未來資料（資料通常 23:00 後才發布）」
  - 若指定日期已在 `python pipeline.py dates` 輸出中：
    * **dry-run 模式** → 印警告後繼續 Step 3（用於 OCR 驗證）
    * **正式模式** → 回報「已存在，跳過」並 exit 0

## 1c. 排除 manual_review 清單

讀 `data/manual_review.txt`（若不存在視為空）。格式：每行一個 `YYYY-MM-DD` 或註解（開頭 `#`）。
- 這些日期已知 OCR 困難、需人工處理，**從 targets 中剔除**避免排程卡死迴圈
- 印提示：「略過 N 筆人工待處理日期: [...]」

## 1d. 若 `targets` 為空
表示資料已最新到昨天（或剩下的都在 manual_review），回報「已是最新」並 **exit 0**。

---

# Step 2：前置檢查

**Working tree 乾淨嗎**：
```bash
git status --porcelain data/ dashboard_all.html
```
- **正式模式**：若有未 commit 變動 → stop 並回報「working tree 不乾淨，拒絕執行」
- **dry-run 模式**：印提醒後繼續

---

# Step 3：一次取得 scantrader 文章清單

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
4. 若最舊的 `mmdd` 還晚於 `targets` 清單中最早日期 → 用 `drag` 手勢觸發 infinite scroll（JS `window.scrollTo` 對此站無效）。每次 drag 後 `wait_for` 1.5 秒、重跑 script，**最多 10 次**。
5. 取得全部文章清單，建 `{mmdd: href}` 對照表（注意：跨年時 MM-DD 重複的小心）。

## 3a. 交叉比對

- 對 `targets` 每個 `YYYY-MM-DD`，取 `MM-DD` 到對照表查
- 有 → 加入 `to_fetch = [(date, url), ...]`
- 無 → 歸入 `skipped_no_article`（可能休市 / 尚未發布）

## 3b. 若 `to_fetch` 為空

正常結束，回報：
```
無可抓取文章
  目標範圍: {LATEST+1} ~ {YESTERDAY}
  網站上無對應文章的日期: {skipped_no_article}
  可能原因: 休市日 / 網站尚未發布
```
**exit 0**（非錯誤；排程會明日再試）。

---

# Step 4~8：對 `to_fetch` 每個 `(date, url)` 逐一處理

**重要**：每一筆都完整跑 Step 4–8 且 commit 成功後才進下一筆。

## Retry 策略（所有網路操作適用）

`navigate_page`、`evaluate_script`、圖片 `new_page` 這三類操作若失敗：
- 第 1 次失敗 → 等 2 秒重試
- 第 2 次失敗 → 等 5 秒重試
- 第 3 次仍失敗 → stop 該日並加入 `manual_review.txt`（見 Step 5 的 quarantine 規則）

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
4. 若 `total < 4` → 套用上述 retry 策略；3 次仍不足就 quarantine

## 5. 讀 4 張圖（OCR）

對每張圖：
- `new_page` 打開 PNG URL
- `take_screenshot`（full page, PNG）
- 用你的 vision 能力解讀
- `close_page`

**OCR 規則見 `scraper_guide.md`**。重點摘要（必讀，避免再產生錯字 record）：

> **每一列都同時看到「代號 + 股名」並列時，只記錄代號**（4-6 位數字，可能含 `*` 或 `-KY` 後綴）。代號比中文股名穩定（OCR 不會把 `2330` 讀錯成別的數字，但容易把「聯詠」讀成「聯泳」）。
>
> 只有罕見情況（興櫃股無代號或代號被遮擋）才退而取股名。
>
> dashboard 會透過 `data/symbol_index.json` 反查代號對應的名稱，使用者看到的還是「2330 台積電」。

### Quarantine 機制（OCR 信心不足時）

任何欄位信心 < 80% 或有明顯異常（例如讀出空白、亂碼、日期對不上）時：
1. **不要猜、不要寫 record**
2. 把該日追加到 `data/manual_review.txt`：
   ```
   echo "{YYYY-MM-DD}  # OCR 不確定: {具體原因}" >> data/manual_review.txt
   ```
3. 跳過該日（**非 stop 整批**），繼續處理下一筆 `to_fetch`
4. Step 9 的最終報告列出所有 quarantine 的日期

這避免排程被同一個 OCR 卡住日永久困住。使用者人工驗證後從 `manual_review.txt` 刪除該行即可下次重抓。

## 6. 組 record

### 6a. 套用 OCR 錯字對照（fallback 用）

如果 Step 5 你**真的拿到代號**，可跳過這段（代號 OCR 不會字形混淆）。

如果某列 OCR 沒抓到代號、只抓到股名（罕見，例如興櫃或圖被遮擋），讀 `data/ocr_corrections.json` 的 `ocr_to_correct` dict 對該股名做 lookup-and-replace：

```python
import json
with open('data/ocr_corrections.json', encoding='utf-8') as f:
    fixes = json.load(f)['ocr_to_correct']

def apply_fixes(s: str) -> str:
    return ','.join(fixes.get(x.strip(), x.strip()) for x in s.split(',') if x.strip())

bull = apply_fixes(bull_raw)
bear = apply_fixes(bear_raw)
top5 = apply_fixes(top5_raw)
```

字典含 ~23 條歷史已知 OCR 錯字（如「聯泳→聯詠」「光豐金→永豐金」），是安全網而非主要手段。**主要手段是 Step 5 寫對代號**。

### 6b. 產生 `tmp_record.json`

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

### 6c. 發現新 OCR 錯字時

如果你 OCR 出的某個股名 **不在** `ocr_corrections.json` 但你**有把握**它是某檔常見股票的 OCR 錯字（字形相近且唯一對應），追加進 `ocr_corrections.json` 並在最終報告（Step 9）列出。下次抓取自動套用。

不確定的不要加 — 寧可讓 record 寫原 OCR 結果由 dashboard 顯示「(待補)」，不要寫錯誤對應。

## 7. 寫入、驗證（非 dry-run 才跑）

```bash
python pipeline.py append tmp_record.json
python pipeline.py check
rm tmp_record.json
```
任一步失敗 → rollback 並停止整批：
```bash
git checkout -- data/
rm -f tmp_record.json
```

**dry-run 模式**：略過此步，把 record 印到終端。

## 8. Commit + push（非 dry-run 才跑）

```bash
git add data/
git commit -m "data: YYYY-MM-DD (rate=NNN)"
git push
```

Commit message 規則：
- 格式：`data: 日期 (rate=N)`；若 rate ≥ 170 加警戒標記：`data: 日期 (rate=N, alert)`
- 不加 `Co-Authored-By`
- **絕對不要** `--force`
- push 失敗 → stop 並回報（**不要** reset 或 force）

---

# Step 9：最終報告

**成功時**：
```
補齊完成
  處理範圍: {LATEST+1} ~ {YESTERDAY}
  新增 {N} 天:
    - 2026-04-18  rate=172  (commit abc1234)
    - 2026-04-21  rate=175 alert  (commit def5678)
  跳過 (網站無文章): [...]
  Quarantine (加入 manual_review): [...]
  目前總筆數: {N}
```

**失敗時**：
```
補齊中斷
  已成功: [2026-04-18]
  失敗於: 2026-04-21
  卡在步驟: {N}
  錯誤: {訊息}
  已恢復: {git status 是否乾淨}
  建議: {下一步}
```

**無事可做時**：
```
已是最新 (截至 {YESTERDAY})
```

---

# 錯誤處理原則

1. **OCR 信心不足 → quarantine**，不 stop 整批
2. **檔案一致性**：append 後 check 失敗 → 必須 rollback
3. **禁用**：`--force` push、`git reset --hard`、`rm -rf data/`
4. **網站問題 vs 資料問題分開**：
   - 找不到文章 / 網路失敗 → 網站問題，exit 0（排程明日再試）
   - schema 驗證失敗 / check 失敗 → 資料問題，exit 1
5. **不自己維護休市日曆**；以網站有無文章為唯一事實
6. **批次中任一天失敗**：已成功 commit 的保留，失敗的那天 rollback，不繼續後續
