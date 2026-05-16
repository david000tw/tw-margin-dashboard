# TA-lite Lesson Loop (C 級閉環) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 對 ta_reports 算 T+N excess outcome、LLM 反思 Trader 決策寫 lesson、下次 deepdive 撈語意相似歷史 lesson 塞 prompt。嚴格 walk-forward (lesson.date < d)。

**Architecture:** 6 個新檔(`ta_lesson_store` / `ta_outcome` / `ta_retriever` / `ta_reflect` / `ta_backfill` + 修 `ta_features` / `ta_prompts` / `ta_deepdive`)，pluggable retriever 雙實作(embedding + claude-as-retriever)。`call_llm` 重用既有 `agents/predict.py` subprocess wrapper，零 API key 成本。

**Tech Stack:** Python 3.8+, unittest, `subprocess claude -p`，可選 `sentence-transformers` (A 級 retriever 才需要)。

**Spec:** `docs/superpowers/specs/2026-05-16-lesson-loop-design.md`

---

## File Structure

```
agents/
  ta_lesson_store.py     新 — JSONL 持久層 + walk-forward 查詢
  ta_outcome.py          新 — 算 T+5/10/20 excess + verdict
  ta_retriever.py        新 — Protocol + 3 impls (Embedding / Claude / Compare)
  ta_reflect.py          新 — LLM 反思 + parse + 寫 store
  ta_backfill.py         新 — CLI 批次回填,checkpoint/resume
  ta_features.py         改 — SymbolFeatures 加 lessons 欄位
  ta_prompts.py          改 — _header 加「過去判斷紀錄」section
  ta_deepdive.py         改 — CLI 加 --retriever / --skip-lessons / --top-lessons
tests/
  test_ta_lesson_store.py
  test_ta_outcome.py
  test_ta_retriever.py
  test_ta_reflect.py
  test_ta_backfill.py
  test_ta_features.py    改 — 加 lessons field 測試
  test_ta_runner.py      不動
data/
  ta_outcomes/<date>/<symbol>.json    Task 2 產出
  ta_lessons.jsonl                     Task 6 產出
  ta_lessons_embed.npy                 (僅 embedding 使用) Task 4 產出
  ta_backfill_checkpoint.json          Task 10 產出
  retriever_compare.jsonl              (僅 compare 使用) Task 5 產出
```

實作順序（依依賴關係）：

1. `ta_lesson_store` (foundation, no LLM)
2. `ta_outcome` + report markdown parser helper
3. `ta_retriever` — Protocol + ClaudeRetriever
4. `ta_retriever` — EmbeddingRetriever + fallback
5. `ta_retriever` — CompareRetriever + factory
6. `ta_reflect`
7. `ta_features.collect` 加 lessons field
8. `ta_prompts._header` 顯示 lessons
9. `ta_deepdive` CLI flags + 串接 retriever
10. `ta_backfill` CLI
11. PoC backfill 50 警戒日 + 評估

---

### Task 1: ta_lesson_store

**Files:**
- Create: `agents/ta_lesson_store.py`
- Test: `tests/test_ta_lesson_store.py`

提供 lesson 的 append + walk-forward 查詢。Append-only JSONL，load 時 cache。

- [ ] **Step 1: Write the failing test**

Create `tests/test_ta_lesson_store.py`:

```python
"""
agents/ta_lesson_store.py 的測試。

walk-forward 不變量:query_candidates(before=d) 只回 lesson.date < d。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from ta_lesson_store import LessonStore   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_lesson(date: str, symbol: str, verdict: str = "right_hold") -> dict:
    return {
        "id": f"{date}_{symbol}",
        "date": date,
        "symbol": symbol,
        "ticker": f"{symbol}.TW",
        "outcome": {"verdict": verdict, "trader_action": "hold"},
        "reflection": f"模擬 lesson {date} {symbol}",
        "tags": ["test_tag"],
        "reflected_at": "2026-05-16T10:00:00",
    }


class TestLessonStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "test_lessons.jsonl"

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()
        if self.tmp.parent.exists():
            self.tmp.parent.rmdir()

    def test_append_and_load_roundtrip(self):
        store = LessonStore(path=self.tmp)
        l1 = fx_lesson("2024-01-02", "2330")
        store.append(l1)
        store2 = LessonStore(path=self.tmp)   # 重新 load
        loaded = store2.query_candidates(before="2025-01-01")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "2024-01-02_2330")

    def test_query_strict_before(self):
        store = LessonStore(path=self.tmp)
        store.append(fx_lesson("2024-01-02", "2330"))
        store.append(fx_lesson("2024-01-05", "2454"))
        store.append(fx_lesson("2024-01-10", "1101"))
        result = store.query_candidates(before="2024-01-05")
        ids = {l["id"] for l in result}
        # 01-02 ✓  01-05 排除(== before)  01-10 排除(> before)
        self.assertEqual(ids, {"2024-01-02_2330"})

    def test_exists_check(self):
        store = LessonStore(path=self.tmp)
        store.append(fx_lesson("2024-01-02", "2330"))
        self.assertTrue(store.exists("2024-01-02_2330"))
        self.assertFalse(store.exists("2024-01-02_9999"))

    def test_append_duplicate_id_replaces(self):
        # 同 id 二次 append 應該蓋掉 (idempotent reflect 用)
        store = LessonStore(path=self.tmp)
        store.append(fx_lesson("2024-01-02", "2330", verdict="wrong_direction"))
        store.append(fx_lesson("2024-01-02", "2330", verdict="right_hold"))
        result = store.query_candidates(before="2025-01-01")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["outcome"]["verdict"], "right_hold")

    def test_query_empty_store(self):
        store = LessonStore(path=self.tmp)
        self.assertEqual(store.query_candidates(before="2024-01-01"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

```
cd C:\Users\yen\Desktop\法人日資料
python -m unittest tests.test_ta_lesson_store -v
```

Expected: ImportError (ta_lesson_store not defined).

- [ ] **Step 3: Implement ta_lesson_store**

Create `agents/ta_lesson_store.py`:

```python
"""
TradingAgents-lite lesson store。

JSONL append-only,load 進 memory cache。query_candidates() 嚴格
walk-forward(lesson.date < before)。同 id 二次 append 取代(idempotent
給 ta_reflect 重跑用)。

落地檔: data/ta_lessons.jsonl
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
LESSONS_JSONL = DATA / "ta_lessons.jsonl"


class LessonStore:
    """Append-only lesson 持久層,walk-forward 查詢。"""

    def __init__(self, path: Path = LESSONS_JSONL):
        self._path = path
        self._cache: dict[str, dict] = {}   # id → lesson
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                lesson = json.loads(line)
                self._cache[lesson["id"]] = lesson

    def append(self, lesson: dict) -> None:
        """寫入 lesson(同 id 取代)。atomic-ish:append 一行 + 重寫整檔去重。"""
        self._cache[lesson["id"]] = lesson
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 重寫整檔(dedup by id, 按 date 排序便於人工瀏覽)
        tmp = self._path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for l in sorted(self._cache.values(), key=lambda x: x["date"]):
                f.write(json.dumps(l, ensure_ascii=False) + "\n")
        os.replace(tmp, self._path)

    def query_candidates(self, before: str) -> list[dict]:
        """回 lesson.date < before 的 lesson list,按 date 升序。"""
        return [l for l in self._cache.values() if l["date"] < before]

    def exists(self, lesson_id: str) -> bool:
        return lesson_id in self._cache

    def all(self) -> list[dict]:
        """回所有 lesson(不過濾),按 date 升序。給 backfill 統計用。"""
        return sorted(self._cache.values(), key=lambda x: x["date"])
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_ta_lesson_store -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```
git add agents/ta_lesson_store.py tests/test_ta_lesson_store.py
git commit -m "feat(lesson-loop): LessonStore — JSONL append-only + walk-forward query"
```

---

### Task 2: ta_outcome — verdict + T+N excess

**Files:**
- Create: `agents/ta_outcome.py`
- Test: `tests/test_ta_outcome.py`

對 ta_reports/<date>/summary.json 的 entry，從 stock_prices 算 T+5/10/20 excess return + verdict。重用 `verify_predictions.py` 計價邏輯（不要重寫）。Idempotent 重跑不重寫。

- [ ] **Step 1: Write the failing test**

Create `tests/test_ta_outcome.py`:

```python
"""
agents/ta_outcome.py 測試:verdict 邏輯 + T+N excess 計算。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ta_outcome import verdict, parse_trader_section, compute_outcome   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_prices():
    return {
        "dates": [
            "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
            "2024-01-12", "2024-01-15", "2024-01-16", "2024-01-17",
        ],
        "prices": {
            "2330.TW": {"start": 0, "csv": "600,602,605,610,615,612,618,620,625,622,628,630"},
            "1101.TW": {"start": 0, "csv": "40,40.5,41,41.2,40.8,40,39.8,39.5,39.2,39,38.8,38.5"},
        },
    }


def fx_twii():
    return {
        "2024-01-02": 17000.0, "2024-01-03": 17050.0, "2024-01-04": 17080.0,
        "2024-01-05": 17120.0, "2024-01-08": 17200.0, "2024-01-09": 17150.0,
        "2024-01-10": 17220.0, "2024-01-11": 17260.0, "2024-01-12": 17290.0,
        "2024-01-15": 17320.0, "2024-01-16": 17350.0, "2024-01-17": 17380.0,
    }


