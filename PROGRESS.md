# 法人日資料 爬取進度

**最後更新：** 2026-04-12 18:48

## 整體目標
從 scantrader.com 抓取台股法人資料，目標年份：2024、2023、2022、2021

## 目前進度

| 年份 | 已存筆數 | 最舊已存日期 | 剩餘未存 | 批次檔 |
|------|---------|------------|---------|--------|
| 2024 | 152 | 2024-05-20 | 88 | batch_2024.json (240 筆) |
| 2023 | 152 | 2023-05-24 | 87 | batch_2023.json (239 筆) |
| 2022 | 0 | - | 待建批次檔 | 尚未建立 |
| 2021 | 0 | - | 待建批次檔 | 尚未建立 |

## 下一步待處理（按優先順序）

### 2024 下一批
- 2024-05-17 → https://scantrader.com/article/018f86e9383100000600000000000000
- 2024-05-16 → https://scantrader.com/article/018f81c1a5dc00000600000000000000
- 2024-05-15 → https://scantrader.com/article/018f7c9b096a00000609000000000000
- 2024-05-14 → https://scantrader.com/article/018f7775bc7100000600000000000000
- 2024-05-13 → https://scantrader.com/article/018f7250f6060000060a000000000000

### 2023 下一批
- 2023-05-23 → https://scantrader.com/article/018848f3ef8700000608000000000000
- 2023-05-22 → https://scantrader.com/article/018843cea07c000038fd000000000000
- 2023-05-19 → https://scantrader.com/article/0188345908c800000608000000000000
- 2023-05-18 → https://scantrader.com/article/01882f340a4f000038fd000000000000
- 2023-05-17 → https://scantrader.com/article/01882a122ea2000038fd000000000000

## 技術規則（關鍵勿忘）

### 3-tab 架構
- **Tab 451966803** = 2024 文章 tab
- **Tab 451966808** = 2023 文章 tab  
- **Tab 451966802** = scratch PNG 瀏覽器（讀圖用）

### PNG 擷取 JS（在文章 tab 執行）
```js
const imgs=Array.from(document.querySelectorAll('img')).map(i=>i.src).filter(s=>s.includes('storage.googleapis.com')&&s.includes('.png'));const n=imgs.length,off=(n===7)?1:0;`n=${n} bull=${imgs[2+off].split('/').pop()} bear=${imgs[3+off].split('/').pop()} rate=${imgs[4+off].split('/').pop()} fusion=${imgs[5+off].split('/').pop()}`
```

### 圖片解讀規則
- **BULL 圖**（借券賣出減少）：LEFT 欄 = 外資買超前20名 → bull codes
- **BEAR 圖**（借券賣出增加）：LEFT 欄 = 外資賣超前20名 → bear codes
- **RATE 圖**：讀最右日期欄數值，取整數（警戒判定 `rate>=170` 由 Dashboard 即時推導，不寫入 record）
- **FUSION 圖**：RIGHT 欄按融資減少排序，跳過法人≤0 的列，取前5個股票名稱

### Tab 導航注意事項
- Tab 808 **一定要 navigate 兩次**（第一次不一定切換成功）
- Tab 803 偶爾需要兩次，確認 tab title 的日期吻合再擷取

### GCS 圖片 URL 格式
```
https://storage.googleapis.com/quants-images-prod/scantrader/upload/{filename}.png
```

### 存檔 Python pattern
```python
import json
from pathlib import Path
base = '/sessions/sleepy-upbeat-ramanujan/mnt/stock-analysis/法人日資料/data'
JSON_FILE = Path(f'{base}/stock_data_YEAR.json')
record = {{'date':'YYYY-MM-DD','bull':'...','bear':'...','rate':168,'top5_margin_reduce_inst_buy':'...'}}
data = json.loads(JSON_FILE.read_text(encoding='utf-8'))
existing = {{d['date'] for d in data['data']}}
if record['date'] not in existing:
    data['data'].append(record)
    data['data'].sort(key=lambda x: x['date'], reverse=True)
    data['trading_days'] = len(data['data'])
    JSON_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
```

## 待辦清單
- [ ] 完成 2024 剩餘 {len(unsaved24)} 筆（最舊 {unsaved24[-1]['date'] if unsaved24 else 'done'}）
- [ ] 完成 2023 剩餘 {len(unsaved23)} 筆（最舊 {unsaved23[-1]['date'] if unsaved23 else 'done'}）
- [ ] 建立 2022 batch_2022.json 並開始爬取
- [ ] 建立 2021 batch_2021.json 並開始爬取
