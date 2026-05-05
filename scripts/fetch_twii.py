"""
補齊 data/twii_all.json 缺漏的台股大盤指數 (^TWII) 收盤價。

從 all_data_merged.json 取所有交易日，找出 twii_all.json 缺的日子，
用 yfinance 抓 ^TWII 收盤補進去。週末 / 休市日不會有 TWII 收盤，
yfinance 抓不到就跳過。

用法 (從 repo root):
  python scripts/fetch_twii.py

需要:
  pip install yfinance
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
MERGED = DATA / "all_data_merged.json"
TWII = DATA / "twii_all.json"


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    import yfinance as yf

    merged = json.loads(MERGED.read_text(encoding="utf-8"))
    twii: dict[str, float] = json.loads(TWII.read_text(encoding="utf-8")) if TWII.exists() else {}

    merged_dates = sorted({r["date"] for r in merged})
    missing = [d for d in merged_dates if d not in twii]

    if not missing:
        print("已是最新，無缺漏")
        return 0

    print(f"merged 共 {len(merged_dates)} 天，twii 缺 {len(missing)} 天")
    print(f"缺漏範圍: {missing[0]} ~ {missing[-1]}")

    start = (datetime.strptime(missing[0], "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
    end = (datetime.strptime(missing[-1], "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")
    print(f"yfinance ^TWII {start} ~ {end} ...")

    df = yf.download("^TWII", start=start, end=end, progress=False, auto_adjust=False)
    if df is None or df.empty:
        print("yfinance 無回應")
        return 1

    closes = df["Close"]
    if hasattr(closes, "columns"):
        closes = closes.iloc[:, 0]
    fetched = {d.strftime("%Y-%m-%d"): round(float(v), 2) for d, v in closes.dropna().items()}

    added: list[str] = []
    skipped_no_data: list[str] = []
    for d in missing:
        if d in fetched:
            twii[d] = fetched[d]
            added.append(d)
        else:
            skipped_no_data.append(d)

    twii_sorted = dict(sorted(twii.items()))
    TWII.write_text(
        json.dumps(twii_sorted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n新增 {len(added)} 天:")
    for d in added:
        print(f"  {d}: {twii[d]}")
    if skipped_no_data:
        print(f"\n跳過 {len(skipped_no_data)} 天 (yfinance 無收盤資料，可能休市/週末):")
        for d in skipped_no_data:
            wd = datetime.strptime(d, "%Y-%m-%d").strftime("%a")
            print(f"  {d} ({wd})")
    print(f"\ntwii_all.json 現有 {len(twii_sorted)} 筆")
    return 0


if __name__ == "__main__":
    sys.exit(main())