class TestVerdict(unittest.TestCase):
    def test_buy_right_direction(self):
        self.assertEqual(verdict("buy", 0.02), "right_direction")

    def test_buy_wrong_direction(self):
        self.assertEqual(verdict("buy", -0.02), "wrong_direction")

    def test_sell_right_direction(self):
        self.assertEqual(verdict("sell", -0.02), "right_direction")

    def test_sell_wrong_direction(self):
        self.assertEqual(verdict("sell", 0.02), "wrong_direction")

    def test_hold_right_hold(self):
        # |excess| < 3% 算對的 hold
        self.assertEqual(verdict("hold", 0.02), "right_hold")
        self.assertEqual(verdict("hold", -0.02), "right_hold")

    def test_hold_missed_long(self):
        self.assertEqual(verdict("hold", 0.08), "missed_long")

    def test_hold_avoided_loss(self):
        self.assertEqual(verdict("hold", -0.08), "avoided_loss")

    def test_hold_wrong_direction_marginal(self):
        # excess 在 3% < |x| < 5% 之間,算 wrong_direction
        self.assertEqual(verdict("hold", 0.04), "wrong_direction")
        self.assertEqual(verdict("hold", -0.04), "wrong_direction")


class TestParseTraderSection(unittest.TestCase):
    def test_parses_action_conviction_horizon_rationale(self):
        md = """
## 交易員

ACTION: hold
CONVICTION: 0.45
HORIZON: short
RATIONALE: 綜合四份報告後,多空訊號相互抵消,難以形成高信心方向判斷。
"""
        result = parse_trader_section(md)
        self.assertEqual(result["action"], "hold")
        self.assertAlmostEqual(result["conviction"], 0.45)
        self.assertEqual(result["horizon"], "short")
        self.assertIn("綜合四份", result["rationale"])

    def test_returns_none_when_no_trader_section(self):
        md = "## 技術分析師\n技術面看多。"
        self.assertIsNone(parse_trader_section(md))

    def test_extracts_action_case_insensitive(self):
        md = "## 交易員\nACTION: Buy\nCONVICTION: 0.7\nHORIZON: long\nRATIONALE: x"
        result = parse_trader_section(md)
        self.assertEqual(result["action"], "buy")


