# TradingAgents-lite PoC 評估報告

**日期**：2026-05-14
**Spec**：`docs/superpowers/specs/2026-05-14-ta-lite-design.md`
**Plan**：`docs/superpowers/plans/2026-05-14-ta-lite.md`
**結論先寫**：✅ 可繼續推進。Pipeline 跑通，輸出品質遠優於預期，但**chip 資料未在這 2 檔 PoC 中發揮作用**——需要再選一檔 chip 訊號活躍的標的做第二輪 PoC 才能完整驗證籌碼面 agent。

## 跑了什麼

- **日期**：2026-05-04（rate=181 警戒日，最新一筆資料）
- **Symbols**：`2330`（台積電，當日 top5）+ `2303`（聯電，當日 bull）
- **Model**：sonnet（即 Claude Sonnet 4.6，走 Claude Code 訂閱）
- **耗時**：12 次 LLM call 共 ~3.5 分鐘（~18 秒/call，含 subprocess `claude -p` 啟動）
- **結果**：兩檔皆 `status=ok`（6/6 agent 全成功），無 timeout、無 subscription rate limit 踩到
- **輸出**：`data/ta_reports/2026-05-04/{2330,2303}.md` + `summary.json`

## 品質評估

### 加分項

1. **資料引用精確**：每個 agent 都引用具體數字而非空話
   - 「累積報酬 +15.09% vs TWII +10.55%」← 來自 `price_features`
   - 「歷史 long 勝率 71%（21 次中勝 15 次）」← 來自 `past_perf`
   - 「MA5 74.82 / MA20 67.71，乖離超過 10%」← 來自 `price_features`

2. **多空辯論真的有對話**：不是 paraphrase，是各自引用同一份資料得出相反結論
   - 2303 多方：「MA5 對 MA20 乖離超過 10% 代表中期上升趨勢已確立」
   - 2303 空方：「MA5 對 MA20 乖離已達 10% 以上，短線嚴重超買」
   - → 同一份技術指標被解讀為「強勢延續」vs「超買回測」，論點都紮根於資料

3. **Trader 綜合判斷有層次**：
   - 2330：ACTION=hold, CONVICTION=0.45（略偏空但等訊號）
   - 2303：ACTION=hold, CONVICTION=0.55（技術強但缺籌碼背書）
   - 兩檔都選 hold，但理由不同——這是合理的「資料矛盾時選保守」

4. **Risk Manager 給出可執行細節**：MAX_POSITION_PCT、三大風險點、對 Trader 的 REBUTTAL（同意/不同意+理由）

5. **walk-forward 嚴謹**：報告中所有日期、價格、TWII 都在 `< 2026-05-04`，不洩漏未來

### 扣分項

1. **chip data 未發揮作用** ⚠️ 這是 PoC 最大缺憾
   - 2330 在 < 2026-05-04 的近 60 日 **chip 三榜（bull/bear/top5）皆空**
   - 2303 也是三榜皆空
   - 籌碼分析師被迫只能說「訊號缺席，無法判定多空」——這個 insight 是對的，但 chip layer 本來該提供的「該股近期 N 次出現於 X 榜」資訊沒用上

2. **TWII 缺漏的 6 天會導致 anchor miss**：但 PoC 跑的 2026-05-04 走的是 `< d`，window_end = `2026-05-03` 之類有 TWII 的日期，沒踩到。理論上若某 PoC 日期的 `window_end` 落在 2026-04-24/04-27/04-28/04-29/04-30/05-04 這幾天（已知 TWII 缺漏），`twii_return_window` 會回 `None`，prompt 顯示 "(TWII anchor 缺，無相對表現可算)"。這條 fallback 在 Task 5 已實作。

3. **chip_features 提示包含 hardcoded 60 日**：若調整 window 參數，prompt 文字會說謊（Task 4 code review 已 flag）。PoC 還不需要改 window，先擱置。

## 與 predict.py 的方向一致性

- `2330` AI 預測歷史：long 21 次/勝 15 次（71%）、short 9 次/勝 7 次（78%）
- `2303` AI 預測歷史：long 23 次/勝 20 次（87%）、short 25 次/勝 16 次（64%）
- TradingAgents-lite 給兩檔都 hold/低 conviction，與 predict.py 沒有衝突

無法直接驗證「方向一致率」因為 `ai_predictions.jsonl` 目前只回填到 2021-10-19，2026-05-04 沒有 predict.py 的對應預測。

## 後續步驟（優先順序）

1. ⭐ **再跑一輪 PoC，挑 chip 訊號活躍的標的**：例如近 60 日多次出現於 bull/top5 的個股，確認籌碼分析師能挖出真實 pattern 而非只說「訊號缺席」。
   - 建議：找 `top5_count >= 5` 的 symbol（從 `all_data_merged.json` 反查）
2. **subscription 用量小心評估再排進 daily-fetch**：3.5 分鐘 / 2 檔 → 預設模式 6 檔 × ~10 分鐘 → daily-fetch 從現在的 ~3 分鐘變成 ~13 分鐘，且每天打 36 次 `claude -p`。先繼續手動觸發。
3. **若決定整合 dashboard**：dashboard 已可讀 `data/ta_reports/<d>/summary.json` 拉 entry 列表。但 PoC 階段先保持「報告檔給人讀」的 markdown 形式，不急著做 UI。
4. **AI prediction backfill 推進到近期**：predict.py 還停在 2021-10-19，要回填到 2026 才能讓 TradingAgents-lite 的 default 模式（從 predict.py picks 挑 top-N）能用。

## 是否值得繼續

**是。** 投資報酬比合理：用 ~600 行新程式（features + prompts + runner + CLI + tests）換到「警戒日可隨時手動跑 6 檔深度報告」的能力，且不增加新 dependency（不引入 langchain/langgraph、不花 token 費）。

但**還不要排進 daily-fetch**——chip-side validation 還沒做完，subscription rate limit 風險未知。
