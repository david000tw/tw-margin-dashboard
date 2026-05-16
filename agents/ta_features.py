"""
TradingAgents-lite feature 預取層:每個 function 嚴格 walk-forward,
只讀 record.date < d 的資料。所有 feature 都是純函式,易於測試。

agents/ta_runner.py 在組 prompt 前先呼叫 collect() 拿到 SymbolFeatures。
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _records_before(merged: list[dict], d: str, window: int) -> list[dict]:
    """回傳 < d 的最近 window 筆 record(時序排序)。"""
    earlier = [r for r in merged if r["date"] < d]
    return earlier[-window:]


def _symbol_in_field(record: dict, field_name: str, symbol: str) -> bool:
    """symbol 是否出現在 record[field_name](逗號分隔)中。處理 '*' / 空字串。"""
    val = record.get(field_name) or ""
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


def _ema(values: list[float], period: int) -> list[float]:
    """
    指數移動平均(EMA),回傳與 values 等長的 list。
    首值用 values[0] 起算,確保不同 period 的 EMA 序列 index 對齊
    (因此 zip(ema12, ema26) 算 DIF 不會錯位)。
    """
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _macd(closes: list[float]) -> tuple[float, float, float] | None:
    """
    MACD (12, 26, 9):
      DIF = EMA12 - EMA26
      Signal = EMA9 of DIF
      Hist = DIF - Signal
    需要 >= 35 個 close 才有意義(26 EMA 收斂 + 9 signal EMA)。
    回傳最後一日 (dif, signal, hist),不足回 None。
    """
    if len(closes) < 35:
        return None
    ema12_seq = _ema(closes, 12)
    ema26_seq = _ema(closes, 26)
    dif_seq = [a - b for a, b in zip(ema12_seq, ema26_seq)]
    signal_seq = _ema(dif_seq, 9)
    dif = dif_seq[-1]
    signal = signal_seq[-1]
    return dif, signal, dif - signal


# ── OHLCV 指標 helpers (Task 4) ──────────────────────────────


def _atr(
    highs: list[float], lows: list[float], closes: list[float],
    period: int = 14,
) -> float | None:
    """Average True Range,需要 highs/lows/closes 各 >= period+1 個元素。
    TR = max(high-low, |high-prev_close|, |low-prev_close|)。
    """
    if len(highs) < period or len(lows) < period or len(closes) < period:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # 取最後 period 個 TR 平均
    return sum(trs[-period:]) / period


def _gap_count(
    opens: list[float], closes: list[float], threshold: float = 0.005,
) -> int:
    """窗內跳空次數(open vs 昨日 close 偏離 > threshold)。
    threshold 預設 0.5%。"""
    count = 0
    for i in range(1, len(opens)):
        prev_close = closes[i - 1]
        if prev_close == 0:
            continue
        deviation = abs(opens[i] - prev_close) / prev_close
        if deviation > threshold:
            count += 1
    return count


def _vol_ratio(
    volumes: list[float],
) -> tuple[float | None, float | None, float | None]:
    """回 (avg5, avg20, ratio)。需要 >= 20 個元素,不足回 (None, None, None)。"""
    if len(volumes) < 20:
        return None, None, None
    avg5 = sum(volumes[-5:]) / 5
    avg20 = sum(volumes[-20:]) / 20
    ratio = avg5 / avg20 if avg20 > 0 else None
    return avg5, avg20, ratio


def _candle_pattern(
    open_: float, high: float, low: float, close: float,
) -> str | None:
    """單根 K 線型態識別:錘頭 / 十字 / None。

    錘頭(hammer): 實體 < 30% 全長 + 下影線 > 60% 全長 + 收紅(close > open)
    十字(doji): 實體 < 10% 全長
    其他: None
    """
    total_range = high - low
    if total_range <= 0:
        return None
    body = abs(close - open_)
    body_pct = body / total_range

    if body_pct < 0.10:
        return "十字"

    lower_shadow = min(open_, close) - low
    lower_shadow_pct = lower_shadow / total_range

    if body_pct < 0.30 and lower_shadow_pct > 0.60 and close > open_:
        return "錘頭"

    return None


def price_features(
    ticker: str, d: str, prices: dict, twii: dict[str, float],
    *, window: int = 60, pg_adapter=None,
) -> dict | None:
    """
    對 ticker 在 d 之前 window 個交易日的價格特徵。

    嘗試從 pg_adapter 拉 OHLCV (若提供)。失敗 / 缺 → fallback close-only。

    回傳 (見既有 docstring 加上):
      window_start, window_end       回看窗的起訖日
      closes                         window 個收盤(時序)
      ma5, ma20, ma_window           短中長期均線
      return_window                  window 個交易日的累積報酬
      twii_return_window             同期 TWII 報酬(anchor 缺 → None)
      excess_return_window           股票報酬 - TWII 報酬(anchor 缺 → None)
      bias_ma20                      月均線乖離 % = (close - ma20)/ma20 * 100
      macd_dif, macd_signal, macd_hist   MACD (12,26,9), 不足 35 日 → None
      ohlcv_available    True=用 PG OHLCV 路徑; False=用既有 close-only
      atr14              ATR(14), len<14 回 None
      atr_pct_of_close   ATR / 現價 * 100
      gap_count_window   窗內跳空次數
      vol_avg_5, vol_avg_20, vol_ratio_5_20
      candle_pattern     最後一根 K 線型態
    """
    # 先嘗試 OHLCV 路徑
    if pg_adapter is not None:
        try:
            result = _price_features_from_ohlcv(
                ticker, d, pg_adapter, twii, window=window, prices=prices,
            )
            if result is not None:
                return result
        except Exception:
            pass  # PG 失敗 → fallback close-only

    # Fallback: close-only (既有邏輯)
    result = _price_features_close_only(ticker, d, prices, twii, window=window)
    if result is None:
        return None
    # 補上 OHLCV-only 欄位 = None
    result.update({
        "ohlcv_available": False,
        "atr14": None,
        "atr_pct_of_close": None,
        "gap_count_window": None,
        "vol_avg_5": None,
        "vol_avg_20": None,
        "vol_ratio_5_20": None,
        "candle_pattern": None,
    })
    return result


def _price_features_close_only(
    ticker: str, d: str, prices: dict, twii: dict[str, float],
    *, window: int = 60,
) -> dict | None:
    """既有 close-only 邏輯,從 prices dict 拉。
    把原 price_features 整個 function body 搬進來,return 整個 dict
    (不要包 ohlcv_available 那些 None 欄位,留給 wrapper 補)。"""
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
    twii_return_window: float | None
    excess_return_window: float | None
    if twii_start_v and twii_end_v:
        twii_return_window = (twii_end_v / twii_start_v) - 1
        excess_return_window = return_window - twii_return_window
    else:
        twii_return_window = None
        excess_return_window = None

    bias_ma20 = ((closes[-1] - ma20) / ma20 * 100) if ma20 else None

    macd = _macd(closes)
    macd_dif: float | None
    macd_signal: float | None
    macd_hist: float | None
    if macd is None:
        macd_dif = macd_signal = macd_hist = None
    else:
        macd_dif, macd_signal, macd_hist = macd

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
        "bias_ma20": bias_ma20,
        "macd_dif": macd_dif,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
    }


def _price_features_from_ohlcv(
    ticker: str, d: str, pg_adapter, twii: dict[str, float],
    *, window: int = 60, prices: dict,
) -> dict | None:
    """OHLCV 路徑:從 PG 拉 OHLCV 算所有指標。
    若 PG 沒夠資料 → 回 None 讓上層 fallback。"""
    from datetime import datetime, timedelta
    end_dt = datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)
    start_dt = end_dt - timedelta(days=int(window * 1.5))   # 多抓避免遇假日
    ohlcv = pg_adapter.get_ohlcv(
        ticker, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"),
    )
    if ohlcv.empty or len(ohlcv) < window:
        return None    # 不足 → fallback

    ohlcv = ohlcv.tail(window).reset_index(drop=True)
    opens   = ohlcv["open"].tolist()
    highs   = ohlcv["high"].tolist()
    lows    = ohlcv["low"].tolist()
    closes  = ohlcv["close"].tolist()
    volumes = [float(v) for v in ohlcv["volume"].tolist()]

    # 既有指標 (從 closes 算)
    ma5 = _mean(closes[-5:]) if len(closes) >= 5 else _mean(closes)
    ma20 = _mean(closes[-20:]) if len(closes) >= 20 else _mean(closes)
    ma_window = _mean(closes)
    return_window = (closes[-1] / closes[0]) - 1 if closes[0] else 0.0
    window_start = str(ohlcv["date"].iloc[0])
    window_end = str(ohlcv["date"].iloc[-1])
    bias_ma20 = ((closes[-1] - ma20) / ma20 * 100) if ma20 else None
    macd = _macd(closes)
    macd_dif, macd_signal, macd_hist = (
        macd if macd is not None else (None, None, None)
    )

    twii_start_v = twii.get(window_start)
    twii_end_v = twii.get(window_end)
    twii_return_window: float | None
    excess_return_window: float | None
    if twii_start_v and twii_end_v:
        twii_return_window = (twii_end_v / twii_start_v) - 1
        excess_return_window = return_window - twii_return_window
    else:
        twii_return_window = None
        excess_return_window = None

    # OHLCV-only 新指標
    atr14 = _atr(highs, lows, closes, period=14)
    atr_pct = (atr14 / closes[-1] * 100) if (atr14 is not None and closes[-1]) else None
    gap_count = _gap_count(opens, closes, threshold=0.005)
    vol_avg_5, vol_avg_20, vol_ratio = _vol_ratio(volumes)
    last_pattern = _candle_pattern(
        open_=opens[-1], high=highs[-1], low=lows[-1], close=closes[-1],
    )

    return {
        "window_start": window_start,
        "window_end": window_end,
        "closes": closes,
        "ma5": ma5, "ma20": ma20, "ma_window": ma_window,
        "return_window": return_window,
        "twii_return_window": twii_return_window,
        "excess_return_window": excess_return_window,
        "bias_ma20": bias_ma20,
        "macd_dif": macd_dif, "macd_signal": macd_signal, "macd_hist": macd_hist,
        "ohlcv_available": True,
        "atr14": atr14,
        "atr_pct_of_close": atr_pct,
        "gap_count_window": gap_count,
        "vol_avg_5": vol_avg_5, "vol_avg_20": vol_avg_20,
        "vol_ratio_5_20": vol_ratio,
        "candle_pattern": last_pattern,
    }


def past_perf(symbol: str, d: str, prediction_rows: list[dict]) -> dict:
    """
    從 ai_predictions.jsonl rows(含 prediction + outcome 混合)統計
    symbol 在 d 之前被推薦過幾次、勝率。

    回傳:
      long_count        該 symbol 過去在 long list 中出現次數
      long_win_count    對應 outcome 中 long_win=True 的次數
      short_count, short_win_count  同上,short 邊
    """
    sym = symbol.rstrip("*")
    outcomes: dict[str, dict] = {}
    for r in prediction_rows:
        if r.get("type") != "outcome":
            continue
        if r.get("date", "") >= d:
            continue
        prev = outcomes.get(r["date"])
        if prev is None or r["horizon"] > prev["horizon"]:
            outcomes[r["date"]] = r

    long_count = long_win = short_count = short_win = 0
    for r in prediction_rows:
        if r.get("type") != "prediction":
            continue
        if r.get("date", "") >= d:
            continue
        out = outcomes.get(r["date"])
        long_syms = [e.get("symbol", "").rstrip("*") for e in r.get("long", [])]
        short_syms = [e.get("symbol", "").rstrip("*") for e in r.get("short", [])]
        if sym in long_syms:
            long_count += 1
            if out and out.get("long_win"):
                long_win += 1
        if sym in short_syms:
            short_count += 1
            if out and out.get("short_win"):
                short_win += 1

    return {
        "long_count": long_count,
        "long_win_count": long_win,
        "short_count": short_count,
        "short_win_count": short_win,
    }


# ── Market context (近 N 天 merged + TWII 趨勢) ─────────────────

def market_context(
    d: str, merged: list[dict], twii: dict[str, float], *, n_recent: int = 30,
) -> dict:
    """近 n_recent 個交易日的 merged 摘要 + TWII 起訖。所有 record.date < d。"""
    recent = _records_before(merged, d, n_recent)
    twii_dates = sorted([k for k in twii.keys() if k < d])[-n_recent:]
    twii_summary = None
    if twii_dates:
        first, last = twii_dates[0], twii_dates[-1]
        twii_summary = {
            "first_date": first,
            "last_date": last,
            "first_value": twii[first],
            "last_value": twii[last],
            "return_pct": (twii[last] / twii[first] - 1) * 100,
        }
    return {
        "recent_records": recent,
        "twii": twii_summary,
    }


# ── 整合 ───────────────────────────────────────────────────────

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


def collect(
    *, symbol: str, ticker: str, d: str,
    merged: list[dict], prices: dict, twii: dict[str, float],
    prediction_rows: list[dict],
    chip_window: int = 60, price_window: int = 60, market_window: int = 30,
    lessons: list[dict] | None = None,
    pg_adapter=None,
) -> SymbolFeatures:
    """組裝單一 symbol 在 d 的完整 feature。嚴格 walk-forward。
    pg_adapter: 若提供,price_features 嘗試從 PG 拉 OHLCV 算 ATR/跳空 等。"""
    return SymbolFeatures(
        symbol=symbol,
        ticker=ticker,
        target_date=d,
        chip=chip_features(symbol, d, merged, window=chip_window),
        price=price_features(
            ticker, d, prices, twii, window=price_window, pg_adapter=pg_adapter,
        ),
        past_perf=past_perf(symbol, d, prediction_rows),
        market_context=market_context(d, merged, twii, n_recent=market_window),
        lessons=lessons or [],
    )
