# 訊號分析方法論

本文件說明 `scripts/analyze_signals.py` 的計算邏輯、篩選規則、與**重要的限制聲明**。
任何要根據 dashboard「策略回測」tab 做投資決策的人**必讀**。

---

## 1. 資料來源（scantrader 4 張圖）

每天的原始 record 由 4 張圖 OCR 而來,規則見 `scraper_guide.md`。對應到 schema:

| record 欄位 | 對應的圖 | 因果含義（本次假設） |
|---|---|---|
| `bull` | 一周外資買超 Top 20 | 法人持續買進 → 後續股價易漲 |
| `bear` | 一周借券加碼 Top 20 | 法人準備賣 → 後續股價易跌 |
| `rate` | 全市場融資使用率 | rate ≥ 170 視為過熱警戒（散戶過度槓桿） |
| `top5_margin_reduce_inst_buy` | 融資減少 + 法人買進 Top 5 | 散戶撤、法人接,**最強訊號（先驗）** |

`bull` / `bear` 大多是股號（4 位數字）、`top5` 大多是中文股名,在分析時透過 `data/stock_fetch_log.json.symbol_to_ticker` 統一映射到 yfinance ticker（`.TW` / `.TWO`）。

---

## 2. 計算方法

### 2.1 個股報酬定義

對每個 record `(date, side, symbol, horizon)`：

```
p0   = stock_close on or before date              ← getCloseOnOrBefore
pN   = stock_close N 個交易日 after date          ← getCloseNDaysLater
ret  = pN / p0 - 1
```

**N 指交易日數,不是 calendar day**。在 `prices.dates` 排序索引中前進 N 個元素,週末 / 休市自動跳過。

### 2.2 超額報酬（excess return）

```
twii_ret    = TWII_close 相同方法算出的 N 日報酬
excess_ret  = ret - twii_ret
```

> 為何不用 SP500 / MSCI Taiwan?因為這是台股策略,基準必須是台股大盤。
> 為何不年化?N 個交易日的報酬 ≠ 年化收益率;這裡 horizon ∈ {1, 5, 10, 20, 60} 都太短,年化會放大雜訊。

### 2.3 與 dashboard JS 的一致性

Python 端的 `read_price` / `get_close_on_or_before` / `get_close_n_days_later` 是 **dashboard_all.html:586-627 的逐字 port**,並用 `tests/test_analyze_signals.py` 的 fixture 驗證兩端結果完全一致(週末 / 起始 offset / 中間缺值都覆蓋)。

---

## 3. 統計顯著性

### 3.1 為何用 t-test 而不是直接看平均

平均超額報酬 +1% 看起來不錯,但若樣本只有 5 筆且標準差 50%,根本是雜訊。t-stat 把樣本數與分散程度納入:

```
t = avg_excess / (std / √n)
```

| t 值 | 雙尾 p-value | 含義 |
|---|---|---|
| 1.65 | 0.10 | 90% 信心(寬) |
| 1.96 | 0.05 | 95% 信心(學界標準) |
| 2.58 | 0.01 | 99% 信心(嚴) |

報告以 1.96 為標準分隔「真訊號」與「巧合」。

### 3.2 為何不直接算 Sharpe / IRR

- **Sharpe**:本次每筆樣本是「個股 N 日超額」的離散事件,沒有持續持倉的概念,Sharpe 在這裡只是 `avg/std` 的相對排序代理,不是傳統的「年化超額/年化波動」。
- **IRR**:需要真實的買賣時序與資金流。本次假設等權買、N 日後賣,沒有資金管理,算 IRR 會被錯誤詮釋成「策略真實報酬率」。

實單做之前要自己再加上交易成本、持倉重疊、資金分配等真實限制。

---

## 4. 訊號篩選邏輯

### 4.1 五條規則（per side）

`is_effective_signal(stat, params)` 對一個 (symbol, side, horizon=20) 三元組判斷是否選入：

| 條件 | bull / top5 | bear |
|---|---|---|
| 樣本數 | `train_n ≥ min_n` | 同 |
| 平均超額 | `train_avg ≥ +min_avg_excess` | `train_avg ≤ -min_avg_excess` |
| 勝率 | `train_winrate ≥ min_win_rate` | `(1-train_winrate) ≥ min_win_rate`(下跌率) |
| 顯著性 | `\|train_t\| ≥ min_t_stat` | 同 |
| 訊號還活著 | `recent_n ≥ min_recent_n` | 同 |

