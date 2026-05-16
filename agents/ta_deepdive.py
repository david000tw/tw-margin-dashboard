"""
TradingAgents-lite CLI 入口。

用法:
  python agents/ta_deepdive.py 2026-05-14
  python agents/ta_deepdive.py 2026-05-14 --symbols 2330,2317,2454
  python agents/ta_deepdive.py 2026-05-14 --model haiku --top-n 2

預設行為:讀 data/ai_predictions.jsonl 取該日的 long top-3 + short top-3(共 6 檔,
依 conviction 排序)跑深度報告。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

sys.path.insert(0, str(BASE / "agents"))
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE))

from pipeline import load_json  # type: ignore[import-not-found]
from predict import call_llm  # type: ignore[import-not-found]
from ta_features import collect  # type: ignore[import-not-found]
from ta_lesson_store import LessonStore  # type: ignore[import-not-found]
from ta_retriever import make_retriever  # type: ignore[import-not-found]
from ta_runner import run_pipeline, write_report  # type: ignore[import-not-found]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def pick_symbols_from_predictions(
    rows: list[dict], d: str, *, top_n: int = 3,
) -> list[str]:
    """從 ai_predictions.jsonl 取 date=d 的 prediction,各取 conviction top_n 個 long + short。"""
    target = next(
        (r for r in rows if r.get("type") == "prediction" and r.get("date") == d),
        None,
    )
    if not target:
        return []
    longs = sorted(target.get("long", []), key=lambda e: -e.get("conviction", 0))[:top_n]
    shorts = sorted(target.get("short", []), key=lambda e: -e.get("conviction", 0))[:top_n]
    return [e["symbol"] for e in longs] + [e["symbol"] for e in shorts]


def resolve_ticker(symbol: str, symbol_index: dict) -> str | None:
    """從 symbol_index.json 拿 ticker;沒對應回 None。"""
    by_sym = symbol_index.get("by_symbol", {})
    entry = by_sym.get(symbol) or by_sym.get(symbol.rstrip("*"))
    return entry.get("ticker") if entry else None


def _lookup_rate(merged: list[dict], d: str) -> int | None:
    """找 d 那天的 rate(若不在 merged 回 None)。"""
    for r in merged:
        if r.get("date") == d:
            return r.get("rate")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="TradingAgents-lite 多 agent 深度分析")
    ap.add_argument("date", help="分析日(YYYY-MM-DD),嚴格 walk-forward < d")
    ap.add_argument("--symbols", help="逗號分隔的 symbols,預設從 ai_predictions.jsonl 取")
    ap.add_argument("--model", default="sonnet", help="LLM 模型(預設 sonnet,可用 haiku)")
    ap.add_argument("--top-n", type=int, default=3, help="預設模式下 long/short 各取 top N")
    ap.add_argument("--timeout", type=int, default=180, help="單一 LLM call timeout 秒")
    ap.add_argument("--retriever", choices=["claude", "embedding", "compare", "none"],
                     default="none",
                     help="lesson retrieval 後端,預設 none(不撈 lessons)")
    ap.add_argument("--primary", choices=["claude", "embedding"], default="claude",
                     help="compare 模式時回哪個的結果")
    ap.add_argument("--top-lessons", type=int, default=5,
                     help="撈 top-N 個 lesson 塞 prompt")
    ap.add_argument("--skip-lessons", action="store_true",
                     help="略過 lesson 撈取(等價 --retriever none)")
    args = ap.parse_args()

    d = args.date
    merged = load_json(DATA / "all_data_merged.json")
    prices = load_json(DATA / "stock_prices.json")
    twii = {k: float(v) for k, v in load_json(DATA / "twii_all.json").items()}
    prediction_rows = _read_jsonl(DATA / "ai_predictions.jsonl")
    symbol_index = load_json(DATA / "symbol_index.json")

    # 決定 symbols
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = pick_symbols_from_predictions(prediction_rows, d, top_n=args.top_n)
        if not symbols:
            print(f"[ERR] ai_predictions.jsonl 中找不到 d={d} 的 prediction;"
                  "請用 --symbols 手動指定")
            return 2

    print(f"分析日: {d}  symbols: {symbols}  model: {args.model}")
    print(f"預估時間: ~{len(symbols) * 6 * 20 / 60:.1f} 分鐘(6 agents × ~20 秒/call)")

    llm_call = lambda p: call_llm(p, model=args.model, timeout=args.timeout)

    n_ok = n_partial = n_failed = n_skipped = 0
    for sym in symbols:
        ticker = resolve_ticker(sym, symbol_index)
        if not ticker:
            print(f"  {sym}: SKIP(找不到 ticker)")
            n_skipped += 1
            continue
        print(f"  {sym} ({ticker}): 跑 pipeline...", flush=True)
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
        features = collect(
            symbol=sym, ticker=ticker, d=d,
            merged=merged, prices=prices, twii=twii,
            prediction_rows=prediction_rows,
            lessons=lessons,
        )
        result = run_pipeline(features, llm_call=llm_call)
        write_report(result)
        print(f"    → status={result['status']}")
        if result["status"] == "ok":
            n_ok += 1
        elif result["status"] == "partial":
            n_partial += 1
        else:
            n_failed += 1

    print(f"\n完成: ok={n_ok} partial={n_partial} failed={n_failed} skipped={n_skipped}")
    print(f"報告: data/ta_reports/{d}/")
    return 0 if (n_ok + n_partial) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
