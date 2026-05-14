"""
TradingAgents-lite feature 預取層:每個 function 嚴格 walk-forward,
只讀 record.date < d 的資料。所有 feature 都是純函式,易於測試。

agents/ta_runner.py 在組 prompt 前先呼叫 collect() 拿到 SymbolFeatures。
"""
from __future__ import annotations


def _records_before(merged: list[dict], d: str, window: int) -> list[dict]:
    """回傳 < d 的最近 window 筆 record(時序排序)。"""
    earlier = [r for r in merged if r["date"] < d]
    return earlier[-window:]


def _symbol_in_field(record: dict, field: str, symbol: str) -> bool:
    """symbol 是否出現在 record[field](逗號分隔)中。處理 '*' / 空字串。"""
    val = record.get(field) or ""
    if not val:
        return False
    parts = [p.strip().rstrip("*") for p in val.split(",")]
    return symbol.rstrip("*") in parts


def chip_features(
    symbol: str, d: str, merged: list[dict], *, window: int = 60,
) -> dict:
    """
    對 symbol 在 d 之前 window 個交易日的籌碼面統計。

    回傳:
      bull_count        在 bull 榜出現次數
      bear_count        在 bear 榜出現次數
      top5_count        在 top5_margin_reduce_inst_buy 出現次數
      last_top5_date    最近一次出現於 top5 的日期(None 若沒出現過)
      last_top5_rate    該日 rate
      bull_avg_rate     出現於 bull 當日的平均 rate(None 若沒出現過)
    """
    window_records = _records_before(merged, d, window)
    bull_dates: list[tuple[str, int]] = []
    bear_count = 0
    top5_appearances: list[tuple[str, int]] = []

    for r in window_records:
        if _symbol_in_field(r, "bull", symbol):
            bull_dates.append((r["date"], r["rate"]))
        if _symbol_in_field(r, "bear", symbol):
            bear_count += 1
        if _symbol_in_field(r, "top5_margin_reduce_inst_buy", symbol):
            top5_appearances.append((r["date"], r["rate"]))

    last_top5_date = top5_appearances[-1][0] if top5_appearances else None
    last_top5_rate = top5_appearances[-1][1] if top5_appearances else None
    bull_avg_rate = (
        sum(rate for _, rate in bull_dates) / len(bull_dates)
        if bull_dates else None
    )

    return {
        "bull_count": len(bull_dates),
        "bear_count": bear_count,
        "top5_count": len(top5_appearances),
        "last_top5_date": last_top5_date,
        "last_top5_rate": last_top5_rate,
        "bull_avg_rate": bull_avg_rate,
    }


def _read_closes(
    prices: dict, ticker: str, end_idx: int, n: int,
) -> list[float] | None:
    entry = prices.get("prices", {}).get(ticker)
    if not entry:
        return None
    start = entry["start"]
    csv = entry["csv"].split(",")

    closes: list[float] = []
    for i in range(end_idx - n + 1, end_idx + 1):
        if i < start or (i - start) >= len(csv):
            return None
        try:
            closes.append(float(csv[i - start]))
        except (ValueError, IndexError):
            return None
    return closes


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def price_features(
    ticker: str, d: str, prices: dict, twii: dict, twii_dates: list[str],
    *, window: int = 20,
) -> dict | None:
    """
    對 ticker 在 d 之前 window 個交易日的價格特徵。
    缺價 / 找不到 ticker / window 不足 → None。

    回傳:
      window_start, window_end       回看窗的起訖日
      closes                         window 個收盤(時序)
      ma5, ma20, ma_window           短中長期均線
      return_window                  window 個交易日的累積報酬
      twii_return_window             同期 TWII 報酬
      excess_return_window           股票報酬 - TWII 報酬
    """
    dates = prices.get("dates", [])
    end_idx = -1
    for i, dt in enumerate(dates):
        if dt < d:
            end_idx = i
        else:
            break
    if end_idx < window - 1:
        return None

    closes = _read_closes(prices, ticker, end_idx, window)
    if closes is None:
        return None

    ma5 = _mean(closes[-5:]) if len(closes) >= 5 else _mean(closes)
    ma20 = _mean(closes[-20:]) if len(closes) >= 20 else _mean(closes)
    ma_window = _mean(closes)

    return_window = (closes[-1] / closes[0]) - 1 if closes[0] else 0.0

    window_start = dates[end_idx - window + 1]
    window_end = dates[end_idx]

    twii_start_v = twii.get(window_start)
    twii_end_v = twii.get(window_end)
    if twii_start_v and twii_end_v:
        twii_return_window = (twii_end_v / twii_start_v) - 1
    else:
        twii_return_window = 0.0
    excess_return_window = return_window - twii_return_window

    return {
        "window_start": window_start,
        "window_end": window_end,
        "closes": closes,
        "ma5": ma5,
        "ma20": ma20,
        "ma_window": ma_window,
        "return_window": return_window,
        "twii_return_window": twii_return_window,
        "excess_return_window": excess_return_window,
    }
