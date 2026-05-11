"""
歷史回填 LLM 預測:對 merged 中每一天(WARMUP 後)順序呼叫 LLM,寫進
data/ai_predictions.jsonl。每 50 筆 verify 一次,讓 in-context feedback 持續更新。

嚴格 walk-forward:任何給定 d 的 prompt 都不外漏 d 之後的資料(由
agents/predict.py 的 walk_forward_context 守護;測試覆蓋見 tests/test_predict.py)。

用法:
  python scripts/backfill_predictions.py                    # 從 checkpoint 續跑
  python scripts/backfill_predictions.py --max 100          # 試水 100 筆
  python scripts/backfill_predictions.py --start 2022-01-01 # 指定起點
  python scripts/backfill_predictions.py --model haiku      # 換模型

可中斷 / resume:每筆寫完 fsync;重跑時讀 jsonl 末筆 prediction.date 作 checkpoint。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
JSONL = DATA / "ai_predictions.jsonl"

MERGED    = DATA / "all_data_merged.json"
TWII      = DATA / "twii_all.json"
PRICES    = DATA / "stock_prices.json"
FETCH_LOG = DATA / "stock_fetch_log.json"

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "agents"))
from pipeline import load_json   # type: ignore[import-not-found]
from predict import (   # type: ignore[import-not-found]
    walk_forward_context, predict_one_day, append_prediction,
    last_prediction_date, load_jsonl,
)
import verify_predictions   # type: ignore[import-not-found]


WARMUP = 30                   # 跳過前 30 天(讓 LLM 至少看到 30 天 context)
VERIFY_EVERY = 50             # 每 N 筆 verify 一次
RETRY_BACKOFF = [30, 120, 300]   # LLM 失敗重試延遲(秒)


def _running_winrate(jsonl: Path) -> str:
    """印目前已 verified 的 long/short 累積勝率,用於 sanity check。"""
    rows = load_jsonl(jsonl)
    outcomes = [r for r in rows if r.get("type") == "outcome"]
    if not outcomes:
        return "(no outcomes yet)"
    by_h: dict[int, list[dict]] = {}
    for o in outcomes:
        by_h.setdefault(o["horizon"], []).append(o)
    parts = []
    for h in sorted(by_h):
        os_ = by_h[h]
        lw = sum(1 for o in os_ if o.get("long_win")) / len(os_)
        sw = sum(1 for o in os_ if o.get("short_win")) / len(os_)
        parts.append(f"T+{h} L={lw:.2f} S={sw:.2f} (n={len(os_)})")
    return " | ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="從哪天開始(預設 merged 第 30 天之後)")
    ap.add_argument("--max", type=int, help="最多跑 N 筆(試水用)")
    ap.add_argument("--model", default="sonnet", help="LLM model: sonnet | haiku")
    ap.add_argument("--no-verify", action="store_true", help="跳過 verify(只做 predict)")
    args = ap.parse_args()

    print("[load] merged / twii / prices / fetch_log...")
    merged = load_json(MERGED)
    twii   = {k: float(v) for k, v in load_json(TWII).items()}
    prices = load_json(PRICES)
    sym2t  = load_json(FETCH_LOG)["symbol_to_ticker"]
    print(f"  merged={len(merged)}, twii={len(twii)}, "
          f"prices.dates={len(prices['dates'])} × {len(prices['prices'])} ticker, "
          f"sym2t={len(sym2t)}")

    last_done = last_prediction_date(JSONL)
    all_dates = sorted({r["date"] for r in merged})
    pending = all_dates[WARMUP:]
    if args.start:
        pending = [d for d in pending if d >= args.start]
    if last_done:
        pending = [d for d in pending if d > last_done]
        print(f"[resume] 從 {last_done} 之後續跑")
    if args.max:
        pending = pending[:args.max]

    print(f"[plan] 將處理 {len(pending)} 筆(WARMUP={WARMUP}, model={args.model})")
    if not pending:
        print("[done] 沒有待處理的日期")
        return 0

    started = time.perf_counter()
    for i, d in enumerate(pending):
        # 每 VERIFY_EVERY 筆 verify 一次,讓後續 prompt 帶到新驗證好的 outcome
        if not args.no_verify and i % VERIFY_EVERY == 0 and i > 0:
            verify_predictions.run(prices=prices, twii=twii, sym2t=sym2t,
                                    jsonl=JSONL, verbose=False)

        rows = load_jsonl(JSONL)
        ctx = walk_forward_context(
            d, merged=merged, twii=twii,
            rows=rows, price_dates=prices["dates"],
        )

        # 含 backoff retry 的 LLM 呼叫
        last_err: Exception | None = None
        p = None
        for attempt, delay in enumerate([0] + RETRY_BACKOFF):
            if delay:
                time.sleep(delay)
            try:
                p = predict_one_day(d, ctx=ctx, sym2t=sym2t, model=args.model)
                # predict_one_day 內部已 retry parse 一次;若仍 prediction_failed 接受
                break
            except Exception as e:
                last_err = e
                continue

        if p is None:
            p = {
                "type": "prediction_failed", "date": d, "model": args.model,
                "reason": f"all_retries_failed: {type(last_err).__name__}: {last_err}",
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        append_prediction(p)

        if (i + 1) % 10 == 0 or i == 0:
            elapsed = time.perf_counter() - started
            eta = elapsed / (i + 1) * (len(pending) - i - 1)
            print(f"[{i+1}/{len(pending)}] {d} done "
                  f"(elapsed={elapsed:.0f}s, eta={eta:.0f}s)")

        if (i + 1) % 100 == 0 and not args.no_verify:
            verify_predictions.run(prices=prices, twii=twii, sym2t=sym2t,
                                    jsonl=JSONL, verbose=False)
            print(f"  → running winrate: {_running_winrate(JSONL)}")

    # 最後一次 verify 收尾
    if not args.no_verify:
        verify_predictions.run(prices=prices, twii=twii, sym2t=sym2t,
                                jsonl=JSONL, verbose=True)
    print(f"\n[done] {len(pending)} 筆處理完")
    print(f"[summary] {_running_winrate(JSONL)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
