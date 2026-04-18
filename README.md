# 台股融資借券 Dashboard

每日更新的台股法人動向、融資融券分析 Dashboard。

## 檔案結構

```
.
├── pipeline.py              # 每日資料更新 pipeline
├── dashboard_all.html       # 主要 Dashboard (獨立 HTML,可離線檢視)
├── scraper_guide.md         # 資料抓取流程說明
├── PROGRESS.md              # 進度記錄
├── 啟動Dashboard.bat         # Windows 一鍵開啟 Dashboard
└── data/
    ├── all_data_merged.json       # 所有年份合併資料
    ├── stock_data_YYYY.json       # 各年度資料
    ├── stock_data_2026Q1.csv      # 季度 CSV
    ├── twii_all.json              # 加權指數資料
    └── 台股借券融資關鍵數據報告_2026Q1.xlsx
```

## 使用方式

```bash
# 新增一天資料
python pipeline.py append data/new_day.json

# 重建 Dashboard
python pipeline.py rebuild

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
  "rate": 163,
  "rate_alert": true,
  "top5_margin_reduce_inst_buy": "台積電,富邦金,玉山金,中信金,國泰金"
}
```

## Dashboard

開啟 `dashboard_all.html` 即可在瀏覽器中檢視完整的分析結果 (包含融資率警戒日、法人買賣超、加權指數走勢等)。