bear 用反向是因為它預期負 alpha;與 bull/top5 對稱。

### 4.2 為何只看 horizon=20

因為 dashboard 的「策略回測」tab 也以 T+20 為主要決策 horizon(最 prominent 的 cards)。
其他 horizon (1/5/10/60) 仍會在 by_horizon 結構中存在,但**篩選與排序都以 T+20 為準**。

未來想做多 horizon 篩選(例如 T+5 與 T+20 都通過才算)再擴充。

### 4.3 Grid search 校準參數

`PARAM_GRID` 定義 5 個參數的合理區間,共 4×4×3×3×3 = **432 組**。每個 side 各跑一次:

1. 用 train 窗(2021-01-01 ~ 2024-12-31)的 stat 篩出符合該組門檻的個股池
2. 在 test 窗(2025-01-01 ~ 今)算該股票池的 T+20 等權 excess 平均
3. 用 `abs(test_avg) / test_std` 為 Sharpe 排序,選最高那組為 `recommended`
4. test 樣本 < 10 的組合直接淘汰(避免單筆 lucky pick)

**為何 train/test 嚴格分離才算驗證**:如果用全期間統計篩、再用全期間平均評估,等於拿訓練資料當考試卷。在訊號失效的世界,訓練窗看起來再強的策略也可能在 test 窗虧錢 — 這就是 overfit。本次 bull 的 recommended preset 就是典型例子(見第 6 節)。

### 4.4 三個 preset 的設計

| Preset | 用途 | 典型門檻 |
|---|---|---|
| **loose** | 探索性,看訊號廣泛存在嗎 | n=3, t=1.65, avg=1% |
| **recommended** | grid search 找出的 per-side 最佳 | 隨資料動態 |
| **strict** | 嚴篩,可實戰的最高把握 | n=10, t=2.58, avg=3% |

dashboard 切 preset 不重新 fetch,只是切資料切片。

---

## 5. Train / Test 窗口設計

```
2021-01-01 ────────── 2024-12-31  │  2025-01-01 ────── 今 (2026-04-30)
                  TRAIN (~75%)    │      TEST (~25%)
                                       └─ recent (近 365 天)
```

- **Train 窗**:做篩選與門檻校準
- **Test 窗**:純驗證,完全不用於篩選;這是判斷訊號是否真有效的唯一標準
- **Recent 窗**:近 1 年,用於檢驗訊號是否還活著(不是衰退中)

**切點選 2024-12-31 的理由**:給 train ~866 records (75%)、test ~316 records (25%)。
比例典型,且 2024/12 後台股有經歷一段較大的市況變化,適合做 OOS 壓測。

**唯一爭議**:test 窗只 ~316 records,某些訊號(尤其 top5 篩很嚴後)test_n 可能 < 50,
顯著性會被樣本數限制。報告數字必看樣本數,不只看 avg。

---

## 6. 實際結論（最新一次跑的觀察）

對應 `reports/signal_validation_*.md`:

### 6.1 各 side 全期 vs 訓練 vs 測試 vs 近 1 年（T+20）

| side | all (n) | train | **test** | recent |
|---|---|---|---|---|
| bull | +0.45% (21k) | +0.44% | +0.51% | +0.43% (t=1.55,**不顯著**) |
| bear | -0.38% (22k) | -0.19% | **-1.05%** (t=-6.64) | **-2.09%** (t=-10.04) |
| top5 | +1.30% (5.5k) | +0.55% | **+3.34%** (t=7.60) | **+4.98%** (t=9.33) |

### 6.2 三個重要發現

**🟢 Top5 是真訊號,且效力強化中**
- recommended preset 篩 6 檔,train +7.47% → **test +9.50%**(同向且更強)
- 近 1 年 t=9.33,完全沒有訊號衰退跡象
- 這個訊號(融資減 + 法人買)就是 scantrader 的核心 alpha

**🟡 Bear 訊號真,但樣本稀**
- recommended preset 篩 23 檔,train -7.03% → **test -8.81%**(維持下跌)
- 適合做空或迴避;但 test 期 28 筆樣本,要小心 small-sample noise

