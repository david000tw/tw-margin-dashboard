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
from analyze_signals import find_idx_on_or_before   # type: ignore[import-not-found]  # noqa: E402


def verdict(action: str, excess_t10: float) -> str:
    if action == "buy":
        return "right_direction" if excess_t10 > 0.01 else "wrong_direction"
    if action == "sell":
        return "right_direction" if excess_t10 < -0.01 else "wrong_direction"
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
    header_match = _TRADER_HEADER_RE.search(md)
    if not header_match:
        return None
    after = md[header_match.end():]
    result: dict = {}
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
    任一 horizon 超界 / 缺價 / 缺 TWII / 缺 symbol → 回 None。"""
    dates = prices["dates"]
    base_idx = find_idx_on_or_before(dates, date)
    if base_idx < 0:
        return None
    ticker = sym2t.get(symbol) or sym2t.get(symbol.rstrip("*"))
    if not ticker:
        return None

    excesses: dict[int, float] = {}
    for h in horizons:
        if base_idx + h >= len(dates):
            return None
        twii_ret = _twii_excess(twii, twii_dates, date, h)
        if twii_ret is None:
            return None
        excess = _stock_excess(prices, twii_ret, symbol, sym2t, date, h)
        if excess is None:
            return None
        excesses[h] = excess

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


def run_outcomes(
    *, prices: dict, twii: dict, sym2t: dict,
    reports_dir: Path = REPORTS, outcomes_dir: Path = OUTCOMES,
    verbose: bool = True,
) -> dict:
    """掃 ta_reports/<*>/summary.json,對每筆 entry 算 outcome。
    Idempotent:已有 outcome json 的 skip。"""
    from pipeline import load_json   # type: ignore[import-not-found]  # noqa: E402
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
    from pipeline import load_json   # type: ignore[import-not-found]  # noqa: E402
    prices = load_json(DATA / "stock_prices.json")
    twii = {k: float(v) for k, v in load_json(DATA / "twii_all.json").items()}
    sym2t = load_json(DATA / "stock_fetch_log.json")["symbol_to_ticker"]
    run_outcomes(prices=prices, twii=twii, sym2t=sym2t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
