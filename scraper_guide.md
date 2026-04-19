# 台股借券融資資料擷取指南

## 網站結構
- 文章列表：https://scantrader.com/u/9769/articles
- 目標文章標題格式：`MM-DD 台股-借券賣出-融資關鍵資料`
- 文章內含 6 張 GCS 圖片，**最後 4 張**依序為：
  1. `bull` — 借券賣出減少排行（偏多）
  2. `bear` — 借券賣出增加排行（偏空）
  3. `rate` — 融資多空走勢圖
  4. `fusion` — 融資增減排行

---

## Step 1：從文章列表頁取得所有文章 URL

需在 https://scantrader.com/u/9769/articles 頁面，
**用真實滑鼠捲動事件**（JS 的 window.scrollTo 無效）觸發 infinite scroll。

捲動到底後，在 Console 執行：
```javascript
const titleEls = Array.from(document.querySelectorAll('*')).filter(el =>
  el.children.length < 3 && el.innerText?.trim().match(/^\d{2}-\d{2}台股-借券賣出/)
);
const results = [];
titleEls.forEach(el => {
  const link = el.closest('[href]') || el.parentElement?.closest('[href]') 
    || el.closest('li,article,[class*="item"]')?.querySelector('a[href*="/article/"]');
  if (link) {
    const m = el.innerText.trim().match(/^(\d{2}-\d{2})/);
    if (m) results.push({date: m[1], url: link.href});
  }
});
const unique = [...new Map(results.map(r=>[r.date,r])).values()]
  .sort((a,b)=>a.date.localeCompare(b.date));
window._allBorrowArticles = unique;
console.log(`Total: ${unique.length}`, unique.map(r=>r.date).join(', '));
```

---

## Step 2：進入文章頁取得 4 張圖片 URL

在文章頁 Console 執行：
```javascript
const imgs = Array.from(document.querySelectorAll('img[src*="storage.googleapis.com"]')).map(i=>i.src);
const [bull, bear, rate, fusion] = imgs.slice(-4);
JSON.stringify({bull, bear, rate, fusion});
```

---

## Step 3：讀取各圖片資料

### 方法：直接在瀏覽器開啟圖片 URL，再截圖/縮放閱讀

#### 圖 1 — bull（借券賣出減少排行）
- 圖片標題：「借券賣出減少排行」
- 版面：左（外資買超前20名）｜**中（同步偏多標的）**｜右（借券減少前20名）
- ✅ **只讀取中間欄**（有色文字，約 5-10 個股名）
- Zoom region: `[350, 100, 650, 850]`

#### 圖 2 — bear（借券賣出增加排行）
- 圖片標題：「借券賣出增加排行」
- 版面：左（外資賣超前20名）｜**中（同步偏空標的）**｜右（借券增加前20名）
- ✅ **只讀取中間欄**
- Zoom region: `[350, 100, 650, 850]`

#### 圖 3 — rate（融資多空走勢圖）
- 底部有一排日期與百分比表格
- ✅ 讀最後一欄（文章日期）的百分比數字，取整數
- Zoom region: `[0, 700, 940, 945]`

#### 圖 4 — fusion（融資增減排行）
- 版面：左（融資增加）｜右（融資減少）
- ✅ **只看右半部**，找法人欄為**正數（藍色）**的前5支股票
- Zoom region: `[470, 100, 940, 600]`

---

## Step 4：資料格式

```json
{
  "date": "2026-MM-DD",
  "bull": "股名1,股名2,股名3",
  "bear": "股名1,股名2",
  "rate": 171,
  "top5_margin_reduce_inst_buy": "股名1,股名2,股名3,股名4,股名5"
}
```

- 股名以逗號分隔，不加空格，不加引號
- 保留特殊後綴：`*`（如 `可寧衛*`）、`-KY`（如 `中美-KY`）
- 警戒（`rate >= 170`）由 Dashboard 即時推導，**不需要也不可寫 `rate_alert` 欄位**（寫了會被 `pipeline.py append` 的 schema 檢查擋下）

---

## 專案目錄結構

```
法人日資料/
├── dashboard.html                    ← 主 Dashboard（瀏覽器開啟）
├── 啟動Dashboard.bat                 ← 一鍵啟動 HTTP Server + 開啟 Dashboard
├── pipeline.py                       ← 資料工具（append 記錄、存圖片）
├── scraper_guide.md                  ← 本指南
├── images/                           ← 原始圖片（自動建立）
│   └── 2026-MM-DD/
│       ├── bull.png
│       ├── bear.png
│       ├── rate.png
│       └── fusion.png
└── data/
    ├── stock_data_2026Q1.json        ← 主資料（61 個交易日）
    ├── stock_data_2026Q1.csv         ← 同上（CSV 格式）
    ├── twii_2026Q1.json              ← 加權指數收盤價
    ├── 台股借券融資關鍵數據報告_2026Q1.xlsx  ← Excel 報告
    ├── pending_articles.json         ← 待抓取文章清單（備用）
    └── batches/                      ← 原始批次匯入資料（歷史備份）
        ├── batch_A.json
        └── ...
```

---

## 注意事項
- 12月文章（12-xt）屬於2025年，非2026年，注意年份邊界
- 部分 OCR 常見錯誤：`可等衛*` → `可寧衛*`、`緒創` 可能是 `緯創`
- 每篇文章抓取約需 10-15 秒（4張圖各需一次導覽）
