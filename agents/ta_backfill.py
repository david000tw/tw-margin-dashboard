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
sys.path.insert(0, str(BASE))


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
