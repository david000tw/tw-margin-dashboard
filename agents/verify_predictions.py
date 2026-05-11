"""
讀 ai_predictions.jsonl,對每個 (prediction.date, h) 缺對應 outcome 的 pair,
若 prices.dates 上已有 d+h 個交易日,計算 long/short 的 T+h excess return
並 append outcome 行。

呼叫:
  python agents/verify_predictions.py            # 從預設路徑讀寫
  from verify_predictions import run; run()      # daily-fetch / backfill 內呼叫
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
JSONL = DATA / "ai_predictions.jsonl"
PRICES = DATA / "stock_prices.json"
TWII = DATA / "twii_all.json"
FETCH_LOG = DATA / "stock_fetch_log.json"

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import load_json   # type: ignore[import-not-found]
from analyze_signals import (   # type: ignore[import-not-found]
    find_idx_on_or_before, step_forward, get_close_at_idx,
)
from predict import load_jsonl, append_jsonl   # type: ignore[import-not-found]


def _twii_excess(twii: dict, twii_dates: list, d: str, h: int) -> float | None:
    """T+h 的 TWII 報酬;d 不在 twii_dates 直接 None。"""
    try:
        i = twii_dates.index(d)
    except ValueError:
        return None
    base = twii.get(d)
    if not base:
        return None
    j = i
    cnt = 0
    while cnt < h and j < len(twii_dates) - 1:
        j += 1
        cnt += 1
    later = twii.get(twii_dates[j])
    return None if (not later) else (later / base - 1)


def _stock_excess(
    prices: dict, twii_ret: float, sym: str, sym2t: dict, d: str, h: int,
) -> float | None:
    """單一 symbol 的 T+h excess return = stock_ret - twii_ret。失敗回 None。"""
    ticker = sym2t.get(sym) or sym2t.get(sym.rstrip("*"))
    if not ticker:
        return None
    dates = prices["dates"]
    base_idx = find_idx_on_or_before(dates, d)
    if base_idx < 0:
        return None
    p0 = get_close_at_idx(prices, ticker, base_idx)
    if not p0:
        return None
    n_idx = step_forward(dates, base_idx, h)
    if n_idx == base_idx and h > 0:
        return None    # 沒前進(d 在末日附近)
    pN = get_close_at_idx(prices, ticker, n_idx)
    if pN is None:
        return None
    return (pN / p0 - 1) - twii_ret


def compute_outcome(
    prediction: dict, horizon: int, *,
    prices: dict, twii: dict, twii_dates: list, sym2t: dict,
) -> dict | None:
    """
    若 d+h 已可算,回傳 outcome dict;否則 None(等下次再 verify)。
    """
    d = prediction["date"]
    # 必須 d+h 在 prices.dates 範圍內
    base_idx = find_idx_on_or_before(prices["dates"], d)
    if base_idx < 0 or step_forward(prices["dates"], base_idx, horizon) <= base_idx:
        return None      # 還沒過 h 個交易日

    twii_ret = _twii_excess(twii, twii_dates, d, horizon)
    if twii_ret is None:
        return None      # TWII 還沒到 d+h

    long_excess: list[float | None] = [
        _stock_excess(prices, twii_ret, e["symbol"], sym2t, d, horizon)
        for e in prediction.get("long", [])
    ]
    short_excess: list[float | None] = [
        _stock_excess(prices, twii_ret, e["symbol"], sym2t, d, horizon)
        for e in prediction.get("short", [])
    ]

    long_resolved = [x for x in long_excess if x is not None]
    short_resolved = [x for x in short_excess if x is not None]
    long_avg = (sum(long_resolved) / len(long_resolved)) if long_resolved else 0.0
    short_avg = (sum(short_resolved) / len(short_resolved)) if short_resolved else 0.0

    # win 判定:long 期望正 alpha、short 期望負 alpha
    long_win = long_avg > 0 if long_resolved else False
    short_win = short_avg < 0 if short_resolved else False
    win = long_win or short_win

    return {
        "type": "outcome",
        "date": d,
        "horizon": horizon,
        "long_excess": long_excess,
        "short_excess": short_excess,
        "long_avg_excess": round(long_avg, 6),
        "short_avg_excess": round(short_avg, 6),
        "long_n_resolved": len(long_resolved),
        "short_n_resolved": len(short_resolved),
        "long_win": long_win,
        "short_win": short_win,
        "win": win,
        "verified_at": datetime.now().isoformat(timespec="seconds"),
    }


def run(
    *, prices: dict | None = None, twii: dict | None = None,
    sym2t: dict | None = None, jsonl: Path = JSONL, verbose: bool = True,
) -> dict:
    """
    對 jsonl 中尚未 verified 的 (prediction.date, horizon) 算 outcome 並 append。
    Idempotent:已有 outcome 行的 (date, h) 不重寫。
    """
    prices_d: dict = prices if prices is not None else load_json(PRICES)
    twii_d: dict = twii if twii is not None else {k: float(v) for k, v in load_json(TWII).items()}
    sym2t_d: dict = sym2t if sym2t is not None else load_json(FETCH_LOG)["symbol_to_ticker"]
    twii_dates = sorted(twii_d.keys())

    rows = load_jsonl(jsonl)
    existing_outcomes = {(r["date"], r["horizon"]) for r in rows if r.get("type") == "outcome"}
    predictions = [r for r in rows if r.get("type") == "prediction"]

    appended = 0
    skipped_not_yet = 0
    skipped_existing = 0

    for p in predictions:
        d = p["date"]
        for h in p.get("horizons", []):
            if (d, h) in existing_outcomes:
                skipped_existing += 1
                continue
            outcome = compute_outcome(p, h, prices=prices_d, twii=twii_d,
                                       twii_dates=twii_dates, sym2t=sym2t_d)
            if outcome is None:
                skipped_not_yet += 1
                continue
            append_jsonl(outcome, jsonl)
            existing_outcomes.add((d, h))   # 同一 run 內 dedupe
            appended += 1

    if verbose:
        print(f"verify_predictions: appended={appended} "
              f"skipped_not_yet={skipped_not_yet} skipped_existing={skipped_existing}")
    return {
        "appended": appended,
        "skipped_not_yet": skipped_not_yet,
        "skipped_existing": skipped_existing,
    }


def main() -> int:
    run(verbose=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
