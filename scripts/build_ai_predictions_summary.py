"""
讀 data/ai_predictions.jsonl,算累積 stats,寫成 data/ai_predictions_summary.json
給 dashboard 直接 fetch(~50 KB)。

呼叫:
  python scripts/build_ai_predictions_summary.py            # 標準輸出
  python scripts/build_ai_predictions_summary.py --quiet    # 不印 sanity check

backfill / daily-fetch 跑完後自動 chain。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
JSONL = DATA / "ai_predictions.jsonl"
SUMMARY = DATA / "ai_predictions_summary.json"
BACKTEST = DATA / "backtest_summary.json"

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "agents"))
from pipeline import load_json, save_json   # type: ignore[import-not-found]
from predict import load_jsonl   # type: ignore[import-not-found]


def _winrate(outcomes: list[dict], side: str) -> float:
    if not outcomes:
        return 0.0
    win_key = f"{side}_win"
    return sum(1 for o in outcomes if o.get(win_key)) / len(outcomes)


def _avg_excess(outcomes: list[dict], side: str) -> float:
    if not outcomes:
        return 0.0
    key = f"{side}_avg_excess"
    return sum(o.get(key, 0.0) for o in outcomes) / len(outcomes)


def _bucket_by_horizon(outcomes: list[dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for o in outcomes:
        out.setdefault(o["horizon"], []).append(o)
    return out


def _rolling_winrate(outcomes_h20: list[dict], window: int) -> list[dict]:
    """對 T+20 outcome 按 date 排序,算 rolling window 勝率序列。"""
    sorted_o = sorted(outcomes_h20, key=lambda o: o["date"])
    out = []
    for i in range(window, len(sorted_o) + 1):
        chunk = sorted_o[i - window:i]
        out.append({
            "date": chunk[-1]["date"],
            "long": round(_winrate(chunk, "long"), 4),
            "short": round(_winrate(chunk, "short"), 4),
        })
    return out


def _date_window_filter(outcomes: list[dict], min_date: str | None) -> list[dict]:
    if not min_date:
        return outcomes
    return [o for o in outcomes if o["date"] >= min_date]


def _baseline_t20() -> float | None:
    """從 backtest_summary.json 拿 scantrader top5 recommended preset 的 test 窗 T+20 avg。"""
    if not BACKTEST.exists():
        return None
    try:
        bs = load_json(BACKTEST)
        return bs["by_side"]["top5"]["filtered_presets"]["recommended"]["test_window"]["by_horizon"]["20"]["avg_excess"]
    except (KeyError, TypeError):
        return None


def build(jsonl_path: Path = JSONL) -> dict:
    rows = load_jsonl(jsonl_path)
    predictions = [r for r in rows if r.get("type") == "prediction"]
    outcomes = [r for r in rows if r.get("type") == "outcome"]
    failed = [r for r in rows if r.get("type") == "prediction_failed"]

    if not predictions:
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "n_predictions": 0,
            "n_failed": len(failed),
            "n_verified": 0,
        }

    by_h = _bucket_by_horizon(outcomes)
    today = datetime.now().date()
    recent_30_lo = (today - timedelta(days=30)).isoformat()
    recent_90_lo = (today - timedelta(days=90)).isoformat()

    # By horizon stats(全期)
    by_horizon: dict[str, dict] = {}
    for h in (5, 10, 20):
        os_ = by_h.get(h, [])
        by_horizon[str(h)] = {
            "n": len(os_),
            "long_avg_excess": round(_avg_excess(os_, "long"), 6),
            "short_avg_excess": round(_avg_excess(os_, "short"), 6),
            "long_winrate": round(_winrate(os_, "long"), 4),
            "short_winrate": round(_winrate(os_, "short"), 4),
        }

    # Rolling winrate(T+20)
    h20 = by_h.get(20, [])
    rolling_30 = _rolling_winrate(h20, 30) if len(h20) >= 30 else []
    rolling_90 = _rolling_winrate(h20, 90) if len(h20) >= 90 else []

    # Decay:全期 vs 近 90 / 30 天
    decay = {
        "all": {
            "long_avg_excess": round(_avg_excess(h20, "long"), 6),
            "short_avg_excess": round(_avg_excess(h20, "short"), 6),
        },
        "recent_90": {
            "long_avg_excess": round(_avg_excess(_date_window_filter(h20, recent_90_lo), "long"), 6),
            "short_avg_excess": round(_avg_excess(_date_window_filter(h20, recent_90_lo), "short"), 6),
        },
        "recent_30": {
            "long_avg_excess": round(_avg_excess(_date_window_filter(h20, recent_30_lo), "long"), 6),
            "short_avg_excess": round(_avg_excess(_date_window_filter(h20, recent_30_lo), "short"), 6),
        },
    }

    # vs scantrader baseline
    baseline = _baseline_t20()
    vs_baseline = {
        "ai_long_t20_avg": by_horizon["20"]["long_avg_excess"],
        "scantrader_top5_test_avg": baseline,
        "diff": (round(by_horizon["20"]["long_avg_excess"] - baseline, 6)
                  if baseline is not None else None),
    }

    # Recent 30 prediction 詳情(給 dashboard 表格)
    sorted_pred = sorted(predictions, key=lambda p: p["date"])[-30:]
    outcomes_idx = {(o["date"], o["horizon"]): o for o in outcomes}
    recent_table = []
    for p in sorted_pred:
        entry = {
            "date": p["date"],
            "long": [{"symbol": e["symbol"], "conviction": e.get("conviction")}
                       for e in p.get("long", [])],
            "short": [{"symbol": e["symbol"], "conviction": e.get("conviction")}
                       for e in p.get("short", [])],
            "rationale": p.get("rationale", "")[:200],
            "outcome": {},
        }
        for h in p.get("horizons", []):
            o = outcomes_idx.get((p["date"], h))
            if o:
                entry["outcome"][str(h)] = {
                    "long_avg_excess": o["long_avg_excess"],
                    "short_avg_excess": o["short_avg_excess"],
                    "long_win": o["long_win"],
                    "short_win": o["short_win"],
                }
        recent_table.append(entry)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_predictions": len(predictions),
        "n_failed": len(failed),
        "n_verified": len(set(o["date"] for o in outcomes)),
        "first_date": sorted_pred[0]["date"] if sorted_pred else None,
        "last_date": sorted_pred[-1]["date"] if sorted_pred else None,
        "by_horizon": by_horizon,
        "rolling_winrate_30": rolling_30,
        "rolling_winrate_90": rolling_90,
        "decay": decay,
        "vs_scantrader_baseline": vs_baseline,
        "recent_30_predictions": recent_table,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not JSONL.exists():
        print(f"[warn] {JSONL.relative_to(BASE)} 不存在,寫入空 summary", file=sys.stderr)

    summary = build()
    save_json(SUMMARY, summary)

    if not args.quiet:
        size_kb = SUMMARY.stat().st_size / 1024
        print(f"寫入 {SUMMARY.relative_to(BASE)}: {size_kb:.1f} KB")
        print(f"  n_predictions = {summary.get('n_predictions', 0)}")
        print(f"  n_verified    = {summary.get('n_verified', 0)}")
        print(f"  n_failed      = {summary.get('n_failed', 0)}")
        if summary.get("by_horizon"):
            for h, s in summary["by_horizon"].items():
                print(f"  T+{h:>2}: long avg={s['long_avg_excess']:+.4f} winrate={s['long_winrate']:.2f} | "
                      f"short avg={s['short_avg_excess']:+.4f} winrate={s['short_winrate']:.2f} (n={s['n']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
