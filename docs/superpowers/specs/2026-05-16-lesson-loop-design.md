# TradingAgents-lite Lesson Loop (C 級閉環) 設計

**日期**：2026-05-16
**狀態**：design
**作者**：david + Claude
**前置**：commit `1532a4e`（ta-lite + MACD）、`dc937af`（custom agents）
**Spec 上一篇**：`docs/superpowers/specs/2026-05-14-ta-lite-design.md`

## 背景

TradingAgents-lite PoC 已 ship（每日對某警戒日跑 6 agent 深度報告）。報告產出後**沒有 outcome 計算與反思機制**，整套等於 stateless：同一檔股票分析 10 次跟 1 次品質一樣，agent 不會「學到」過去判斷失誤的 pattern。

本 spec 增建「lesson loop」閉環：

1. 算出 ta_report 的 T+5/10/20 實際 excess return
2. LLM 對 Trader 的 ACTION/CONVICTION vs 實際 outcome 寫反思
3. 反思結果存進 lesson store
4. 下次 deepdive 時撈出**語意相似**的歷史 lesson 塞進 prompt
5. 嚴格 walk-forward：lesson 帶日期戳，retrieve 只看 `lesson.date < d`

## 目標

- 對 `ta_reports/<date>/<symbol>.md` 自動算 T+N excess（重用 `verify_predictions.py` 計價邏輯）
- 對每個 outcome 用 LLM 反思一次，產生 lesson 寫進 `data/ta_lessons.jsonl`
- 提供兩個 retriever 後端可切換：A) sentence-transformers embedding；B) `claude -p` LLM-as-retriever
- 提供 backfill CLI，可指定日期區間批次跑 deepdive + reflect，checkpoint 中斷可 resume
- 全程 walk-forward 不洩漏：lesson_date < d

## 非目標

- 不取代既有 `ta_deepdive.py` 主流程，新增 flag 切換是否帶 lesson
- 不接 daily-fetch 自動觸發（PoC 階段全手動）
- 不做 reflection 的 reflection（meta-cognition）
- 不做 lesson 自動 forget / dedup（PoC 階段 append-only）
- 不整合 dashboard 顯示 lesson
- 不裝任何 LangChain / mem0 / Letta（重用既有 `call_llm` subprocess 模式）

## 架構

### 檔案結構

```
agents/
  ta_outcome.py        新 — 算 T+5/10/20 excess return,verdict 分類
  ta_reflect.py        新 — Trader-only 反思,LLM 產 lesson 寫 store
  ta_lesson_store.py   新 — JSONL 持久層 + walk-forward 查詢
  ta_retriever.py      新 — Protocol + EmbeddingRetriever / ClaudeRetriever / CompareRetriever
  ta_features.py       改 — collect() 多回傳 lessons field (預設 None)
  ta_prompts.py        改 — _header() 多「過去判斷紀錄」section (lessons 非空時)
  ta_deepdive.py       改 — CLI 加 --retriever / --skip-lessons / --top-lessons flags
  ta_backfill.py       新 — CLI 做歷史回填,可指定日期區間
data/
  ta_outcomes/<date>/<symbol>.json     每筆 report 的 T+N excess + verdict
  ta_lessons.jsonl                      append-only,所有 lesson 一個檔
  ta_lessons_embed.npy                  (僅 A 使用) embedding 快取,跟 jsonl 列數一致
  ta_backfill_checkpoint.json           回填進度 (last completed date)
  retriever_compare.jsonl               (僅 compare 模式) A vs B 選擇差異 log
```

### Agent 分工不變

新增的閉環不改動 6 agent stage 編排。改動點：

- Prompt 多塞「過去判斷紀錄」section（Stage 1+2+3+4 都看得到，但**主要影響 Chip Analyst + Trader**）
- Trader 的 ACTION 是 outcome verdict 的判定基準

## 資料流

### Forward run（你日常使用）

```
python agents/ta_deepdive.py 2026-05-20 --retriever claude
  │
  ▼
collect features at d=2026-05-20
  │
  ▼
lesson_store.query_candidates(before="2026-05-20")
  │ 回所有 lesson.date < 2026-05-20 的 lesson
  ▼
retriever.retrieve(query=situation, candidates=above, k=5)
  │ A 用 cosine sim、B 用 claude rank
  ▼
features.lessons = top-5 lesson dicts
  │
  ▼
run_pipeline → write_report (Trader 看到歷史教訓)
```