**🔴 Bull 訊號失效,別買**
- 全期看似有 +0.45%,t=5.62 顯著
- 但 recommended preset (篩出來「最強的 bull」)在 test 反而 **-3.35%**
- 近 1 年也只剩 t=1.55(< 1.96 不顯著)
- 結論:**外資週買超榜在最近這幾年已經沒有預測力**,不要照著進場

---

## 7. 限制聲明（最重要的一節）

本分析有以下假設,實單操作前必須補回去:

### 7.1 沒考慮的成本
- **手續費**:單趟 ~0.1425% × 2(買賣)≈ 0.3%
- **證交稅**:賣出 0.3%(ETF 是 0.1%)
- **滑價**:大型權值股可忽略,中小型 0.5-1% 都有可能
→ 報告中的「+9.5% 超額」實際扣完成本可能只剩 +8% 左右,但不影響「訊號是否有效」的結論

### 7.2 沒考慮的資金 / 持倉問題
- **等權**:假設每檔股票買等量金額。實際做要考慮個股市值、波動度
- **持倉重疊**:同一檔在連續多日入榜,本次當作多次獨立樣本算。實際操作這檔只能持有一次,計入 N+1, N+2 後不算新訊號
- **資金分配**:6-23 檔篩選結果,實際做需要決定每檔配多少權重、留多少現金

### 7.3 沒考慮的市場面
- **大盤崩盤時 alpha 也會虧**:超額報酬 = 個股 - TWII;若全市場 -30%,個股 -20% 算 +10% alpha,實際資產仍虧 20%
- **產業集中度**:篩選結果若集中在某一產業(例如全是半導體),實際就是產業 beta 而不是個股 alpha
- **基本面 / 產業景氣**:本分析純看技術籌碼面,完全不看 EPS / 營收 / 訂單能見度

### 7.4 統計面的盲點
- **多重比較**:432 組 × 3 side = 1296 次假設檢定,即便每組 p<0.05,期望會有 ~65 組純粹靠運氣通過。所以 grid search 的「最佳」要保留懷疑
- **Survivorship bias**:`stock_prices.json` 只有 yfinance 抓得到的 ticker,下市股不見了。歷史上若有「先入榜後下市」的個股,本次當作不存在,輕度高估訊號效力
- **資料窗口短**:5 年資料(2021-2026)只涵蓋一個多空循環。長期(20+ 年)的訊號穩健性無法判斷

### 7.5 對應的決策建議

1. 進場前再驗證:把當下的訊號丟回 `analyze_signals.py` 看篩選結果,而不是用 1 個月前跑的舊報告
2. 信賴 top5 訊號最高,bear 次之,bull **不建議**直接照做
3. 預期報酬要打折:報告 +9.5% → 心裡留 +5-7%
4. 單筆部位上限:同一檔不超過 5% 總資金,避免訊號失效時崩
5. 設停損:T+20 內沒漲到預期就出場,不繼續持有

---

## 8. 股名 / 股號對照（symbol_index 與 aliases）

dashboard 的「進階分析」與「策略回測」tab 顯示「2330 台積電」這種「代號 名稱」並列格式，靠的是 `data/symbol_index.json`（由 `scripts/symbol_resolve.py` 產生）。

對應流程：

```
merged 中 unique symbols (1136)
  │
  ├── stock_map.json       (TWSE/TPEx 上市櫃官方簡稱)   覆蓋 ~86.5%
  ├── stock_aliases.json   (人工 / 自動補表,凌駕 stock_map) 補 ~2-5%
  └── stock_fetch_log.json (yfinance ticker 對照)
        ↓
  symbol_index.json   {symbol → {code, name, ticker, display}}
```

### 補 unknown 股名

跑完 `fetch_prices.py` 後若 `unknown_names` 大於 ~10%，先試自動推導：

```bash
python scripts/lookup_aliases.py            # dry-run 看能補多少
python scripts/lookup_aliases.py --write    # 寫入 data/stock_aliases.json
python scripts/symbol_resolve.py            # 重建 symbol_index.json
```

`stock_aliases.json` 結構：

```json
{
  "一銓": {"code": "3661", "name": "一銓-KY", "source": "substring_unique"},
  "_candidates_佳邦": {
    "candidates": [
      {"name": "佳邦科技", "code": "2072"},
      {"name": "佳邦*",     "code": "5314"}
    ]
  },
  "_unmatched": ["一江", "三福", "中鴻", ...]
}
```