class TestComputeOutcome(unittest.TestCase):
    def test_compute_with_full_horizons(self):
        # d=2024-01-02 trader 說 buy,T+5 excess?
        # 2330: 600 → 615(idx 4=01-08), ret=+2.50%
        # TWII: 17000 → 17200, ret=+1.18%
        # excess = +1.32%
        sym2t = {"2330": "2330.TW"}
        result = compute_outcome(
            date="2024-01-02", symbol="2330",
            trader={"action": "buy", "conviction": 0.7, "horizon": "short",
                    "rationale": "..."},
            prices=fx_prices(), twii=fx_twii(),
            twii_dates=sorted(fx_twii().keys()), sym2t=sym2t,
            horizons=[5, 10],
        )
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["actual_excess_t5"], 0.0132, places=3)
        self.assertEqual(result["verdict"], "right_direction")  # buy + excess_t10>0

    def test_returns_none_when_horizon_not_reached(self):
        # d=2024-01-15(idx 9),T+10 → idx 19 超界 → None
        result = compute_outcome(
            date="2024-01-15", symbol="2330",
            trader={"action": "buy", "conviction": 0.7, "horizon": "short",
                    "rationale": "..."},
            prices=fx_prices(), twii=fx_twii(),
            twii_dates=sorted(fx_twii().keys()), sym2t={"2330": "2330.TW"},
            horizons=[10],
        )
        self.assertIsNone(result)

    def test_unknown_symbol_returns_none(self):
        result = compute_outcome(
            date="2024-01-02", symbol="9999",
            trader={"action": "buy", "conviction": 0.7, "horizon": "short",
                    "rationale": "..."},
            prices=fx_prices(), twii=fx_twii(),
            twii_dates=sorted(fx_twii().keys()), sym2t={"2330": "2330.TW"},
            horizons=[5],
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_ta_outcome -v
```

Expected: ImportError.

- [ ] **Step 3: Implement ta_outcome**

Create `agents/ta_outcome.py`:

```python
"""
TradingAgents-lite outcome 計算。

對 ta_reports/<date>/<symbol>.md 的 Trader 決策,從 stock_prices 算
T+5/10/20 實際 excess return + verdict 分類。重用
agents/verify_predictions.py 的計價邏輯。

落地檔: data/ta_outcomes/<date>/<symbol>.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
OUTCOMES = DATA / "ta_outcomes"
REPORTS = DATA / "ta_reports"

sys.path.insert(0, str(BASE / "agents"))
sys.path.insert(0, str(BASE / "scripts"))

from verify_predictions import _twii_excess, _stock_excess   # type: ignore[import-not-found]  # noqa: E402


def verdict(action: str, excess_t10: float) -> str:
    """根據 Trader 的 ACTION 跟 T+10 excess return 判定 verdict。

    6 種:right_direction / right_hold / missed_long / avoided_loss /
    wrong_direction。
    """
    if action == "buy":
        return "right_direction" if excess_t10 > 0.01 else "wrong_direction"
    if action == "sell":
        return "right_direction" if excess_t10 < -0.01 else "wrong_direction"
    # hold
    if abs(excess_t10) < 0.03:
        return "right_hold"
    if excess_t10 > 0.05:
        return "missed_long"
    if excess_t10 < -0.05:
        return "avoided_loss"
    return "wrong_direction"


_TRADER_HEADER_RE = re.compile(r"##\s*交易員", re.MULTILINE)
_FIELD_RE = {
    "action":     re.compile(r"^ACTION:\s*(\w+)", re.MULTILINE | re.IGNORECASE),
    "conviction": re.compile(r"^CONVICTION:\s*([0-9.]+)", re.MULTILINE | re.IGNORECASE),
    "horizon":    re.compile(r"^HORIZON:\s*(\w+)", re.MULTILINE | re.IGNORECASE),
    "rationale":  re.compile(r"^RATIONALE:\s*(.+?)(?=\n##|\Z)", re.MULTILINE | re.DOTALL | re.IGNORECASE),
}


def parse_trader_section(md: str) -> dict | None:
    """從 ta_report markdown 抓出 Trader section 的 4 個欄位。
    沒找到 Trader header 回 None。"""
    if not _TRADER_HEADER_RE.search(md):
        return None
    trader_idx = _TRADER_HEADER_RE.search(md).end()
    after = md[trader_idx:]
    result = {}
    for key, pattern in _FIELD_RE.items():
        m = pattern.search(after)
        if not m:
            return None
        val = m.group(1).strip()
        if key == "action":
            result[key] = val.lower()
        elif key == "conviction":
            try:
                result[key] = float(val)
            except ValueError:
                return None
        else:
            result[key] = val
    return result


def compute_outcome(
    *, date: str, symbol: str, trader: dict,
    prices: dict, twii: dict, twii_dates: list[str], sym2t: dict,
    horizons: list[int] = [5, 10, 20],
) -> dict | None:
    """算 T+horizons 的 excess return + verdict。
    任一 horizon 無法計算(超界 / 缺價 / 缺 TWII)就回 None。"""
    excesses: dict[int, float] = {}
    for h in horizons:
        twii_ret = _twii_excess(twii, twii_dates, date, h)
        if twii_ret is None:
            return None
        excess = _stock_excess(prices, twii_ret, symbol, sym2t, date, h)
        if excess is None:
            return None
        excesses[h] = excess

    # 主要 verdict 用 T+10(若無 T+10 用 T+5)
    primary_h = 10 if 10 in excesses else (5 if 5 in excesses else horizons[0])
    v = verdict(trader["action"], excesses[primary_h])

    return {
        "date": date,
        "symbol": symbol,
        "trader_action": trader["action"],
        "trader_conviction": trader["conviction"],
        "trader_horizon": trader["horizon"],
        "trader_rationale_excerpt": (trader.get("rationale") or "")[:200],
        **{f"actual_excess_t{h}": excesses[h] for h in horizons if h in excesses},
        "verdict": v,
        "primary_horizon": primary_h,
    }


def parse_report_md(report_path: Path) -> dict[str, str]:
    """把 ta_report 的 markdown 拆成 6 個 agent section 的 text dict。
    回 {market, chip, bull, bear, trader, risk} → 內容 string。"""
    text = report_path.read_text(encoding="utf-8")
    sections = {}
    # 按 ## 分段
    role_keys = [
        ("技術分析師", "market"), ("籌碼分析師", "chip"),
        ("多方研究員", "bull"), ("空方研究員", "bear"),
        ("交易員", "trader"), ("風險經理", "risk"),
    ]
    for role, key in role_keys:
        pattern = rf"##\s*{re.escape(role)}\s*\n(.*?)(?=\n##|\Z)"
        m = re.search(pattern, text, re.DOTALL)
        sections[key] = m.group(1).strip() if m else ""
    return sections


def run_outcomes(
    *, prices: dict, twii: dict, sym2t: dict,
    reports_dir: Path = REPORTS, outcomes_dir: Path = OUTCOMES,
    verbose: bool = True,
) -> dict:
    """掃 ta_reports/<*>/summary.json,對每筆 entry 算 outcome。
    Idempotent:已有 outcome json 的 skip。"""
    from pipeline import load_json  # noqa: E402
    twii_dates = sorted(twii.keys())

    appended = 0
    skipped_existing = 0
    skipped_not_ready = 0

    for date_dir in sorted(reports_dir.glob("*")):
        if not date_dir.is_dir():
            continue
        summary_path = date_dir / "summary.json"
        if not summary_path.exists():
            continue
        summary = load_json(summary_path)
        for entry in summary.get("entries", []):
            date = summary["date"]
            symbol = entry["symbol"]
            out_path = outcomes_dir / date / f"{symbol}.json"
            if out_path.exists():
                skipped_existing += 1
                continue

            report_path = date_dir / f"{symbol}.md"
            if not report_path.exists():
                continue
            md = report_path.read_text(encoding="utf-8")
            trader = parse_trader_section(md)
            if not trader:
                if verbose:
                    print(f"  {date} {symbol}: 無法 parse Trader section, skip")
                continue

            outcome = compute_outcome(
                date=date, symbol=symbol, trader=trader,
                prices=prices, twii=twii, twii_dates=twii_dates, sym2t=sym2t,
            )
            if outcome is None:
                skipped_not_ready += 1
                continue

            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(outcome, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            appended += 1

    if verbose:
        print(f"ta_outcome: appended={appended} "
              f"skipped_existing={skipped_existing} "
              f"skipped_not_ready={skipped_not_ready}")
    return {
        "appended": appended,
        "skipped_existing": skipped_existing,
        "skipped_not_ready": skipped_not_ready,
    }


def main() -> int:
    from pipeline import load_json  # noqa: E402
    prices = load_json(DATA / "stock_prices.json")
    twii = {k: float(v) for k, v in load_json(DATA / "twii_all.json").items()}
    sym2t = load_json(DATA / "stock_fetch_log.json")["symbol_to_ticker"]
    run_outcomes(prices=prices, twii=twii, sym2t=sym2t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_ta_outcome -v
```

Expected: 11/11 PASS (8 verdict + 3 parse + 3 compute).

- [ ] **Step 5: Commit**

```
git add agents/ta_outcome.py tests/test_ta_outcome.py
git commit -m "feat(lesson-loop): ta_outcome — T+N excess + verdict 分類"
```

---

### Task 3: ta_retriever — Protocol + ClaudeRetriever

**Files:**
- Create: `agents/ta_retriever.py`
- Test: `tests/test_ta_retriever.py`

定義 `LessonRetriever` Protocol，先實作 ClaudeRetriever（不需安裝任何套件）。

- [ ] **Step 1: Write the failing test**

Create `tests/test_ta_retriever.py`:

```python
"""
agents/ta_retriever.py 測試。LLM 全部 stub。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from ta_retriever import ClaudeRetriever   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_candidates() -> list[dict]:
    return [
        {"id": "2024-01-02_2330", "date": "2024-01-02", "reflection": "三榜皆空但漲 8%, 不該偏空"},
        {"id": "2024-01-05_1101", "date": "2024-01-05", "reflection": "技術面強勢但 bear 訊號失效"},
        {"id": "2024-01-08_2454", "date": "2024-01-08", "reflection": "MACD 黃金交叉初啟,可進"},
        {"id": "2024-01-10_2002", "date": "2024-01-10", "reflection": "警戒環境下三榜皆空多空難判"},
        {"id": "2024-01-12_3008", "date": "2024-01-12", "reflection": "融資減+法人買多次出現確實有效"},
    ]


class TestClaudeRetriever(unittest.TestCase):
    def test_parses_selected_indices(self):
        stub = lambda _: '{"selected": [1, 3, 5]}'
        r = ClaudeRetriever(llm_call=stub)
        result = r.retrieve("三榜皆空 + 技術強勢", fx_candidates(), k=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["id"], "2024-01-02_2330")
        self.assertEqual(result[1]["id"], "2024-01-08_2454")
        self.assertEqual(result[2]["id"], "2024-01-12_3008")

    def test_handles_out_of_range_indices(self):
        # LLM 回 99 (超界), 應被過濾
        stub = lambda _: '{"selected": [1, 99, 3]}'
        r = ClaudeRetriever(llm_call=stub)
        result = r.retrieve("query", fx_candidates(), k=3)
        self.assertEqual(len(result), 2)
        self.assertEqual({l["id"] for l in result},
                         {"2024-01-02_2330", "2024-01-08_2454"})

    def test_handles_non_json_response(self):
        stub = lambda _: "我覺得 lesson 1 最像"
        r = ClaudeRetriever(llm_call=stub)
        result = r.retrieve("query", fx_candidates(), k=3)
        # parse 失敗應回空 list 不要 crash
        self.assertEqual(result, [])

    def test_empty_candidates(self):
        stub = lambda _: '{"selected": []}'
        r = ClaudeRetriever(llm_call=stub)
        result = r.retrieve("query", [], k=3)
        self.assertEqual(result, [])

    def test_k_caps_returned_count(self):
        stub = lambda _: '{"selected": [1, 2, 3, 4, 5]}'
        r = ClaudeRetriever(llm_call=stub)
        result = r.retrieve("query", fx_candidates(), k=2)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_ta_retriever -v
```

Expected: ImportError.

- [ ] **Step 3: Implement ClaudeRetriever**

Create `agents/ta_retriever.py`:

```python
"""
TradingAgents-lite lesson retriever。

Protocol:
  retrieve(query, candidates, k) → list[dict] (top-k 相似 lesson)

實作:
  ClaudeRetriever:   用 claude -p 做 LLM-as-retriever ranking, 零安裝
  EmbeddingRetriever: sentence-transformers cosine sim, 需裝 ~1.5GB (Task 4)
  CompareRetriever:  兩個都跑 log 差異 (Task 5)

Walk-forward 不靠 retriever 守 — store 層已過濾 date<d 的 candidates,
retriever 純粹做相關度排序。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable, Protocol

BASE = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(BASE / "agents"))


class LessonRetriever(Protocol):
    def retrieve(
        self, query: str, candidates: list[dict], k: int,
    ) -> list[dict]:
        ...


def _format_candidates_numbered(candidates: list[dict]) -> str:
    """把 candidates 編 1-based 號碼 + reflection 摘要,給 LLM 看。"""
    lines = []
    for i, c in enumerate(candidates, start=1):
        ref = (c.get("reflection") or "")[:200]
        lines.append(f"{i}. [{c.get('date')} {c.get('symbol', '?')}] {ref}")
    return "\n".join(lines)


def _parse_selected_json(raw: str) -> list[int]:
    """從 LLM 輸出抓 {\"selected\": [1,3,5]} 的 indices。失敗回 []。"""
    if not raw:
        return []
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    indices = obj.get("selected")
    if not isinstance(indices, list):
        return []
    return [i for i in indices if isinstance(i, int)]


class ClaudeRetriever:
    """用 claude -p 從 candidates 挑 top-k 最相關的 lesson。

    優點:零安裝,重用既有 subprocess wrapper。
    缺點:慢(~10 秒/retrieval),會多一次 LLM call。
    """

    def __init__(self, llm_call: Callable[[str], str] | None = None):
        if llm_call is None:
            from predict import call_llm  # type: ignore[import-not-found]
            llm_call = lambda p: call_llm(p)
        self._llm_call = llm_call

    def retrieve(
        self, query: str, candidates: list[dict], k: int,
    ) -> list[dict]:
        if not candidates:
            return []
        prompt = self._build_prompt(query, candidates, k)
        raw = self._llm_call(prompt)
        indices = _parse_selected_json(raw)
        selected = []
        for idx in indices[:k]:
            if 1 <= idx <= len(candidates):
                selected.append(candidates[idx - 1])
        return selected

    def _build_prompt(self, query: str, candidates: list[dict], k: int) -> str:
        return f"""[SYSTEM]
你是 lesson 相關度評估員。你的任務:從一堆過去的 lesson 中,挑出 k 個跟「當前情境」最語意相關的。

[USER]
=== 當前情境 ===
{query}

=== 過去 lesson 候選(1-based 編號) ===
{_format_candidates_numbered(candidates)}

=== 任務 ===
從上述 {len(candidates)} 個 lesson 中挑 {k} 個跟當前情境最相關的。
只回 JSON,不要任何說明:
{{"selected": [3, 7, 15]}}
"""
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_ta_retriever -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```
git add agents/ta_retriever.py tests/test_ta_retriever.py
git commit -m "feat(lesson-loop): ClaudeRetriever — LLM-as-retriever, 零安裝"
```

---

### Task 4: ta_retriever — EmbeddingRetriever

**Files:**
- Modify: `agents/ta_retriever.py`
- Modify: `tests/test_ta_retriever.py`

加 EmbeddingRetriever。lazy import sentence-transformers，失敗回 None 讓 caller fallback。embed 結果寫進 npy 快取。

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ta_retriever.py`:

```python
class TestEmbeddingRetrieverWithStub(unittest.TestCase):
    """用 stub embedder 不真的 import sentence-transformers。"""

    def test_cosine_similarity_ranking(self):
        from ta_retriever import EmbeddingRetriever  # type: ignore[import-not-found]
        # stub embedder:把 text 轉成簡單向量(用第一個字 char code)
        import numpy as np
        def stub_embed(texts):
            return np.array(
                [[ord(t[0]) if t else 0, ord(t[-1]) if t else 0] for t in texts],
                dtype=np.float32,
            )

        r = EmbeddingRetriever(_embed_fn=stub_embed)
        # query "AB" 跟 candidates 比
        candidates = [
            {"id": "c1", "reflection": "AB lesson"},     # 開頭 A → 高相似
            {"id": "c2", "reflection": "XY lesson"},     # 開頭 X → 低相似
            {"id": "c3", "reflection": "AZ lesson"},     # 開頭 A → 也高
        ]
        result = r.retrieve("AB", candidates, k=2)
        self.assertEqual(len(result), 2)
        ids = {l["id"] for l in result}
        # c1 跟 c3 都是 A 開頭, 應入選
        self.assertIn("c1", ids)
        self.assertNotIn("c2", ids)

    def test_empty_candidates(self):
        from ta_retriever import EmbeddingRetriever  # type: ignore[import-not-found]
        r = EmbeddingRetriever(_embed_fn=lambda texts: __import__("numpy").zeros((len(texts), 2)))
        self.assertEqual(r.retrieve("query", [], k=3), [])


class TestEmbeddingRetrieverFallback(unittest.TestCase):
    """sentence-transformers 不存在時的 ImportError 處理。"""

    def test_make_retriever_falls_back_to_claude_when_st_missing(self):
        from ta_retriever import make_retriever  # type: ignore[import-not-found]
        # 假裝 sentence-transformers 安裝失敗 → 應 fallback 回 ClaudeRetriever
        # 用 patch 把 EmbeddingRetriever._real_init 改成 raise
        from ta_retriever import ClaudeRetriever
        stub_llm = lambda _: '{"selected": [1]}'
        r = make_retriever("embedding", _force_embedding_fail=True, _claude_llm=stub_llm)
        self.assertIsInstance(r, ClaudeRetriever)
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_ta_retriever -v
```

Expected: ImportError (EmbeddingRetriever / make_retriever not defined).

- [ ] **Step 3: Implement EmbeddingRetriever + factory**

Append to `agents/ta_retriever.py`:

```python
import warnings
from typing import Sequence


class EmbeddingRetriever:
    """sentence-transformers cosine sim retriever。

    第一次用 lazy load 模型 (~2-5 sec)。需要先:
        pip install sentence-transformers
    沒裝會 raise ImportError, caller(make_retriever)會 fallback 到 ClaudeRetriever。
    """

    def __init__(
        self,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
        _embed_fn=None,
    ):
        self._model_name = model_name
        if _embed_fn is not None:
            # 測試注入用
            self._embed_fn = _embed_fn
            self._model = None
        else:
            self._model = None  # lazy load
            self._embed_fn = None

    def _ensure_model(self):
        if self._embed_fn is not None or self._model is not None:
            return
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        self._model = SentenceTransformer(self._model_name)
        self._embed_fn = lambda texts: self._model.encode(  # type: ignore[union-attr]
            list(texts), show_progress_bar=False, convert_to_numpy=True,
        )

    def _embed(self, texts: Sequence[str]):
        self._ensure_model()
        return self._embed_fn(texts)

    def retrieve(
        self, query: str, candidates: list[dict], k: int,
    ) -> list[dict]:
        if not candidates:
            return []
        import numpy as np
        cand_texts = [(c.get("reflection") or "")[:500] for c in candidates]
        vecs = self._embed([query] + cand_texts)
        q_vec = vecs[0]
        c_vecs = vecs[1:]
        # cosine = dot / (|q|*|c|)
        q_norm = np.linalg.norm(q_vec) + 1e-9
        c_norms = np.linalg.norm(c_vecs, axis=1) + 1e-9
        scores = (c_vecs @ q_vec) / (c_norms * q_norm)
        top_idx = np.argsort(-scores)[:k]
        return [candidates[i] for i in top_idx]


def make_retriever(
    name: str,
    *,
    primary: str = "claude",
    _force_embedding_fail: bool = False,
    _claude_llm=None,
) -> LessonRetriever:
    """retriever 工廠。name in {"claude", "embedding", "compare", "none"}。

    name="embedding" 但裝套件失敗 → fallback to ClaudeRetriever + warning。
    name="compare" → CompareRetriever (定義在 Task 5)。
    """
    if name == "claude":
        return ClaudeRetriever(llm_call=_claude_llm)
    if name == "embedding":
        try:
            if _force_embedding_fail:
                raise ImportError("forced for test")
            return EmbeddingRetriever()
        except ImportError as e:
            warnings.warn(
                f"sentence-transformers 不可用 ({e}),"
                " fallback 到 ClaudeRetriever。"
                " 安裝指令: pip install sentence-transformers"
            )
            return ClaudeRetriever(llm_call=_claude_llm)
    if name == "compare":
        # 在 Task 5 加 CompareRetriever
        raise NotImplementedError("CompareRetriever 在 Task 5 實作")
    if name == "none":
        # null retriever 永遠回空
        class _NullRetriever:
            def retrieve(self, query, candidates, k):
                return []
        return _NullRetriever()
    raise ValueError(f"unknown retriever name: {name}")
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_ta_retriever -v
```

Expected: 7/7 PASS (5 ClaudeRetriever + 2 Embedding stub + 1 fallback).

- [ ] **Step 5: Commit**

```
git add agents/ta_retriever.py tests/test_ta_retriever.py
git commit -m "feat(lesson-loop): EmbeddingRetriever + make_retriever factory with ImportError fallback"
```

---

### Task 5: ta_retriever — CompareRetriever + log

**Files:**
- Modify: `agents/ta_retriever.py`
- Modify: `tests/test_ta_retriever.py`

CompareRetriever 同時跑 Embedding + Claude，log 兩邊選擇差異到 `data/retriever_compare.jsonl`，回傳 primary 指定的那個結果。

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ta_retriever.py`:

```python
class TestCompareRetriever(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp_log = Path(tempfile.mkdtemp()) / "compare.jsonl"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_log.parent, ignore_errors=True)

    def test_returns_primary_choice_and_logs_both(self):
        import json
        import numpy as np
        from ta_retriever import (   # type: ignore[import-not-found]
            CompareRetriever, ClaudeRetriever, EmbeddingRetriever,
        )

        stub_llm = lambda _: '{"selected": [1, 2]}'
        stub_embed = lambda texts: np.array([[i, 0] for i in range(len(texts))], dtype=np.float32)

        a = EmbeddingRetriever(_embed_fn=stub_embed)
        b = ClaudeRetriever(llm_call=stub_llm)
        r = CompareRetriever(a=a, b=b, primary="claude", log_path=self.tmp_log)

        candidates = [
            {"id": "c1", "reflection": "x", "date": "2024-01-01"},
            {"id": "c2", "reflection": "y", "date": "2024-01-02"},
            {"id": "c3", "reflection": "z", "date": "2024-01-03"},
        ]
        result = r.retrieve("query", candidates, k=2)

        # primary=claude → 回 claude 的選擇 (indices 1,2 → c1, c2)
        self.assertEqual([l["id"] for l in result], ["c1", "c2"])

        # log file 應有一行,記錄兩邊選擇
        self.assertTrue(self.tmp_log.exists())
        entries = [json.loads(line) for line in self.tmp_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["query"], "query")
        self.assertEqual(entry["primary"], "claude")
        self.assertIn("a_picked", entry)
        self.assertIn("b_picked", entry)
        self.assertEqual(entry["b_picked"], ["c1", "c2"])

    def test_primary_embedding(self):
        import numpy as np
        from ta_retriever import (   # type: ignore[import-not-found]
            CompareRetriever, ClaudeRetriever, EmbeddingRetriever,
        )

        stub_llm = lambda _: '{"selected": [3]}'
        # stub embedder 讓 c1 跟 query 最相似
        def stub_embed(texts):
            return np.array([[1, 0] if i == 0 or texts[i].startswith("x") else [0, 1]
                            for i in range(len(texts))], dtype=np.float32)

        a = EmbeddingRetriever(_embed_fn=stub_embed)
        b = ClaudeRetriever(llm_call=stub_llm)
        r = CompareRetriever(a=a, b=b, primary="embedding", log_path=self.tmp_log)

        candidates = [
            {"id": "c1", "reflection": "x first"},
            {"id": "c2", "reflection": "y other"},
            {"id": "c3", "reflection": "z third"},
        ]
        result = r.retrieve("x query", candidates, k=1)
        # primary=embedding → 回 embedding 選的 (c1)
        self.assertEqual([l["id"] for l in result], ["c1"])
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_ta_retriever -v
```

Expected: ImportError (CompareRetriever not defined).

- [ ] **Step 3: Implement CompareRetriever**

Append to `agents/ta_retriever.py`:

```python
from datetime import datetime


COMPARE_LOG = BASE / "data" / "retriever_compare.jsonl"


class CompareRetriever:
    """同時跑 A (embedding) + B (claude),log 差異到 jsonl, 回 primary 的結果。"""

    def __init__(
        self,
        a: LessonRetriever | None,
        b: LessonRetriever,
        primary: str = "claude",
        log_path: Path = COMPARE_LOG,
    ):
        self._a = a
        self._b = b
        self._primary = primary
        self._log_path = log_path

    def retrieve(
        self, query: str, candidates: list[dict], k: int,
    ) -> list[dict]:
        result_a = self._a.retrieve(query, candidates, k) if self._a else []
        result_b = self._b.retrieve(query, candidates, k)
        a_ids = [l.get("id") for l in result_a]
        b_ids = [l.get("id") for l in result_b]
        overlap = len(set(a_ids) & set(b_ids))
        self._log({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "query": query,
            "candidate_count": len(candidates),
            "k": k,
            "primary": self._primary,
            "a_picked": a_ids,
            "b_picked": b_ids,
            "overlap": overlap,
        })
        return result_a if self._primary == "embedding" else result_b

    def _log(self, entry: dict) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

Also update `make_retriever()` to handle "compare":

```python
# 在 make_retriever 內 name=="compare" 分支替換成:
    if name == "compare":
        try:
            if _force_embedding_fail:
                raise ImportError("forced for test")
            a = EmbeddingRetriever()
        except ImportError:
            warnings.warn("compare 模式 embedding 不可用,a=None,只跑 b")
            a = None
        b = ClaudeRetriever(llm_call=_claude_llm)
        return CompareRetriever(a=a, b=b, primary=primary)
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_ta_retriever -v
```

Expected: 9/9 PASS.

- [ ] **Step 5: Commit**

```
git add agents/ta_retriever.py tests/test_ta_retriever.py
git commit -m "feat(lesson-loop): CompareRetriever — A vs B 並行 log 差異"
```

---

### Task 6: ta_reflect

**Files:**
- Create: `agents/ta_reflect.py`
- Test: `tests/test_ta_reflect.py`

對 outcome 跑 LLM 反思 → 寫 lesson 進 store。idempotent 重跑不重寫。

- [ ] **Step 1: Write the failing test**

Create `tests/test_ta_reflect.py`:

```python
"""
agents/ta_reflect.py 測試。LLM stub。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from ta_reflect import build_reflection_prompt, parse_reflection_response, reflect_one   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_outcome() -> dict:
    return {
        "date": "2026-05-04",
        "symbol": "2330",
        "trader_action": "hold",
        "trader_conviction": 0.45,
        "trader_horizon": "short",
        "trader_rationale_excerpt": "綜合四份報告後,多空訊號相互抵消...",
        "actual_excess_t5": 0.012,
        "actual_excess_t10": 0.082,
        "actual_excess_t20": 0.045,
        "verdict": "missed_long",
        "primary_horizon": 10,
    }


def fx_report_sections() -> dict[str, str]:
    return {
        "market": "技術面 MA5/MA20 看似多頭排列,但動能轉弱...",
        "chip": "三榜皆空,籌碼面中性偏空觀望。",
        "bull": "MA20 仍提供支撐,歷史 long 勝率高...",
        "bear": "短線動能轉弱,跌幅 5.7% 尚未止穩...",
        "trader": "ACTION: hold\nCONVICTION: 0.45\n...",
        "risk": "同意,但建議觀察條件...",
    }


class TestBuildReflectionPrompt(unittest.TestCase):
    def test_includes_outcome_and_all_sections(self):
        prompt = build_reflection_prompt(fx_outcome(), fx_report_sections())
        # 三個 horizon 的 excess 都要在
        self.assertIn("+1.20%", prompt)    # T+5
        self.assertIn("+8.20%", prompt)    # T+10
        self.assertIn("+4.50%", prompt)    # T+20
        # verdict
        self.assertIn("missed_long", prompt)
        # Trader 決策
        self.assertIn("hold", prompt)
        # 其他 agent 摘要
        self.assertIn("三榜皆空", prompt)


class TestParseReflectionResponse(unittest.TestCase):
    def test_parses_clean_json(self):
        raw = '{"reflection": "我那天判斷錯了...", "tags": ["chip_silent", "tech_strong"]}'
        result = parse_reflection_response(raw)
        self.assertEqual(result["reflection"], "我那天判斷錯了...")
        self.assertEqual(result["tags"], ["chip_silent", "tech_strong"])

    def test_handles_json_with_surrounding_text(self):
        raw = '反思如下:\n{"reflection": "x", "tags": ["a"]}\n以上'
        result = parse_reflection_response(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["reflection"], "x")

    def test_returns_none_on_invalid_json(self):
        self.assertIsNone(parse_reflection_response("not json at all"))

    def test_returns_none_on_missing_fields(self):
        raw = '{"reflection": "x"}'   # 缺 tags
        self.assertIsNone(parse_reflection_response(raw))


class TestReflectOne(unittest.TestCase):
    def test_happy_path(self):
        stub = lambda _: ('{"reflection": "我把 chip 缺席解讀成偏空了", '
                          '"tags": ["chip_silent", "tech_strong"]}')
        lesson = reflect_one(fx_outcome(), fx_report_sections(), llm_call=stub)
        self.assertEqual(lesson["id"], "2026-05-04_2330")
        self.assertEqual(lesson["date"], "2026-05-04")
        self.assertEqual(lesson["symbol"], "2330")
        self.assertIn("chip 缺席", lesson["reflection"])
        self.assertEqual(lesson["tags"], ["chip_silent", "tech_strong"])
        self.assertEqual(lesson["outcome"]["verdict"], "missed_long")

    def test_llm_failure_returns_failed_marker(self):
        stub = lambda _: "[LLM timeout]"
        lesson = reflect_one(fx_outcome(), fx_report_sections(), llm_call=stub)
        self.assertEqual(lesson["id"], "2026-05-04_2330")
        self.assertTrue(lesson.get("reflect_failed"))
        self.assertIn("reason", lesson)

    def test_non_json_response_returns_failed_marker(self):
        stub = lambda _: "我覺得 trader 那天錯了"
        lesson = reflect_one(fx_outcome(), fx_report_sections(), llm_call=stub)
        self.assertTrue(lesson.get("reflect_failed"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_ta_reflect -v
```

Expected: ImportError.

- [ ] **Step 3: Implement ta_reflect**

Create `agents/ta_reflect.py`:

```python
"""
TradingAgents-lite reflection 層。

對 ta_outcome 寫的 outcome,組 prompt 餵 LLM,解析 JSON 輸出寫進
LessonStore。Idempotent — 已 reflected 的 outcome skip。

落地檔: data/ta_lessons.jsonl (via LessonStore)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

sys.path.insert(0, str(BASE / "agents"))

from ta_lesson_store import LessonStore   # type: ignore[import-not-found]  # noqa: E402
from ta_outcome import parse_report_md   # type: ignore[import-not-found]  # noqa: E402


def build_reflection_prompt(outcome: dict, report_sections: dict[str, str]) -> str:
    """組反思 prompt。outcome 含 3 個 horizon excess + verdict;
    report_sections 含 6 個 agent 的 markdown text。"""
    return f"""[SYSTEM]
你是台股分析師團隊的「教練」。針對單一交易決策的事後結果,
寫一段反思,幫 Trader 下次避免犯類似錯誤。

[USER]
=== 決策回顧 ===
日期: {outcome['date']}  標的: {outcome['symbol']}
Trader 那天:
  ACTION: {outcome['trader_action']}
  CONVICTION: {outcome['trader_conviction']}
  HORIZON: {outcome['trader_horizon']}
  RATIONALE 摘要: {outcome.get('trader_rationale_excerpt', '')}

=== 實際結果 ===
T+5 excess return: {outcome['actual_excess_t5']*100:+.2f}%
T+10 excess return: {outcome['actual_excess_t10']*100:+.2f}%
T+20 excess return: {outcome.get('actual_excess_t20', 0)*100:+.2f}%
verdict: {outcome['verdict']}

=== 同日其他 agent 報告摘要 ===
[Market Analyst] {report_sections.get('market', '(無)')[:300]}
[Chip Analyst]  {report_sections.get('chip', '(無)')[:300]}
[Bull]          {report_sections.get('bull', '(無)')[:300]}
[Bear]          {report_sections.get('bear', '(無)')[:300]}
[Risk Manager]  {report_sections.get('risk', '(無)')[:300]}

=== 你的任務 ===
1. 用繁體中文 200-300 字反思:
   - Trader 判斷哪裡對 / 哪裡錯
   - 是哪個上游 agent (Market/Chip/Bull/Bear) 把 Trader 帶歪
   - 下次遇到類似情境應該注意什麼

2. 從以下 tag 池挑 3-5 個最貼切的:
   chip_silent, chip_active, chip_alert_concentrated,
   tech_strong, tech_weak, tech_high_volatility, tech_overbought, tech_oversold,
   alert_day, normal_day, alert_persistent,
   rate_high, rate_borderline,
   bull_outperformed, bear_outperformed, bull_bear_balanced

只輸出 JSON,不要說明:
{{"reflection": "...", "tags": ["chip_silent", "tech_strong"]}}
"""


def parse_reflection_response(raw: str) -> dict | None:
    """從 LLM 輸出抓 reflection JSON。失敗回 None。"""
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "reflection" not in obj or "tags" not in obj:
        return None
    if not isinstance(obj["tags"], list):
        return None
    return obj


def reflect_one(
    outcome: dict, report_sections: dict[str, str],
    *, llm_call, ticker: str | None = None,
) -> dict:
    """對單一 outcome 跑反思,回 lesson dict(可能含 reflect_failed marker)。"""
    lesson_id = f"{outcome['date']}_{outcome['symbol']}"
    now = datetime.now().isoformat(timespec="seconds")
    base = {
        "id": lesson_id,
        "date": outcome["date"],
        "symbol": outcome["symbol"],
        "ticker": ticker or f"{outcome['symbol']}.TW",
        "outcome": {
            "verdict": outcome["verdict"],
            "trader_action": outcome["trader_action"],
            "trader_conviction": outcome["trader_conviction"],
            "actual_excess_t10": outcome["actual_excess_t10"],
        },
        "reflected_at": now,
    }

    prompt = build_reflection_prompt(outcome, report_sections)
    try:
        raw = llm_call(prompt)
    except Exception as e:
        return {**base, "reflect_failed": True,
                "reason": f"{type(e).__name__}: {e}"}

    if not raw or raw.startswith("[LLM timeout]") or raw.startswith("[LLM error"):
        return {**base, "reflect_failed": True,
                "reason": f"llm_marker: {(raw or '')[:200]}"}

    parsed = parse_reflection_response(raw)
    if parsed is None:
        return {**base, "reflect_failed": True,
                "reason": "non_json_output",
                "raw_excerpt": raw[:300]}

    return {**base,
            "reflection": parsed["reflection"][:1000],
            "tags": parsed["tags"][:10]}


def run_reflections(
    *, store: LessonStore, llm_call,
    outcomes_dir: Path = DATA / "ta_outcomes",
    reports_dir: Path = DATA / "ta_reports",
    verbose: bool = True,
) -> dict:
    """掃 outcomes_dir,對未 reflect 的跑反思寫進 store。"""
    appended = 0
    skipped_existing = 0
    failed = 0

    for date_dir in sorted(outcomes_dir.glob("*")):
        if not date_dir.is_dir():
            continue
        for outcome_path in sorted(date_dir.glob("*.json")):
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
            lesson_id = f"{outcome['date']}_{outcome['symbol']}"

            existing = next(
                (l for l in store.all() if l["id"] == lesson_id),
                None,
            )
            if existing and not existing.get("reflect_failed"):
                skipped_existing += 1
                continue

            report_path = reports_dir / outcome["date"] / f"{outcome['symbol']}.md"
            if not report_path.exists():
                if verbose:
                    print(f"  {lesson_id}: report missing, skip")
                continue
            sections = parse_report_md(report_path)

            lesson = reflect_one(outcome, sections, llm_call=llm_call)
            store.append(lesson)
            if lesson.get("reflect_failed"):
                failed += 1
            else:
                appended += 1

    if verbose:
        print(f"ta_reflect: appended={appended} "
              f"skipped_existing={skipped_existing} failed={failed}")
    return {"appended": appended, "skipped_existing": skipped_existing,
            "failed": failed}


def main() -> int:
    sys.path.insert(0, str(BASE / "agents"))
    from predict import call_llm   # type: ignore[import-not-found]
    store = LessonStore()
    run_reflections(store=store, llm_call=lambda p: call_llm(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_ta_reflect -v
```

Expected: 9/9 PASS (1 prompt + 4 parse + 3 reflect_one + run_reflections 略).

- [ ] **Step 5: Commit**

```
git add agents/ta_reflect.py tests/test_ta_reflect.py
git commit -m "feat(lesson-loop): ta_reflect — LLM 反思 Trader 決策寫 lesson"
```

---

### Task 7: ta_features.collect — 加 lessons field

**Files:**
- Modify: `agents/ta_features.py` (append `lessons` to `SymbolFeatures` + `collect` 參數)
- Modify: `tests/test_ta_features.py` (加 lessons 測試)

`SymbolFeatures` 加 `lessons: list[dict]` 欄位（預設 `[]`）。`collect()` 接受 `lessons` 參數透傳。

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ta_features.py` 的 `TestCollect` class:

```python
    def test_collect_with_lessons(self):
        sample_lesson = {
            "id": "2024-01-02_2330", "date": "2024-01-02", "symbol": "2330",
            "reflection": "test lesson", "tags": ["test"],
        }
        result = collect(
            symbol="2330", ticker="2330.TW", d="2024-01-15",
            merged=fx_merged(), prices=fx_prices(),
            twii=fx_twii(), prediction_rows=fx_prediction_rows(),
            price_window=5,
            lessons=[sample_lesson],
        )
        self.assertEqual(len(result.lessons), 1)
        self.assertEqual(result.lessons[0]["id"], "2024-01-02_2330")

    def test_collect_lessons_defaults_to_empty(self):
        result = collect(
            symbol="2330", ticker="2330.TW", d="2024-01-15",
            merged=fx_merged(), prices=fx_prices(),
            twii=fx_twii(), prediction_rows=fx_prediction_rows(),
            price_window=5,
        )
        self.assertEqual(result.lessons, [])
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_ta_features.TestCollect -v
```

Expected: TypeError (lessons 未知參數) 或 AttributeError (SymbolFeatures 無 lessons 欄位).

- [ ] **Step 3: Modify ta_features.py**

In `agents/ta_features.py`:

Find:
```python
@dataclass(frozen=True)
class SymbolFeatures:
    """單一 symbol 對日期 d 的完整 feature bundle。所有資料嚴格 < d。"""
    symbol: str
    ticker: str
    target_date: str
    chip: dict
    price: dict | None
    past_perf: dict
    market_context: dict
```

Replace with:
```python
@dataclass(frozen=True)
class SymbolFeatures:
    """單一 symbol 對日期 d 的完整 feature bundle。所有資料嚴格 < d。

    lessons: 過去語意相似的 lesson(list of dict),由 retriever 撈出,
              caller 預先準備。為空 list 表示不展示「過去判斷紀錄」section。
    """
    symbol: str
    ticker: str
    target_date: str
    chip: dict
    price: dict | None
    past_perf: dict
    market_context: dict
    lessons: list[dict] = field(default_factory=list)
```

Add `field` to existing imports near top:
```python
from dataclasses import dataclass, field
```

Find `collect()` and modify signature + body:
```python
def collect(
    *, symbol: str, ticker: str, d: str,
    merged: list[dict], prices: dict, twii: dict[str, float],
    prediction_rows: list[dict],
    chip_window: int = 60, price_window: int = 60, market_window: int = 30,
    lessons: list[dict] | None = None,
) -> SymbolFeatures:
    """組裝單一 symbol 在 d 的完整 feature。嚴格 walk-forward。"""
    return SymbolFeatures(
        symbol=symbol,
        ticker=ticker,
        target_date=d,
        chip=chip_features(symbol, d, merged, window=chip_window),
        price=price_features(ticker, d, prices, twii, window=price_window),
        past_perf=past_perf(symbol, d, prediction_rows),
        market_context=market_context(d, merged, twii, n_recent=market_window),
        lessons=lessons or [],
    )
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m unittest tests.test_ta_features -v
```

Expected: all existing 27 tests + 2 new = 29/29 PASS.

- [ ] **Step 5: Commit**

```
git add agents/ta_features.py tests/test_ta_features.py
git commit -m "feat(lesson-loop): SymbolFeatures 加 lessons field, collect 接受 lessons 參數"
```

---

### Task 8: ta_prompts._header — 顯示 lessons

**Files:**
- Modify: `agents/ta_prompts.py`

如果 features.lessons 非空，在 `_header()` 末尾加「過去判斷紀錄」section。

- [ ] **Step 1: 手動 sanity check 既有 prompt 不破**

Pre-check：用 ta_features 已 commit 的測試確認 `_format_*` helpers 還 work：

```
python -c "
import sys; sys.path.insert(0, 'agents')
from ta_features import collect
from ta_prompts import build_market_analyst_prompt
from pipeline import load_json
m = load_json('data/all_data_merged.json')
p = load_json('data/stock_prices.json')
t = {k: float(v) for k, v in load_json('data/twii_all.json').items()}
f = collect(symbol='2330', ticker='2330.TW', d='2026-05-04',
            merged=m, prices=p, twii=t, prediction_rows=[])
print(build_market_analyst_prompt(f)[-200:])
"
```

Expected: 看到 prompt 結尾且不 crash。

- [ ] **Step 2: Modify ta_prompts.py 加 `_format_lessons` + 在 `_header` 末加 section**

In `agents/ta_prompts.py`:

Add a new helper after `_format_market`:
```python
def _format_lessons(lessons: list[dict]) -> str:
    """格式化過去判斷紀錄(retriever 撈出來的歷史 lesson)。
    空 list 回空字串(prompt 不顯示這個 section)。"""
    if not lessons:
        return ""
    lines = ["\n=== 過去判斷紀錄(語意相似的歷史教訓) ==="]
    for ln in lessons[:5]:
        verdict = ln.get("outcome", {}).get("verdict", "?")
        action = ln.get("outcome", {}).get("trader_action", "?")
        ref = (ln.get("reflection") or "")[:180]
        sym = ln.get("symbol", "?")
        date = ln.get("date", "?")
        lines.append(
            f"- [{date} {sym}] Trader={action} → verdict={verdict}\n"
            f"  反思: {ref}{'...' if len(ln.get('reflection', '')) > 180 else ''}"
        )
    return "\n".join(lines) + "\n"
```

Modify `_header()`:
```python
def _header(f: SymbolFeatures, role: str) -> str:
    return (
        f"[SYSTEM]\n你是台股分析師團隊中的「{role}」。"
        "輸出必須是繁體中文純文字(不要 JSON、不要 markdown 標題)。\n\n"
        f"[USER]\n=== 分析標的 ===\n"
        f"代號: {f.symbol}  ticker: {f.ticker}  分析日: {f.target_date}\n\n"
        f"=== 籌碼面 ===\n{_format_chip(f.chip)}\n\n"
        f"=== 技術面 ===\n{_format_price(f.price)}\n\n"
        f"=== 過去 AI 推薦紀錄 ===\n{_format_past_perf(f.past_perf)}\n\n"
        f"=== 大盤近況 ===\n{_format_market(f.market_context)}\n"
        f"{_format_lessons(f.lessons)}"
    )
```

- [ ] **Step 3: 用 lessons 跑一次 sanity check 驗 prompt 出現新 section**

```
python -c "
import sys; sys.path.insert(0, 'agents')
from ta_features import collect
from ta_prompts import build_chip_analyst_prompt
from pipeline import load_json
m = load_json('data/all_data_merged.json')
p = load_json('data/stock_prices.json')
t = {k: float(v) for k, v in load_json('data/twii_all.json').items()}
fake_lessons = [
    {'id': '2026-04-29_2330', 'date': '2026-04-29', 'symbol': '2330',
     'reflection': '當時三榜皆空但漲 8%, 偏空判斷錯了',
     'outcome': {'verdict': 'missed_long', 'trader_action': 'hold'}},
]
f = collect(symbol='2330', ticker='2330.TW', d='2026-05-04',
            merged=m, prices=p, twii=t, prediction_rows=[],
            lessons=fake_lessons)
prompt = build_chip_analyst_prompt(f)
print('過去判斷紀錄' in prompt, '2026-04-29' in prompt)
"
```

Expected: `True True`.

- [ ] **Step 4: Run existing tests to ensure no regression**

```
python -m unittest tests.test_ta_runner tests.test_ta_features -v 2>&1 | tail -5
```

Expected: 51/51 PASS (39 existing + 2 new from Task 7 + 10 still ta_runner).

- [ ] **Step 5: Commit**

```
git add agents/ta_prompts.py
git commit -m "feat(lesson-loop): _format_lessons + _header 末加「過去判斷紀錄」section"
```

---

### Task 9: ta_deepdive — CLI flags + 串接 retriever

**Files:**
- Modify: `agents/ta_deepdive.py`

加 `--retriever` / `--primary` / `--skip-lessons` / `--top-lessons` flags。在 collect() 前做 retrieval。

- [ ] **Step 1: Modify ta_deepdive.py**

In `agents/ta_deepdive.py`:

Add at top imports:
```python
from ta_lesson_store import LessonStore   # type: ignore[import-not-found]
from ta_retriever import make_retriever   # type: ignore[import-not-found]
```

In `main()`, after existing argparse setup, add new args:
```python
    ap.add_argument("--retriever", choices=["claude", "embedding", "compare", "none"],
                     default="none",
                     help="lesson retrieval 後端,預設 none(不撈 lessons)")
    ap.add_argument("--primary", choices=["claude", "embedding"], default="claude",
                     help="compare 模式時回哪個的結果")
    ap.add_argument("--top-lessons", type=int, default=5,
                     help="撈 top-N 個 lesson 塞 prompt")
    ap.add_argument("--skip-lessons", action="store_true",
                     help="略過 lesson 撈取(等價 --retriever none)")
```

In the per-symbol loop, before `features = collect(...)`, insert:
```python
        # Lesson retrieval(walk-forward: 只看 lesson.date < d)
        lessons = []
        if not args.skip_lessons and args.retriever != "none":
            try:
                store = LessonStore()
                candidates = store.query_candidates(before=d)
                if candidates:
                    retriever = make_retriever(args.retriever, primary=args.primary)
                    query = (f"日期 {d} 標的 {sym} ({ticker})。"
                              f"當日大盤 rate={_lookup_rate(merged, d)}。"
                              "需要找過去類似情境的判斷紀錄。")
                    lessons = retriever.retrieve(query, candidates, k=args.top_lessons)
                    print(f"    撈 {len(lessons)}/{len(candidates)} 個 lesson")
            except Exception as e:
                print(f"    [WARN] retrieval 失敗: {e},改用 lessons=[]")
                lessons = []
```

Add helper after `resolve_ticker()`:
```python
def _lookup_rate(merged: list[dict], d: str) -> int | None:
    """找 d 那天的 rate(若不在 merged 回 None)。"""
    for r in merged:
        if r.get("date") == d:
            return r.get("rate")
    return None
```

Modify the `collect(...)` call to pass `lessons`:
```python
        features = collect(
            symbol=sym, ticker=ticker, d=d,
            merged=merged, prices=prices, twii=twii,
            prediction_rows=prediction_rows,
            lessons=lessons,
        )
```

- [ ] **Step 2: 驗證 --help 不破 + --retriever none 不影響原有行為**

```
python agents/ta_deepdive.py --help
```

Expected: 看到新的 4 個 flag。

- [ ] **Step 3: 跑既有 PoC + 新 flag 不影響行為(快速 smoke test 不打 LLM 改用 --retriever none + 1 個 symbol)**

```
python -c "
import sys; sys.path.insert(0, 'agents')
import argparse
# 不真跑 main, 只驗 import 與 wire-up 不破
from ta_deepdive import _lookup_rate, resolve_ticker, pick_symbols_from_predictions
print('imports OK')
"
```

Expected: `imports OK`.

- [ ] **Step 4: Commit**

```
git add agents/ta_deepdive.py
git commit -m "feat(lesson-loop): ta_deepdive 加 --retriever / --skip-lessons / --top-lessons,collect 前做 retrieval"
```

---

### Task 10: ta_backfill — 批次回填 CLI

**Files:**
- Create: `agents/ta_backfill.py`
- Test: `tests/test_ta_backfill.py`

CLI iterate 一段日期區間，對每日跑 deepdive + outcome + reflect，checkpoint 中斷可 resume。

- [ ] **Step 1: Write the failing test**

Create `tests/test_ta_backfill.py`:

```python
"""
agents/ta_backfill.py 測試。實際 LLM call 不打,純驗 logic。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from ta_backfill import select_dates, read_checkpoint, write_checkpoint   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_merged():
    return [
        {"date": "2026-04-28", "rate": 175, "bull": "x", "bear": "y", "top5_margin_reduce_inst_buy": ""},
        {"date": "2026-04-29", "rate": 165, "bull": "x", "bear": "y", "top5_margin_reduce_inst_buy": ""},
        {"date": "2026-04-30", "rate": 180, "bull": "x", "bear": "y", "top5_margin_reduce_inst_buy": ""},
        {"date": "2026-05-04", "rate": 181, "bull": "x", "bear": "y", "top5_margin_reduce_inst_buy": ""},
    ]


class TestSelectDates(unittest.TestCase):
    def test_alert_only_filters_below_170(self):
        result = select_dates(
            fx_merged(), from_date="2026-04-01", to_date="2026-05-31",
            alert_only=True,
        )
        # 04-29 (rate=165) 不入選
        self.assertEqual(result, ["2026-04-28", "2026-04-30", "2026-05-04"])

    def test_no_alert_filter_returns_all_in_range(self):
        result = select_dates(
            fx_merged(), from_date="2026-04-01", to_date="2026-05-31",
            alert_only=False,
        )
        self.assertEqual(len(result), 4)

    def test_date_range_inclusive(self):
        result = select_dates(
            fx_merged(), from_date="2026-04-30", to_date="2026-04-30",
            alert_only=True,
        )
        self.assertEqual(result, ["2026-04-30"])


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "ck.json"

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()
        self.tmp.parent.rmdir()

    def test_no_checkpoint_returns_none(self):
        self.assertIsNone(read_checkpoint(self.tmp))

    def test_write_and_read_roundtrip(self):
        write_checkpoint(self.tmp, "2026-04-29", {"appended": 5})
        ck = read_checkpoint(self.tmp)
        self.assertEqual(ck["last_completed"], "2026-04-29")
        self.assertEqual(ck["stats"]["appended"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m unittest tests.test_ta_backfill -v
```

Expected: ImportError.

- [ ] **Step 3: Implement ta_backfill**

Create `agents/ta_backfill.py`:

```python
"""
TradingAgents-lite backfill CLI。

對指定日期區間批次跑 (deepdive → outcome → reflect),checkpoint 中斷可 resume。

用法:
  python agents/ta_backfill.py --from 2026-02-01 --to 2026-05-04 --alert-only
  python agents/ta_backfill.py --from 2026-02-01 --to 2026-05-04 --retriever compare
  python agents/ta_backfill.py --dry-run --from 2026-02-01 --to 2026-05-04 --alert-only

落地檔: data/ta_backfill_checkpoint.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
CHECKPOINT = DATA / "ta_backfill_checkpoint.json"

sys.path.insert(0, str(BASE / "agents"))
sys.path.insert(0, str(BASE / "scripts"))


def select_dates(
    merged: list[dict], *, from_date: str, to_date: str, alert_only: bool = True,
) -> list[str]:
    """從 merged 篩出 [from_date, to_date] 區間的日期(可選只警戒日)。"""
    result = []
    for r in merged:
        d = r.get("date", "")
        if d < from_date or d > to_date:
            continue
        if alert_only and r.get("rate", 0) < 170:
            continue
        result.append(d)
    return sorted(result)


def read_checkpoint(path: Path = CHECKPOINT) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_checkpoint(path: Path, last_completed: str, stats: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last_completed": last_completed, "stats": stats},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resume_from(dates: list[str], checkpoint: dict | None) -> list[str]:
    if not checkpoint:
        return dates
    last = checkpoint.get("last_completed", "")
    return [d for d in dates if d > last]


def main() -> int:
    from pipeline import load_json   # type: ignore[import-not-found]

    ap = argparse.ArgumentParser(description="TA-lite backfill")
    ap.add_argument("--from", dest="from_date", required=True)
    ap.add_argument("--to", dest="to_date", required=True)
    ap.add_argument("--alert-only", action="store_true", default=True)
    ap.add_argument("--all-days", action="store_true",
                     help="覆寫 --alert-only,跑全部日期(含非警戒)")
    ap.add_argument("--retriever", choices=["claude", "embedding", "compare", "none"],
                     default="none")
    ap.add_argument("--primary", choices=["claude", "embedding"], default="claude")
    ap.add_argument("--top-lessons", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=3,
                     help="long/short 各取 top N")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--symbols", default=None,
                     help="可選:手動指定 symbols (CSV)")
    ap.add_argument("--force", action="store_true",
                     help="忽略 checkpoint,從頭跑")
    ap.add_argument("--dry-run", action="store_true",
                     help="列出會跑哪些日期就退出")
    args = ap.parse_args()

    alert_only = args.alert_only and not args.all_days
    merged = load_json(DATA / "all_data_merged.json")
    dates = select_dates(
        merged, from_date=args.from_date, to_date=args.to_date,
        alert_only=alert_only,
    )

    checkpoint = None if args.force else read_checkpoint()
    dates_to_run = _resume_from(dates, checkpoint)

    print(f"backfill 範圍: {args.from_date} ~ {args.to_date}  "
          f"alert_only={alert_only}")
    print(f"符合條件的日期: {len(dates)} 個")
    if checkpoint:
        print(f"checkpoint last_completed={checkpoint['last_completed']},"
              f" 跳過 {len(dates) - len(dates_to_run)} 個")
    print(f"待跑: {len(dates_to_run)} 個")
    if args.dry_run:
        for d in dates_to_run:
            print(f"  - {d}")
        return 0

    if not dates_to_run:
        print("無待跑日期,結束")
        return 0

    stats = {"deepdive_ok": 0, "deepdive_fail": 0, "outcome_added": 0,
             "reflect_added": 0, "reflect_failed": 0}

    for i, d in enumerate(dates_to_run, start=1):
        print(f"\n[{i}/{len(dates_to_run)}] === {d} ===")
        try:
            # 1. Run deepdive (subprocess 重用既有 CLI)
            cmd = [
                sys.executable, str(BASE / "agents" / "ta_deepdive.py"), d,
                "--retriever", args.retriever,
                "--primary", args.primary,
                "--top-lessons", str(args.top_lessons),
                "--top-n", str(args.top_n),
                "--model", args.model,
                "--timeout", str(args.timeout),
            ]
            if args.symbols:
                cmd += ["--symbols", args.symbols]
            r = subprocess.run(cmd, encoding="utf-8", errors="replace")
            if r.returncode == 0:
                stats["deepdive_ok"] += 1
            else:
                stats["deepdive_fail"] += 1
                print(f"  [WARN] deepdive 非零 exit code = {r.returncode}")

            # 2. Run outcome
            from ta_outcome import run_outcomes   # type: ignore[import-not-found]
            prices = load_json(DATA / "stock_prices.json")
            twii = {k: float(v) for k, v in load_json(DATA / "twii_all.json").items()}
            sym2t = load_json(DATA / "stock_fetch_log.json")["symbol_to_ticker"]
            out_stats = run_outcomes(
                prices=prices, twii=twii, sym2t=sym2t, verbose=False,
            )
            stats["outcome_added"] += out_stats["appended"]

            # 3. Run reflect
            from ta_lesson_store import LessonStore   # type: ignore[import-not-found]
            from ta_reflect import run_reflections   # type: ignore[import-not-found]
            from predict import call_llm   # type: ignore[import-not-found]
            store = LessonStore()
            ref_stats = run_reflections(
                store=store,
                llm_call=lambda p: call_llm(p, model=args.model, timeout=args.timeout),
                verbose=False,
            )
            stats["reflect_added"] += ref_stats["appended"]
            stats["reflect_failed"] += ref_stats["failed"]

            # 4. Checkpoint
            write_checkpoint(CHECKPOINT, last_completed=d, stats=stats)

        except KeyboardInterrupt:
            print("\n[INFO] 中斷,checkpoint 已存,下次 --resume 繼續")
            return 130
        except Exception as e:
            print(f"  [ERR] {d}: {type(e).__name__}: {e}")
            traceback.print_exc()
            stats["deepdive_fail"] += 1

    print(f"\n=== Backfill 完成 ===")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes + 驗證 CLI --help / --dry-run**

```
python -m unittest tests.test_ta_backfill -v
```

Expected: 5/5 PASS.

Also:
```
python agents/ta_backfill.py --help
python agents/ta_backfill.py --from 2026-04-28 --to 2026-05-04 --alert-only --dry-run
```

Expected: help 印出 + dry-run 列出 3 個日期 (04-28, 04-30, 05-04) 就退出。

- [ ] **Step 5: Commit**

```
git add agents/ta_backfill.py tests/test_ta_backfill.py
git commit -m "feat(lesson-loop): ta_backfill — 批次回填 CLI,checkpoint/resume,dry-run"
```

---

### Task 11: PoC backfill 50 天 + 評估

**Files:**
- 無 code 變更
- 跑 `data/ta_outcomes/<*>/`, `data/ta_lessons.jsonl`, `data/retriever_compare.jsonl` 等實際輸出
- 寫一份 `reports/lesson_loop_poc_2026-05-XX.md` 給人讀

- [ ] **Step 1: Dry-run 確認預計跑哪些日期**

```
python agents/ta_backfill.py --from 2026-02-01 --to 2026-05-04 --alert-only --dry-run
```

Expected: 列出 ~50 個警戒日（含 04-30, 05-04 等）。如果超過 50 個則往 --from 後拉到剛好 50 天左右。

- [ ] **Step 2: 跑 backfill (預估 5-10 小時連續跑,可中斷 resume)**

```
python agents/ta_backfill.py --from 2026-02-01 --to 2026-05-04 \
    --alert-only --retriever compare --primary claude \
    --model sonnet --top-n 3 --top-lessons 5
```

Expected:
- 每跑完一個日期會 print stats + 更新 checkpoint
- 若中斷重跑會自動 resume
- 完成後 `data/ta_lessons.jsonl` 有 ~300 筆 lesson、`data/retriever_compare.jsonl` 有 retrieval 比對 log

如果踩 subscription rate limit:
- Ctrl+C 中斷
- 等 30 分鐘
- 重跑（會自動 resume）
- 若狀況持續，改 `--model haiku` 加速

- [ ] **Step 3: 用 ta-lite-critic 評估 lesson 品質**

派 ta-lite-critic agent 隨機抽 5 個 lesson 看 reflection 品質：

```
# 在 Claude Code 對話框
@ta-lite-critic 隨機抽 5 筆 data/ta_lessons.jsonl 評估 reflection 品質:
  - reflection 是不是真的引用了 outcome 數字 (T+N excess)
  - 有沒有點出哪個上游 agent 帶歪 Trader
  - tags 標得對不對
```

- [ ] **Step 4: 用 signal-skeptic 評估 retriever_compare**

```
# 在 Claude Code 對話框
@signal-skeptic 看 data/retriever_compare.jsonl 統計:
  - A (embedding) 跟 B (claude) 選擇的 overlap 率
  - 若 overlap < 30%,代表兩個 retriever 看資料完全不同 → 哪個值得信任?
  - 推薦 PoC 後該用哪個 retriever
```

- [ ] **Step 5: 對比「有 lesson」vs「沒 lesson」報告品質**

```
# 跑同一檔股票兩種模式
python agents/ta_deepdive.py 2026-05-04 --symbols 2330 --retriever none
mv data/ta_reports/2026-05-04/2330.md data/ta_reports/2026-05-04/2330-no-lessons.md

python agents/ta_deepdive.py 2026-05-04 --symbols 2330 --retriever claude --top-lessons 5
# 這次會看到 prompt 有「過去判斷紀錄」section

# 派 ta-lite-critic 比對
@ta-lite-critic 比對 data/ta_reports/2026-05-04/2330-no-lessons.md 與
data/ta_reports/2026-05-04/2330.md,看「有 lesson」是否實質改善 chip/trader 論述
```

- [ ] **Step 6: 寫 PoC 評估報告**

新增 `reports/lesson_loop_poc_2026-05-XX.md` (XX = 完成日期)，~400 字：
- 實際跑了幾天、多少 lesson、多少 outcome
- reflection 品質抽查結論（ta-lite-critic 評）
- retriever A vs B 對比結論（signal-skeptic 評）
- 「有 lesson」vs「沒 lesson」報告對比
- subscription 是否踩 rate limit、總耗時
- 結論：是否要 Phase 2 擴展到全 105 警戒日？哪個 retriever 是 default？

- [ ] **Step 7: Commit PoC 結果**

```
git add reports/lesson_loop_poc_2026-05-XX.md
git add data/ta_lessons.jsonl data/retriever_compare.jsonl
git add data/ta_outcomes/ data/ta_reports/ data/ta_backfill_checkpoint.json
git commit -m "report: TA-lite lesson loop PoC 評估 ~50 警戒日"
```

---

## Self-Review (post-write)

### Spec coverage check
- [x] §1 LessonStore + walk-forward query → Task 1
- [x] §1 ta_outcome + verdict + parse_report_md → Task 2
- [x] §1 Retriever Protocol + ClaudeRetriever → Task 3
- [x] §1 EmbeddingRetriever + fallback → Task 4
- [x] §1 CompareRetriever → Task 5
- [x] §1 ta_reflect → Task 6
- [x] §1 ta_features.collect lessons field → Task 7
- [x] §1 ta_prompts._header lessons section → Task 8
- [x] §1 ta_deepdive CLI flags → Task 9
- [x] §1 ta_backfill checkpoint/resume → Task 10
- [x] §9 PoC 驗證計畫 → Task 11

### Placeholder scan
- 無 TBD / TODO / "implement later"
- 每個 step 都有完整 code block 或 exact command

### Type / 名稱一致性
- `LessonStore` 方法名: `append` / `query_candidates` / `exists` / `all` — 全 plan 一致
- `LessonRetriever.retrieve(query, candidates, k)` — 所有 impl 一致
- `make_retriever(name, *, primary=..., _force_embedding_fail=...)` — Task 4/5/9 一致
- `compute_outcome(*, date, symbol, trader, prices, twii, twii_dates, sym2t, horizons)` — Task 2 定義,Task 10 從未直接呼叫(走 run_outcomes wrapper)
- `reflect_one(outcome, report_sections, *, llm_call, ticker)` — Task 6 定義,Task 10 從未直接呼叫(走 run_reflections wrapper)
- `SymbolFeatures.lessons: list[dict]` — Task 7 加,Task 8 讀,Task 9 塞值

### Scope check
- 單一 plan,11 tasks,~2-3 小時 implementation + ~5-10 小時 backfill 跑
- 不引入 langchain / mem0 / letta
- 不接 daily-fetch / dashboard
- 結束在 PoC report,後續 Phase 2 / Phase 3 由人工決定