### Outcome 計算（每次跑都自動 catch up）

```
python agents/ta_outcome.py
  │
  ▼
掃 data/ta_reports/<*>/<*>.md 對應的 summary.json entries
  │
  ▼
對每筆 entry:
  - 若已有 data/ta_outcomes/<date>/<symbol>.json → skip
  - 否則檢查 T+max_h 在 stock_prices.dates 範圍內否
  - 若是,計算 T+5/10/20 excess + verdict,寫 outcome json
```

### Reflection (手動或排程)

```
python agents/ta_reflect.py
  │
  ▼
掃 data/ta_outcomes/<*>/<*>.json
  │
  ▼
對每筆 outcome:
  - 從 ta_lessons.jsonl 查該 (date, symbol) 是否已有 lesson → 若是 skip
  - 否則用 LLM 反思: 拼接 (Trader RATIONALE + outcome verdict + 實際 excess) → 產 lesson
  - 寫 lesson 帶 lesson.date = report 的 date (不是反思的當天)
  - 若 retriever=embedding, 同時計算並 append 進 ta_lessons_embed.npy
```

### Backfill (一次性大量跑)

```
python agents/ta_backfill.py --from 2026-02-01 --to 2026-05-04 --alert-only
  │
  ▼
讀 data/all_data_merged.json 篩 date in range AND rate>=170 (若 --alert-only)
  │
  ▼
讀 data/ta_backfill_checkpoint.json 拿 last_completed_date,跳過已做的
  │
  ▼
for d in remaining_dates:
  1. ta_deepdive(d) - 寫 report  
     (其 collect 內 lesson_store query 過濾掉 lesson.date >= d)
  2. ta_outcome 算 d 那筆 report 的 T+N excess
  3. ta_reflect 對 d 那筆 outcome 反思,寫 lesson (date=d)
  4. checkpoint d 寫 checkpoint json
  
  若中途中斷,下次 resume 從 checkpoint 之後繼續
```

### Walk-forward 不變量

```
LessonStore:
  query_candidates(before="2026-05-20")
    → 只回 lesson.date < "2026-05-20" 的 lesson
    (不依賴 retriever 實作正確性,store 層守好就好)

Retriever:
  retrieve(query, candidates, k)
    → 從 candidates (已 date-filter) 中選 top-k
    (不負責 date 過濾)
```

backfill 跑到 d=2026-03-15 時：
- d=2026-03-10 已寫的 lesson → 看得到
- d=2026-03-15 同一輪剛寫的 lesson → **看不到**（store query 用 strict `<`）

## Retriever 設計

### EmbeddingRetriever (A)

```python
class EmbeddingRetriever:
    def __init__(self, model_name="paraphrase-multilingual-MiniLM-L12-v2"):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)  # lazy load
    
    def embed(self, text: str) -> np.ndarray: ...
    
    def retrieve(self, query, candidates, k):
        # query 跟每個 candidate.lesson 算 cosine sim
        # 回 top-k
        # candidates 的 embedding 從 ta_lessons_embed.npy 撈
```

依賴：`sentence-transformers` (拉 torch + transformers, ~1.5GB)。首次安裝由 `ta_retriever.py` import 時觸發 ImportError → 主程式 catch 後 print 安裝指令並 fallback 到 ClaudeRetriever。

### ClaudeRetriever (B)

```python
class ClaudeRetriever:
    def retrieve(self, query, candidates, k):
        prompt = f"""
        當前情境: {query}
        
        過去 lesson 候選 (從 1 編號):
        {format_candidates_numbered(candidates)}
        
        從上述挑 {k} 個跟當前情境最相關的 lesson,只回 JSON:
        {{"selected": [3, 7, 15]}}  ← 1-based indices
        """
        raw = call_llm(prompt)
        indices = parse_json(raw)["selected"]
        return [candidates[i-1] for i in indices if 1 <= i <= len(candidates)]
```

零新依賴,重用 `agents/predict.py:call_llm`。

### CompareRetriever (debug/PoC use)