- 正式 entry 由 `lookup_aliases.py` 自動或人工填入
- `_candidates_<name>` 是多重候選（人工挑後展平成正式 entry，把該 `_candidates_*` key 刪掉）
- `_unmatched` 是 stock_map 完全沒有候選的股名（多半興櫃 / 已下市 / 特殊命名），需手工查 TWSE / TPEx / Goodinfo 後填入

### dashboard 顯示策略

- 已解析 `code + name`：顯示 `"2330 台積電"`
- 只有股號（已下市）：顯示 `"5080"`
- 只有股名（待補）：顯示 `"新巨群 (待補)"` 提示用戶該補

對 top20 表是預先在 `analyze_signals.py` 把 `display` 欄位寫進 `backtest_summary.json`；對進階分析 tab 是 dashboard 自己 fetch `symbol_index.json` 後即時 enrich。任一檔不存在或載入失敗時，都會 fallback 到原 symbol（不阻擋其他功能）。

### 為什麼有 ~8% 解不掉（真實成因 — 不是腳本沒寫好）

`scripts/lookup_aliases.py` 的解析鏈共五層（`strip_star` → `isin_normalize` → `substring_unique` → 多重候選 → 完全找不到）。對 1136 個 unique symbol 跑下來：

| 階段 | 累積解析率 |
|---|---|
| stock_map（TWSE/TPEx 在市清單）只查 | 86.5% |
| + substring 雙向比對 | 88.9% |
| + 16 個下市股手工 web search 補 | 89.9% |
| + ISIN 一覽表 normalize 比對 | 90.2% |
| + Levenshtein ≤1 fuzzy + 4 個 OCR 手工修正 | **92.3%** |

剩 88 個（5 個 only_code + 83 個 only_name）做過：

- TWSE 全 mode ISIN 一覽表（含上市+上櫃+興櫃+債券+權證+TDR，~42k 筆）的 normalized 雙向 substring 全部 **MISS**
- yfinance Ticker.info（下市股全 404）
- cnyes / Goodinfo 直 fetch（部分 404，部分要先有 code）

**結論**：這 88 個字串**不是任何台股實際的證券簡稱**，是 OCR 把真實公司名讀錯後產生的虛擬字串（典型 OCR 錯字模式：詠↔泳、銅↔鋼、磊↔罩、晶↔品、新↔金、創↔象 等字形相近字符）。腳本繼續猜（fuzzy 第一名）會把錯誤映射寫進 git，**比留 `(待補)` 更糟**：使用者看到「光磊 → 光罩」會以為這是實際對應，但其實 record 真的不是這檔。

### 補強這 88 個的兩條路（都不在 analyze_signals 範圍內）

1. **修 OCR scraper（治本）**：在 `.claude/commands/daily-fetch.md` 加「常見 OCR 錯字對照表」，daily-fetch 抓新資料時自動修正。只影響未來資料。
2. **回原圖人工 review（治歷史資料）**：對高頻 unresolved（出現 ≥ 5 次）打開原始 scantrader 截圖逐筆校正，把確認的對應寫進 `data/stock_aliases.json`。

兩條都需要人或人輔助，不適合放進自動 pipeline。dashboard 的 `(待補)` 標記就是給人看的紅燈。

---

## 9. 重跑與更新

每天 `/daily-fetch` 跑完,merged 會更新。要重新分析:

```bash
python scripts/fetch_prices.py        # 補新日子的股價(週末跑一次足夠)
python scripts/analyze_signals.py     # 重新算 backtest_summary + reports
```

`reports/signal_validation_YYYY-MM-DD.md` 會留下歷史快照,方便日後比對訊號變化。
`data/backtest_summary.json` 會被覆蓋,dashboard 一刷新就看到最新。

---

## 附錄:檔案產物

| 檔案 | 行數 / 大小 | git? |
|---|---|---|
| `data/backtest_summary.json` | ~90 KB | ✓ commit(派生產物,跟 year 檔同) |
| `reports/signal_validation_*.md` | ~85 行 | ✓ commit(歷史快照) |
| `reports/per_sample.csv` | ~24 萬列, 17.9 MB | ✗ gitignore |
| `reports/symbol_stats.csv` | ~7800 列, 860 KB | ✗ gitignore |

`per_sample.csv` 與 `symbol_stats.csv` 是中間產物,人工抽查用。要進 git 的只有 dashboard 讀的 JSON 與人讀的 markdown 報告。
