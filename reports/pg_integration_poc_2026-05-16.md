# PG 整合 PoC 評估 — OHLCV-強化 Market Analyst

**日期**：2026-05-16
**Spec**：`docs/superpowers/specs/2026-05-16-pg-integration-design.md`
**Plan**：`docs/superpowers/plans/2026-05-16-pg-integration.md`
**結論先寫**：✅ 完全成功。Market Analyst + Risk Manager 都實質使用 OHLCV 指標（ATR / 跳空 / 量價 / K線型態），且 Risk Manager 用 ATR 算具體停損價位。可推進到 daily 整合。

## 跑了什麼

- **PG container**：`docker-compose up -d` 在 `台股開發2/`，port 5433，4.39 版 Docker Desktop（避開 4.40+ Inference Manager bug）
- **Seed**：`seed_full_universe.py` 跑 1976 檔 ×16 年 OHLCV，~1 小時跑完，**6.54M rows**
- **PoC 跑**：`python agents/ta_deepdive.py 2026-05-04 --symbols 2330,2303`
- **耗時**：~4 分鐘（同 close-only，沒有額外延遲）

## 修了兩個 bug 才通

第一次跑 `ohlcv_available: False` — 沒走 OHLCV path。Debug 找到：

1. **`_price_features_from_ohlcv()` 簽名要求 `prices=dict` 但函式體內沒用** — wrapper 沒傳 → TypeError → 被 `except Exception: pass` 吞掉 → fallback close-only
2. **Buffer 1.5x 不夠** — `window=60` 對應 calendar 90 日，但春節 + 國定假日讓實際 trading day 只剩 54。改 **2.5x** 才能保證 trading days >= window

兩個 fix 都 commit 在 `agents/ta_features.py`。改 `except Exception: pass` → `warnings.warn(...)` 避免將來再靜默吞錯。

## 品質評估（Market Analyst 真的有用 OHLCV）

### 2330（台積電）

```
ATR14: 62.14 (佔現價 2.91%, 中等波動)
窗內跳空 48 次 (gap > 0.5%) — threshold 對 2330 偏低
量能: 5 日平均 50.6M vs 20 日平均 37.1M, 量比 1.36 → 量增
candle_pattern: None
```

Market Analyst 引用：「**ATR 達 62 點（2.91%），波動不小**…5 日均量比 20 日均量大 1.36 倍，量能配合上漲屬健康訊號」

### 2303（聯電）

Market Analyst 完整段落（節錄）：
> 2303 近期走勢強勢，均線排列健康：MA5（74.82）大幅領先 MA20（67.71）…**ATR 佔現價 6.63% 屬高波動，加上窗內跳空多達 45 次，意味回檔幅度可能也不小**。最新收盤 77.3，**突破前幾日整理區，若量能未能跟進放大，短期需留意回測 MA5 的風險**。

**Risk Manager REBUTTAL** 進一步把 ATR 變成可執行的停損：
> **ATR 佔現價 6.63%，若依正常風險控制設定 1.5 倍 ATR 為停損緩衝（約 7.7 元），對應個股下行至 69.6 附近才觸發停損**

這是 OHLCV 整合最直接的價值：**從「股價漲跌」上升到「波動率 × 倉位管理」的論述層次**。

### 關鍵字命中（vs PoC 前的純 close 報告）

| 指標 | 2330 | 2303 |
|---|---|---|
| ATR | 3 次 | 4 次 |
| 量增/量縮/量比 | 8 次 | 6 次 |
| 跳空 | 0 次（但 gap_count=48 in features） | 2 次（45 次） |
| K線型態（錘頭/十字/吞噬） | 2 次（提到「吞噬」當論點） | 0 次 |

## 兩個值得記的改進機會

### 1. Gap threshold 對 2330 等大型股太低

`_gap_count` 預設 threshold = 0.5%，但 2330 平均單日波動 1-3%，幾乎天天 > 0.5% → 48/60 天被算「跳空」。對小型股可能適中，對大型股要動態調整（例如以 ATR% 為基準）。**不急修，但 list 起來**。

### 2. Reflection / Lesson loop 用 OHLCV 的話會更準

目前 18 條 lesson 是在 close-only 模式下跑的，沒有 ATR / 跳空等資訊。**如果重跑 22 警戒日 backfill with PG**，reflection 會更紮實（特別是「為什麼當時 Trader 沒注意到 ATR 高」這種反思）。

但這要再花 ~3 hr backfill，是後續決定。

## 已達成 + 未達成

### 已達成（程度 1 + 程度 2）

- [x] Docker Desktop 4.39 啟動（繞過 4.40+ Inference Manager bug）
- [x] `market.prices` seed 完整 1976 檔 × 16 年 OHLCV
- [x] `pg_adapter.py` 8 個 read API + 整合測試 18/18 PASS
- [x] OHLCV indicator helpers (ATR/gap/volume/candle pattern) + 9 tests PASS
- [x] `price_features` OHLCV path with close-only fallback
- [x] `_format_price` 顯示新指標
- [x] `ta_deepdive` 接 `pg_adapter` + `--no-pg` flag
- [x] 程度 2：`market.chip_ocr` schema + `export_chip_ocr_to_pg.py` 同步 1183 records
- [x] PoC: Market Analyst + Risk Manager 真的使用 OHLCV 指標

### 未做（後續決定）

- [ ] Seed 其他 7 張 PG 表（`institutional / margin / lending / holders / valuation / monthly_revenue / financials`） — 跑 `台股開發2/scripts/backfill_chips_*.py` + `daily_update.py`
- [ ] 重跑 22 警戒日 lesson loop backfill with PG（拿到 OHLCV-aware lessons）
- [ ] Lesson loop 之前 ta-lite-critic 發現的 P1-P5 prompt 改進（chip analyst 強市對照、Risk Manager REBUTTAL 拆逃生門等）
- [ ] daily-fetch 自動接 export_chip_ocr_to_pg.py（Task 9 是 optional）

## 結論

**OHLCV 整合是質的提升**，Market Analyst 從「看價漲跌」進化到「看波動率 + 量價結構」，Risk Manager 能用 ATR 算具體停損。這是 PG 整合的核心價值。

下一個有 leverage 的工作是 lesson loop 重跑（拿到 OHLCV-aware reflection），但要 ~3 hr。短期最值得做的反而是 fix `_gap_count` threshold（5 分鐘工）。