```python
class CompareRetriever:
    def __init__(self, primary="claude"):
        self.a = EmbeddingRetriever()  # 若 fail 設 None
        self.b = ClaudeRetriever()
        self._primary = primary
    
    def retrieve(self, query, candidates, k):
        result_a = self.a.retrieve(query, candidates, k) if self.a else []
        result_b = self.b.retrieve(query, candidates, k)
        # log 兩邊到 retriever_compare.jsonl
        append_log({
            "query": query,
            "candidate_count": len(candidates),
            "a_picked": [c["id"] for c in result_a],
            "b_picked": [c["id"] for c in result_b],
            "overlap": len(set(a_ids) & set(b_ids)),
        })
        return result_a if self._primary == "embedding" else result_b
```

## Lesson Schema

```json
{
  "id": "2026-05-04_2330",                       // unique = date_symbol
  "date": "2026-05-04",                          // report 的 date (walk-forward 用這個)
  "symbol": "2330",
  "ticker": "2330.TW",
  "outcome": {
    "trader_action": "hold",
    "trader_conviction": 0.45,
    "trader_rationale_excerpt": "綜合四份報告後...",  // 取前 200 字
    "actual_excess_t5": 0.012,
    "actual_excess_t10": 0.082,
    "actual_excess_t20": 0.045,
    "verdict": "missed_long"                     // 6 種其一
  },
  "reflection": "我那天說 hold 是因為 chip 三榜皆空...實際漲 8.2%,錯在我把缺席解讀為偏空。下次應該...",
  "tags": ["chip_silent", "tech_strong", "alert_day"],   // LLM 反思時順手標
  "reflected_at": "2026-05-25T14:23:11"          // 反思的時間 (不是 date)
}
```

## Verdict 邏輯

主要看 T+10 excess return（PRIMARY_HORIZON=20 太遲，10 較靈敏）：

```python
def verdict(action: str, excess_t10: float) -> str:
    if action == "buy":
        return "right_direction" if excess_t10 > 0.01 else "wrong_direction"
    if action == "sell":
        return "right_direction" if excess_t10 < -0.01 else "wrong_direction"
    # hold:
    if abs(excess_t10) < 0.03:
        return "right_hold"
    if excess_t10 > 0.05:
        return "missed_long"
    if excess_t10 < -0.05:
        return "avoided_loss"
    return "wrong_direction"
```

T+5 / T+20 仍記在 outcome.json 供日後分析。

## Reflection Prompt（核心設計）

```
[SYSTEM]
你是台股分析師團隊的「教練」。針對單一交易決策的事後 outcome,
寫一段反思,幫助 Trader 下次避免重蹈覆轍。

[USER]
=== 決策回顧 ===
日期: {report.date}  標的: {symbol}
Trader 那天: ACTION={action} CONVICTION={conviction} HORIZON={horizon}
RATIONALE 摘要: {rationale_first_200chars}

=== 實際結果 ===
T+5 excess return: {t5:+.2f}%
T+10 excess return: {t10:+.2f}%
T+20 excess return: {t20:+.2f}%
verdict: {verdict}

=== 同日的其他 agent 報告摘要 ===
[Market]: {market[:100]}...
[Chip]: {chip[:100]}...
[Bull]: {bull[:100]}...
[Bear]: {bear[:100]}...

=== 你的任務 ===
1. 用繁體中文 200-300 字寫一段 reflection,具體說明:
   - Trader 的判斷邏輯哪裡對 / 哪裡錯
   - 是哪個上游 agent (Market/Chip/Bull/Bear) 的論述帶歪了 Trader
   - 下次遇到「類似情境」應該注意什麼

2. 從以下 tag 池挑 3-5 個最貼切的:
   - chip_silent, chip_active, chip_alert_concentrated
   - tech_strong, tech_weak, tech_high_volatility, tech_overbought, tech_oversold
   - alert_day, normal_day, alert_persistent
   - rate_high (>175), rate_borderline (170-175)
   - bull_outperformed, bear_outperformed, bull_bear_balanced

輸出嚴格 JSON:
{
  "reflection": "...",
  "tags": ["chip_silent", "tech_strong", "alert_day"]
}
```

## 錯誤處理

