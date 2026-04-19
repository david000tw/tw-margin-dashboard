# 台股融資借券 Dashboard

每日更新的台股法人動向、融資融券分析 Dashboard。

**需求**：Python 3.8+、Claude Code CLI（排程自動化用）、Chrome 瀏覽器。

**建議初次設定**（一次即可）：
```bash
bash scripts/install-hooks.sh   # 裝 git pre-commit hook,commit 前自動跑 pipeline.py check
python tests/test_pipeline.py   # 執行單元測試確認環境 OK
```

## 檔案結構

```
.
├── pipeline.py                    # 資料 pipeline (append/rebuild/check/status/dates)
├── dashboard_all.html             # Dashboard (fetch JSON,需 HTTP server)
├── scraper_guide.md               # 4 張圖解讀規則 + OCR 常見錯誤
├── 啟動Dashboard.bat              # 啟動 HTTP server + 開 Chrome
├── DailyFetch.bat                 # 排程器呼叫的包裝腳本
├── 安裝排程.bat / 卸載排程.bat     # Windows 工作排程器註冊 / 解除
├── .claude/
│   └── commands/daily-fetch.md    # /daily-fetch 斜線指令流程
└── data/
    ├── all_data_merged.json       # 全歷史合併 (dashboard 主來源)
    ├── stock_data_YYYY.json       # 依年份分檔
    └── twii_all.json              # 加權指數收盤價
```

## 使用方式

```bash
# 新增一天資料（從 JSON 檔）
python pipeline.py append data/new_day.json

# 或用 CLI 參數直接新增
python pipeline.py append \
  --date 2026-04-18 \
  --rate 172 \
  --bull "台積電,聯發科,鴻海" \
  --bear "長榮,陽明" \
  --top5 "台積電,富邦金,玉山金,中信金,國泰金"

# 同步 Dashboard header 文字
python pipeline.py rebuild

# 驗證資料完整性（schema、年份檔與 merged 同步、TWII 缺漏）
python pipeline.py check

# 查看目前狀態
python pipeline.py status

# 列出所有已收錄日期
python pipeline.py dates
```

## 新增一筆資料的格式

```json
{
  "date": "2026-04-15",
  "bull": "台積電,聯發科,鴻海",
  "bear": "長榮,陽明",
  "rate": 172,
  "top5_margin_reduce_inst_buy": "台積電,富邦金,玉山金,中信金,國泰金"
}
```

融資率警戒（`rate >= 170`）由 Dashboard 即時推導，record 不需填 `rate_alert`。

## Dashboard

`dashboard_all.html` 會 `fetch('./data/all_data_merged.json')` 載入資料，**必須透過 HTTP server 開啟**：

```bash
# Windows：雙擊
啟動Dashboard.bat

# 手動
python -m http.server 8899
# 瀏覽器開 http://localhost:8899/dashboard_all.html
```

直接雙擊 HTML 用 `file://` 開啟會因 CORS 擋住 fetch，畫面會顯示錯誤提示。

## 每日自動擷取（排程）

透過 Claude Code 自訂斜線指令 `/daily-fetch` 自動從 scantrader.com 擷取 + OCR + 寫入 + commit + push。

### 手動測試（建議先做幾次確認 OCR 準確度）

在專案目錄啟動 Claude Code：

```bash
cd "C:\Users\yen\Desktop\法人日資料"
claude
```

指令用法：

| 指令 | 行為 |
|---|---|
| `/daily-fetch` | 補齊從 merged 最新日次日到「昨天」的所有可取得資料 |
| `/daily-fetch 2026-04-18` | 只抓指定單日 |
| `/daily-fetch --dry-run` | 補齊 dry-run：只跑 OCR 印結果，不寫檔不 commit |
| `/daily-fetch --dry-run 2026-04-18` | 單日 dry-run |

### 安裝本地排程

確認 `/daily-fetch` 手動跑穩定後，雙擊：

```
安裝排程.bat
```

會在 Windows 工作排程器建立兩個工作（**不需系統管理員權限**）：

| 工作名稱 | 時間 | 用途 |
|---|---|---|
| `FaRenRiZiLiao_Daily_Primary` | 每日 23:30 | 主排程（台股資料通常 23:00 後發布） |
| `FaRenRiZiLiao_Daily_Fallback` | 每日 07:00 | 備援（當夜網站延遲或機器當時休眠） |

兩者都只在**使用者登入時執行**，電腦關機 / 登出時不會跑。

### 執行紀錄

- 每次執行：`logs/daily-fetch-YYYY-MM-DD-HHMMSS.log`（每次一檔、不再互相覆蓋）
- 失敗彙總：`logs/alerts.log`（累積，定期檢視即可）
- 自動保留最近 60 個 log 檔，舊檔會被 `DailyFetch.bat` 清除
- `logs/` 已在 `.gitignore`，不進 git

### OCR 讀不準時（manual review）

`/daily-fetch` 遇到 OCR 信心 < 80% 的日期會自動加入 `data/manual_review.txt`，下次執行**跳過**這些日期避免卡住。

人工處理後：
1. 用 `pipeline.py append` 手動補該日
2. 從 `data/manual_review.txt` 刪除該行
3. 下次 `/daily-fetch` 就會再次嘗試（若網站仍有文章）

### 檢視排程狀態

```cmd
schtasks /query /tn "FaRenRiZiLiao_Daily_Primary" /v /fo LIST
```

### 手動觸發（測試排程是否正常）

```cmd
schtasks /run /tn "FaRenRiZiLiao_Daily_Primary"
```

### 卸載排程

雙擊 `卸載排程.bat`。

### 排程失敗時的除錯順序

1. 看最新的 `logs/daily-fetch-*.log`（通常有 Claude 的錯誤輸出）
2. `logs/alerts.log` 看歷次失敗時間點
3. 手動跑一次 `/daily-fetch --dry-run` 對照（排除網站 / OCR 問題）
4. 若是權限提示卡住 → 確認 `.bat` 有 `--permission-mode bypassPermissions`