| 失敗點 | 行為 |
|---|---|
| Embedding 模型載入失敗 (未裝套件) | 主程式 catch ImportError，print pip install 指令，fallback to ClaudeRetriever |
| Outcome T+max_h 還沒到 (新 report) | 跳過該 report，下次 ta_outcome 再來 |
| Reflection LLM call 失敗 | 寫 `{"reflect_failed": true, "reason": "..."}` 到 lesson，下次 retry |
| Reflection JSON parse 失敗 | retry 一次（含「請只輸出 JSON」追加 prompt），仍失敗則寫 failed marker |
| Backfill 中途 crash | checkpoint json 已存 last_completed_date，重跑會 resume |
| Backfill 同一 d 重跑 | 偵測既有 report/outcome/lesson 都存在 → skip 該 d（除非 `--force`） |
| Lesson 反思時參考的 ta_report 已被刪 | skip 該 outcome，print 警告 |
| Compare 模式時 A 載入失敗 | 只跑 B，retriever_compare.jsonl 記錄 a_picked=null |

## 測試策略

- **`test_ta_lesson_store.py`** (~5 tests)
  - walk-forward 不變量：`query_candidates(before=d)` 不回 date >= d 的 lesson
  - append/load roundtrip
  - duplicate id detection
- **`test_ta_outcome.py`** (~6 tests)
  - 6 種 verdict case 各 1 test (right_direction buy/sell、right_hold、missed_long、avoided_loss、wrong_direction)
  - T+max_h 不在 prices 範圍時回 None
  - idempotent 重跑不重寫
- **`test_ta_retriever.py`** (~8 tests)
  - EmbeddingRetriever: stub 模型，驗 cosine 排序對
  - ClaudeRetriever: stub `call_llm`，驗 JSON parse 與 indices 邊界
  - CompareRetriever: 兩邊都 stub，驗 log entry 內容、primary 切換
  - 各 retriever 對空 candidates 的 graceful 行為
- **`test_ta_reflect.py`** (~4 tests)
  - stub LLM，驗 prompt 內容含 outcome 三個 horizon
  - JSON 輸出寫進 store
  - reflect_failed marker 寫入 + retry 行為
- **`test_ta_backfill.py`** (~3 tests)
  - dry run mode 列出會跑哪些日期
  - resume from checkpoint
  - --force 蓋掉既有 lesson

## PoC 驗證計畫

**Phase 1（50 天試水）**：

```bash
# 假設今天 2026-05-16,跑最近 50 個警戒日 (約 2026-02 到 2026-05-04)
python agents/ta_backfill.py --from 2026-02-01 --to 2026-05-04 \
    --alert-only --retriever compare --primary claude
```

預估時間：50 天 × (6 股 × 6 agents 跑 deepdive + 6 outcome + 6 reflect) = 50 × ~12 分鐘 = ~10 小時連續跑（需要 checkpoint 中途休息）

完成後評估：
1. `data/ta_lessons.jsonl` 應有 ~300 筆 lesson
2. `data/retriever_compare.jsonl` 看 A vs B 選擇 overlap %
3. 用 `ta-lite-critic` agent 抽 5 個 lesson 評反思品質
4. 跑一檔 deepdive `--retriever embedding` 跟同檔 `--retriever claude` 比 prompt 差異

通過標準（軟性）：
- 反思品質達標（不是空話、確實點出 agent 錯誤）
- A 跟 B 重疊率 > 40%（兩個都有意義）
- 「有 lessons」的 report 比「沒 lessons」report 多至少 1 條具體歷史引用

**Phase 2（擴展到 105 警戒日）**：通過 Phase 1 再做。

**Phase 3（全 1183 天）**：通過 Phase 2 + 確認 subscription 用量可接受，再做。

## 開放議題

無已知開放議題。Subscription rate limit 是潛在風險，會在 backfill 期間觀察並 checkpoint 機制處理。

## 不做（YAGNI）

- ❌ 不做 reflection 的 reflection
- ❌ 不做 lesson 自動 deprecation / forgetting
- ❌ 不接 daily-fetch
- ❌ 不整合 dashboard
- ❌ 非警戒日 backfill (除非 `--all-days`)
- ❌ 不引入 langchain / mem0 / letta
- ❌ 不做 lesson 跨股票主動推薦（純被動 retrieval）
