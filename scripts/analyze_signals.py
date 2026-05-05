"""
歷史訊號驗證 + 篩選機制 + 回測產出

跑一次,產出:
  data/backtest_summary.json                    給 dashboard fetch
  reports/symbol_stats.csv                      ~7800 列個股×訊號×horizon(gitignore)
  reports/signal_validation_YYYY-MM-DD.md       人讀快照(commit)
  reports/per_sample.csv                        ~24 萬筆逐樣本明細(僅 --dump-samples 才產出)

關鍵設計:
  - 計價邏輯 read_price / get_close_on_or_before / get_close_n_days_later 是
    這個 codebase 內唯一的實作;dashboard 早期版本曾在 JS 端做類似事但已重寫成
    讀 backtest_summary.json,所以 Python 邏輯不再需要與 JS 同步
  - Train/test 嚴格分離:訓練窗 2021-01-01 ~ 2024-12-31 篩選 → 測試窗 2025-01-01 ~
    今 評估,grid search 用 test 窗 abs Sharpe 校準
  - 不用 GA;規則式 + grid search 對 1182 天樣本量已足夠

詳見 docs/SIGNAL_ANALYSIS.md
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

BASE       = Path(__file__).resolve().parent.parent
DATA       = BASE / "data"
REPORTS    = BASE / "reports"
MERGED     = DATA / "all_data_merged.json"
TWII       = DATA / "twii_all.json"
PRICES     = DATA / "stock_prices.json"
FETCH_LOG  = DATA / "stock_fetch_log.json"
SYMBOL_IDX = DATA / "symbol_index.json"
SUMMARY    = DATA / "backtest_summary.json"

# 共享 helpers(canonical 來源:scripts/symbol_resolve.py + pipeline.py)
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import load_json, save_json   # type: ignore[import-not-found]
from symbol_resolve import (                 # type: ignore[import-not-found]
    PRIMARY_HORIZON, SIDE_CONFIG, SIDE_FIELDS as SIDE_FIELD,
    split_names,
)

HORIZONS   = [1, 5, 10, PRIMARY_HORIZON, 60]
SIDES      = list(SIDE_CONFIG.keys())

# 訓練/測試窗(嚴格分離,grid search 用 test 窗 Sharpe 校準)
TRAIN_START = "2021-01-01"
TRAIN_END   = "2024-12-31"
TEST_START  = "2025-01-01"
TEST_END    = "2026-12-31"   # open-ended,實際取到 merged 最末日

# 篩選 grid (Phase B3)
PARAM_GRID = {
    "min_n":          [3, 5, 10, 20],
    "min_avg_excess": [0.01, 0.02, 0.03, 0.05],
    "min_win_rate":   [0.50, 0.55, 0.60],
    "min_t_stat":     [1.65, 1.96, 2.58],
    "min_recent_n":   [0, 1, 3],
}

PRESET_LOOSE = {
    "min_n": 3, "min_avg_excess": 0.01, "min_win_rate": 0.50,
    "min_t_stat": 1.65, "min_recent_n": 0,
}
PRESET_STRICT = {
    "min_n": 10, "min_avg_excess": 0.03, "min_win_rate": 0.55,
    "min_t_stat": 2.58, "min_recent_n": 1,
}


# ── helpers (price/twii lookup,測試覆蓋) ──────────────────────

def split_names(s: str) -> list[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def find_idx_on_or_before(dates: list[str], target: str) -> int:
    """
    對應 dashboard getCloseOnOrBefore 的尋址段:
      若 target 在 dates 裡 → 回該 idx
      否則 → 回 ≤ target 的最大 idx;若都比 target 大 → -1
    """
    try:
        return dates.index(target)
    except ValueError:
        i = len(dates) - 1
        while i >= 0 and dates[i] > target:
            i -= 1
        return i


def read_price(price_entry: dict, dates_idx: int) -> float | None:
    """對應 dashboard readPrice。空字串/越界回 None。"""
    if dates_idx < 0:
        return None
    i = dates_idx - price_entry["start"]
    if i < 0:
        return None
    cells = price_entry.get("_cells")
    if cells is None:
        cells = price_entry["csv"].split(",")
        price_entry["_cells"] = cells   # 快取,避免每次 split
    if i >= len(cells):
        return None
    v = cells[i]
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def get_close_on_or_before(prices: dict, ticker: str, target: str) -> float | None:
    p = prices["prices"].get(ticker)
    if p is None:
        return None
    idx = find_idx_on_or_before(prices["dates"], target)
    return read_price(p, idx)


def step_forward(dates: list, base_idx: int, n: int) -> int:
    """從 base_idx 前進 n 個元素,夾在末尾。共用於股價與 TWII 的 N 日後尋址。"""
    j = base_idx
    cnt = 0
    while cnt < n and j < len(dates) - 1:
        j += 1
        cnt += 1
    return j


def get_close_at_idx(prices: dict, ticker: str, dates_idx: int) -> float | None:
    """已知 prices.dates 上的 idx 時直接取價(避免重複 find_idx_on_or_before)。"""
    p = prices["prices"].get(ticker)
    if p is None:
        return None
    return read_price(p, dates_idx)


def get_close_n_days_later(prices: dict, ticker: str, target: str, n: int) -> float | None:
    """對應 dashboard getCloseNDaysLater:從 target 起在 prices.dates 索引中前進 N 個元素。"""
    p = prices["prices"].get(ticker)
    if p is None:
        return None
    dates = prices["dates"]
    base_idx = find_idx_on_or_before(dates, target)
    if base_idx < 0:
        return None
    j = base_idx
    cnt = 0
    while cnt < n and j < len(dates) - 1:
        j += 1
        cnt += 1
    return read_price(p, j)


def twii_return(twii: dict[str, float], twii_dates: list[str], target: str, n: int) -> float | None:
    """
    對應 dashboard twiiReturn:目標日不在 twii_dates 直接 None(不找前一日,
    刻意保留與 dashboard 既有行為一致)。
    """
    try:
        i = twii_dates.index(target)
    except ValueError:
        return None
    base = twii.get(target)
    if not base:
        return None
    j = i
    cnt = 0
    while cnt < n and j < len(twii_dates) - 1:
        j += 1
        cnt += 1
    later = twii.get(twii_dates[j])
    if not later:
        return None
    return later / base - 1


def compute_excess_return(p0: float, pN: float, twii0: float, twiiN: float) -> float:
    """個股報酬 - TWII 報酬。"""
    stock_ret = pN / p0 - 1
    twii_ret  = twiiN / twii0 - 1
    return stock_ret - twii_ret


# ── Layer 1: per-sample table ────────────────────────────────

@dataclass
class Sample:
    date:    str
    side:    str
    symbol:  str
    ticker:  str
    horizon: int
    p0:      float
    pN:      float
    ret:     float
    twii_ret: float
    excess_ret: float


def build_per_sample_table(
    merged: list[dict],
    twii:   dict[str, float],
    prices: dict,
    sym2t:  dict[str, str],
) -> list[Sample]:
    """
    每個 record 的 (price base_idx, twii base_idx, 各 horizon 的 forward idx) 都在
    record loop 開頭算一次,內層 ticker × horizon 直接 idx 查表,避免 1300×5 次線性 scan。
    """
    twii_dates = sorted(twii.keys())
    price_dates = prices["dates"]
    out: list[Sample] = []

    # twii_ret 對 (date, h) 的快取(同日子常被多檔股票共用)
    twii_ret_cache: dict[tuple[str, int], float | None] = {}

    for r in merged:
        d = r["date"]
        twii0 = twii.get(d)
        if twii0 is None:
            continue

        # Step A:對該日期算一次 price/twii base_idx,以及每個 horizon 的 forward idx
        price_base = find_idx_on_or_before(price_dates, d)
        if price_base < 0:
            continue
        price_h_idx = {h: step_forward(price_dates, price_base, h) for h in HORIZONS}

        try:
            twii_base = twii_dates.index(d)
        except ValueError:
            continue
        twii_h_idx = {h: step_forward(twii_dates, twii_base, h) for h in HORIZONS}

        # 該日期下各 horizon 的 twii 報酬一次算完,內層 ticker 共用
        twii_rets: dict[int, float | None] = {}
        for h in HORIZONS:
            key = (d, h)
            if key in twii_ret_cache:
                twii_rets[h] = twii_ret_cache[key]
                continue
            later = twii.get(twii_dates[twii_h_idx[h]])
            twii_rets[h] = (later / twii0 - 1) if (twii0 and later) else None
            twii_ret_cache[key] = twii_rets[h]

        for side in SIDES:
            for sym in split_names(r.get(SIDE_FIELD[side], "")):
                ticker = sym2t.get(sym) or sym2t.get(sym.rstrip("*"))
                if ticker is None:
                    continue

                p0 = get_close_at_idx(prices, ticker, price_base)
                if not p0:   # None 或 0
                    continue

                for h in HORIZONS:
                    pN = get_close_at_idx(prices, ticker, price_h_idx[h])
                    if pN is None:
                        continue
                    tret = twii_rets[h]
                    if tret is None:
                        continue
                    sret = pN / p0 - 1
                    out.append(Sample(
                        date=d, side=side, symbol=sym, ticker=ticker, horizon=h,
                        p0=p0, pN=pN, ret=sret, twii_ret=tret, excess_ret=sret - tret,
                    ))
    return out


def write_samples_csv(path: Path, samples: list[Sample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date","side","symbol","ticker","horizon","p0","pN","ret","twii_ret","excess_ret"])
        for s in samples:
            w.writerow([s.date, s.side, s.symbol, s.ticker, s.horizon,
                        f"{s.p0:.4f}", f"{s.pN:.4f}",
                        f"{s.ret:.6f}", f"{s.twii_ret:.6f}", f"{s.excess_ret:.6f}"])


# ── Layer 2: symbol × signal × horizon stats ─────────────────

@dataclass
class SymbolStat:
    symbol: str
    side:   str
    horizon: int
    n:                int = 0
    avg_excess:       float = 0.0
    win_rate:         float = 0.0
    std:              float = 0.0
    t_stat:           float = 0.0
    recent_n:         int = 0
    recent_avg_excess: float = 0.0
    train_n:          int = 0
    train_avg:        float = 0.0
    train_t:          float = 0.0
    train_winrate:    float = 0.0
    test_n:           int = 0
    test_avg:         float = 0.0
    test_t:           float = 0.0
    test_winrate:     float = 0.0


def _stats_of(values: list[float]) -> tuple[int, float, float, float, float]:
    """returns (n, avg, std, t_stat, win_rate). n=0 → 全 0。"""
    n = len(values)
    if n == 0:
        return 0, 0.0, 0.0, 0.0, 0.0
    avg = sum(values) / n
    if n < 2:
        return n, avg, 0.0, 0.0, 1.0 if avg > 0 else 0.0
    var = sum((v - avg) ** 2 for v in values) / (n - 1)
    std = math.sqrt(var)
    t   = avg / (std / math.sqrt(n)) if std > 0 else 0.0
    win = sum(1 for v in values if v > 0) / n
    return n, avg, std, t, win


def recent_window_lo(today: str) -> str:
    """近 1 年(365 天)的下界,以 ISO 日期字串回傳。今天 - 365 天的形式,
    用字串比對(s.date >= recent_lo)避免 hot loop 內反覆 strptime。"""
    today_d = datetime.strptime(today, "%Y-%m-%d").date()
    return (today_d - timedelta(days=365)).isoformat()


def compute_symbol_stats(samples: list[Sample], today: str) -> list[SymbolStat]:
    idx: dict[tuple[str, str, int], list[Sample]] = {}
    for s in samples:
        idx.setdefault((s.symbol, s.side, s.horizon), []).append(s)

    recent_lo = recent_window_lo(today)

    stats: list[SymbolStat] = []
    for (sym, side, h), group in idx.items():
        all_excess = [s.excess_ret for s in group]
        n, avg, std, t, win = _stats_of(all_excess)

        recent_excess = [s.excess_ret for s in group if s.date >= recent_lo]
        rn, ravg, _, _, _ = _stats_of(recent_excess)

        train_excess = [s.excess_ret for s in group if TRAIN_START <= s.date <= TRAIN_END]
        tn, tavg, _, tt, twin = _stats_of(train_excess)

        test_excess  = [s.excess_ret for s in group if TEST_START  <= s.date <= TEST_END]
        en, eavg, _, et, ewin = _stats_of(test_excess)

        stats.append(SymbolStat(
            symbol=sym, side=side, horizon=h,
            n=n, avg_excess=avg, win_rate=win, std=std, t_stat=t,
            recent_n=rn, recent_avg_excess=ravg,
            train_n=tn, train_avg=tavg, train_t=tt, train_winrate=twin,
            test_n=en, test_avg=eavg, test_t=et, test_winrate=ewin,
        ))
    return stats


def write_symbol_stats_csv(path: Path, stats: list[SymbolStat]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "symbol","side","horizon",
            "n","avg_excess","win_rate","std","t_stat",
            "recent_n","recent_avg_excess",
            "train_n","train_avg","train_t","train_winrate",
            "test_n","test_avg","test_t","test_winrate",
        ])
        for s in stats:
            w.writerow([
                s.symbol, s.side, s.horizon,
                s.n, f"{s.avg_excess:.6f}", f"{s.win_rate:.4f}", f"{s.std:.6f}", f"{s.t_stat:.4f}",
                s.recent_n, f"{s.recent_avg_excess:.6f}",
                s.train_n, f"{s.train_avg:.6f}", f"{s.train_t:.4f}", f"{s.train_winrate:.4f}",
                s.test_n, f"{s.test_avg:.6f}", f"{s.test_t:.4f}", f"{s.test_winrate:.4f}",
            ])


# ── Layer 3: signal-level summary ────────────────────────────

def _aggregate_excess(samples: Iterable[Sample], date_lo: str = "", date_hi: str = "9999") -> dict:
    """同 side × horizon 把所有 excess 聚合,回 {by_horizon:{h:{n,avg,t,winrate}}}"""
    by_h: dict[int, list[float]] = {h: [] for h in HORIZONS}
    syms: set[str] = set()
    for s in samples:
        if not (date_lo <= s.date <= date_hi):
            continue
        by_h[s.horizon].append(s.excess_ret)
        syms.add(s.symbol)
    out = {"n_symbols": len(syms), "by_horizon": {}}
    for h, vals in by_h.items():
        n, avg, std, t, win = _stats_of(vals)
        out["by_horizon"][str(h)] = {
            "n": n, "avg_excess": round(avg, 6), "win_rate": round(win, 4),
            "t_stat": round(t, 4), "std": round(std, 6),
        }
    return out


# ── Filtering + grid search ──────────────────────────────────

def is_effective_signal(stat: SymbolStat, params: dict) -> bool:
    """
    判斷一個 (symbol, side, horizon) 是否被選入篩選池。

    用 SIDE_CONFIG['sign'] 驅動方向(bull/top5: +1、bear: -1):
      - sign * train_avg >= min_avg_excess     (bear 等價於 train_avg <= -threshold)
      - effective_winrate >= min_win_rate      (bear 為敗率 = 1 - train_winrate)
    """
    if stat.train_n < params["min_n"]:
        return False
    if abs(stat.train_t) < params["min_t_stat"]:
        return False
    if stat.recent_n < params["min_recent_n"]:
        return False
    sign = SIDE_CONFIG[stat.side]["sign"]
    if sign * stat.train_avg < params["min_avg_excess"]:
        return False
    effective_winrate = stat.train_winrate if sign > 0 else 1.0 - stat.train_winrate
    if effective_winrate < params["min_win_rate"]:
        return False
    return True


def evaluate_preset(
    stats: list[SymbolStat],
    samples: list[Sample],
    params: dict,
    side: str,
    horizon: int = PRIMARY_HORIZON,
) -> dict:
    """
    對單一 side 的 stats 用 train 統計篩 → test 窗等權績效。
    保留 stand-alone 介面供 test 與外部 call;hot loop 改走
    `_evaluate_with_buckets`(grid_search 預先 bucket samples 的 fast path)。
    """
    side_h_stats = [st for st in stats if st.side == side and st.horizon == horizon]
    test_buckets = _build_test_buckets(samples, side, horizon)
    return _evaluate_with_buckets(side_h_stats, test_buckets, params, side)


def _build_test_buckets(
    samples: list[Sample], side: str, horizon: int,
) -> dict[str, list[float]]:
    """{symbol → list[excess_ret]} for given side+horizon, only test window."""
    buckets: dict[str, list[float]] = {}
    for s in samples:
        if (s.side == side and s.horizon == horizon
                and TEST_START <= s.date <= TEST_END):
            buckets.setdefault(s.symbol, []).append(s.excess_ret)
    return buckets


def _evaluate_with_buckets(
    side_h_stats: list[SymbolStat],
    test_buckets: dict[str, list[float]],
    params: dict,
    side: str,
) -> dict:
    selected = {st.symbol for st in side_h_stats if is_effective_signal(st, params)}
    if not selected:
        return {"params": params, "side": side, "n_symbols": 0,
                "test_n": 0, "test_avg": 0.0, "test_sharpe": 0.0, "test_winrate": 0.0}
    test_excess: list[float] = []
    for sym in selected:
        test_excess.extend(test_buckets.get(sym, []))
    n, avg, std, _, win = _stats_of(test_excess)
    sharpe = abs(avg) / std if std > 0 else 0.0
    return {
        "params": params, "side": side,
        "n_symbols": len(selected),
        "test_n": n, "test_avg": round(avg, 6), "test_winrate": round(win, 4),
        "test_sharpe": round(sharpe, 4),
    }


def _iter_grid() -> Iterable[dict]:
    for n_ in PARAM_GRID["min_n"]:
        for ae in PARAM_GRID["min_avg_excess"]:
            for wr in PARAM_GRID["min_win_rate"]:
                for ts in PARAM_GRID["min_t_stat"]:
                    for rn in PARAM_GRID["min_recent_n"]:
                        yield {"min_n": n_, "min_avg_excess": ae,
                               "min_win_rate": wr, "min_t_stat": ts,
                               "min_recent_n": rn}


def grid_search_thresholds(stats: list[SymbolStat], samples: list[Sample]) -> dict:
    """
    每個 side 各搜尋 PARAM_GRID,用 test 窗 abs Sharpe 排序選最佳。

    優化:per-side 預先 bucket samples → 每個 grid combo 只看 selected ~10 個
    symbol 的預算 list,不再對 240k samples 全 scan。原本 432×3×240k = 300M
    次比較,降到 ~1k 次 dict 查表。
    """
    combos = list(_iter_grid())
    by_side: dict[str, dict] = {}
    for side in SIDES:
        side_h_stats = [st for st in stats
                        if st.side == side and st.horizon == PRIMARY_HORIZON]
        test_buckets = _build_test_buckets(samples, side, PRIMARY_HORIZON)
        results = [_evaluate_with_buckets(side_h_stats, test_buckets, p, side)
                   for p in combos]
        valid = [r for r in results if r["test_n"] >= 10]
        valid.sort(key=lambda r: r["test_sharpe"], reverse=True)
        if valid:
            by_side[side] = {"best": valid[0]["params"], "top10": valid[:10]}
        else:
            by_side[side] = {"best": dict(PRESET_LOOSE), "top10": []}
    return {"by_side": by_side, "n_searched_per_side": len(combos)}


# ── Build summary JSON for dashboard ─────────────────────────

def build_signal_summary(
    samples: list[Sample], stats: list[SymbolStat], grid: dict, today: str,
) -> dict:
    """
    回傳完整 backtest_summary dict(已含 filtered_presets 與 recommended_thresholds),
    main 不需要做後續 patch。
    """
    recent_lo = recent_window_lo(today)

    by_side: dict[str, dict] = {}
    for side in SIDES:
        side_samples = [s for s in samples if s.side == side]
        by_side[side] = {
            "all":    _aggregate_excess(side_samples),
            "recent": _aggregate_excess(side_samples, date_lo=recent_lo),
            "train":  _aggregate_excess(side_samples, TRAIN_START, TRAIN_END),
            "test":   _aggregate_excess(side_samples, TEST_START,  TEST_END),
            "filtered_presets": _build_presets_for_side(side, stats, samples, grid, recent_lo),
        }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "horizons": HORIZONS,
        "windows": {
            "train":  [TRAIN_START, TRAIN_END],
            "test":   [TEST_START, today],
            "recent": [recent_lo, today],
        },
        "by_side": by_side,
        "recommended_thresholds": {s: grid["by_side"][s]["best"] for s in SIDES},
        "grid_search_top10_by_side": {s: grid["by_side"][s]["top10"] for s in SIDES},
    }


def _load_symbol_display() -> dict[str, str]:
    """讀 data/symbol_index.json 拿 {symbol → display}。不存在則回空 dict。"""
    try:
        idx = load_json(SYMBOL_IDX)
    except FileNotFoundError:
        return {}
    return {s: info.get("display", s) for s, info in idx.get("by_symbol", {}).items()}


def _build_presets_for_side(
    side: str, stats: list[SymbolStat], samples: list[Sample],
    grid: dict, recent_lo: str,
) -> dict:
    """單一 side 的 loose/recommended/strict 三組 preset 完整輸出。"""
    sign = SIDE_CONFIG[side]["sign"]
    side_stats = [st for st in stats if st.side == side]
    side_samples = [s for s in samples if s.side == side]
    display_map = _load_symbol_display()

    presets = {
        "loose":       dict(PRESET_LOOSE),
        "recommended": grid["by_side"][side]["best"],
        "strict":      dict(PRESET_STRICT),
    }
    out: dict[str, dict] = {}
    for name, params in presets.items():
        selected_pairs = {
            (st.symbol, st.horizon) for st in side_stats
            if is_effective_signal(st, params)
        }
        n_symbols = len({p[0] for p in selected_pairs})
        sel_samples = [s for s in side_samples
                       if (s.symbol, s.horizon) in selected_pairs]
        agg = _aggregate_excess(sel_samples)
        agg["params"] = params
        agg["n_symbols"] = n_symbols
        agg["test_window"]   = _aggregate_excess(sel_samples, TEST_START, TEST_END)
        agg["recent_window"] = _aggregate_excess(sel_samples, date_lo=recent_lo)

        # top20:按 sign * train_avg 降冪(bull/top5 取最正,bear 取最負)
        cand = [st for st in side_stats
                if st.horizon == PRIMARY_HORIZON and is_effective_signal(st, params)]
        cand.sort(key=lambda st: sign * st.train_avg, reverse=True)
        agg["symbols_top20"] = [
            {
                "symbol":  st.symbol,
                "display": display_map.get(st.symbol, st.symbol),
                "n": st.n,
                "avg_excess": round(st.avg_excess, 6),
                "win_rate":   round(st.win_rate, 4),
                "t_stat":     round(st.t_stat, 4),
                "recent_n":   st.recent_n,
                "train_avg":  round(st.train_avg, 6),
                "test_avg":   round(st.test_avg, 6),
            }
            for st in cand[:20]
        ]
        out[name] = agg
    return out


# 保留舊 build_presets 公開介面(test 與外部 caller 可能仍 import)
def build_presets(
    stats: list[SymbolStat], samples: list[Sample], grid: dict, today: str,
) -> dict:
    recent_lo = recent_window_lo(today)
    return {
        side: _build_presets_for_side(side, stats, samples, grid, recent_lo)
        for side in SIDES
    }


# ── Markdown report (commit) ─────────────────────────────────

def write_markdown_report(path: Path, summary: dict, grid: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 訊號驗證報告 — {summary['generated_at'][:10]}",
        "",
        f"訓練窗:`{summary['windows']['train'][0]}` ~ `{summary['windows']['train'][1]}`",
        f"測試窗:`{summary['windows']['test'][0]}` ~ `{summary['windows']['test'][1]}`",
        "",
        "## Grid search 結果 (per-side)",
        "",
        f"每 side 各搜尋 {grid['n_searched_per_side']} 組,test 窗 abs Sharpe 排序前 5:",
        "",
    ]
    for side in SIDES:
        side_grid = grid["by_side"][side]
        lines.append(f"### {side}")
        lines.append("")
        lines.append("| min_n | min_avg | min_win | min_t | min_rn | n_sym | test_n | test_avg | test_sharpe |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in side_grid["top10"][:5]:
            p = r["params"]
            lines.append(
                f"| {p['min_n']} | {p['min_avg_excess']} | {p['min_win_rate']} | "
                f"{p['min_t_stat']} | {p['min_recent_n']} | "
                f"{r['n_symbols']} | {r['test_n']} | {r['test_avg']:+.4f} | {r['test_sharpe']:+.4f} |"
            )
        lines.append("")
        lines.append(f"**推薦組**:`{side_grid['best']}`")
        lines.append("")
    lines += [
        "## 各 side T+20 表現",
        "",
        "| side | window | n | avg_excess | t_stat | win_rate |",
        "|---|---|---|---|---|---|",
    ]
    for side in SIDES:
        for win in ("all", "train", "test", "recent"):
            d = summary["by_side"][side][win]["by_horizon"].get("20", {})
            lines.append(
                f"| {side} | {win} | {d.get('n',0)} | "
                f"{d.get('avg_excess',0):+.4f} | {d.get('t_stat',0):+.2f} | "
                f"{d.get('win_rate',0):.3f} |"
            )

    lines += ["", "## 各 preset 篩選後 T+20 表現 (out-of-sample)", "",
              "| side | preset | n_sym | train_avg | train_n | **test_avg** | test_n |",
              "|---|---|---|---|---|---|---|"]
    for side in SIDES:
        for name in ("loose", "recommended", "strict"):
            d = summary["by_side"][side]["filtered_presets"].get(name, {})
            h_all  = d.get("by_horizon", {}).get("20", {})
            h_test = d.get("test_window", {}).get("by_horizon", {}).get("20", {})
            # train_avg 從全期 - test 推回去意義不大;取 stat layer 的全期當代理
            n_sym = d.get("n_symbols", 0)
            lines.append(
                f"| {side} | {name} | {n_sym} | "
                f"{h_all.get('avg_excess', 0):+.4f} | {h_all.get('n', 0)} | "
                f"**{h_test.get('avg_excess', 0):+.4f}** | {h_test.get('n', 0)} |"
            )

    lines += [
        "", "## 限制聲明", "",
        "- 不含交易成本(實單會差 0.5-1%/趟)",
        "- 等權持倉、不處理同檔多日上榜的重疊",
        "- 不做風險調整(VaR / drawdown 是事後算非事前濾)",
        "- 不考慮基本面/產業景氣",
        "",
        "詳見 `docs/SIGNAL_ANALYSIS.md`。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


# ── main ─────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dump-samples", action="store_true",
        help="額外寫 reports/per_sample.csv (~17 MB,平常不需要)",
    )
    args = ap.parse_args()

    missing = [p.name for p in (MERGED, TWII, PRICES, FETCH_LOG) if not p.exists()]
    if missing:
        print(f"[ERR] 缺少資料檔: {missing}", file=sys.stderr)
        return 1

    # 確保 symbol_index 最新(讓 top20 帶得到 display 名稱)
    try:
        from symbol_resolve import write_index as _write_symbol_index  # type: ignore[import-not-found]
        _write_symbol_index()
    except Exception as e:
        print(f"[WARN] symbol_index 更新失敗,top20 將顯示原 symbol: {e}", file=sys.stderr)

    print("[1/5] 載入資料...")
    merged = load_json(MERGED)
    twii   = {k: float(v) for k, v in load_json(TWII).items()}
    prices = load_json(PRICES)
    sym2t  = load_json(FETCH_LOG)["symbol_to_ticker"]
    print(f"  merged={len(merged)} 筆, twii={len(twii)} 天, prices.dates={len(prices['dates'])} 天 × {len(prices['prices'])} ticker")
    print(f"  symbol_to_ticker={len(sym2t)} 筆")

    today = max(r["date"] for r in merged)

    print("[2/5] 建 per-sample table...")
    samples = build_per_sample_table(merged, twii, prices, sym2t)
    print(f"  共 {len(samples):,} 筆樣本")
    if args.dump_samples:
        write_samples_csv(REPORTS / "per_sample.csv", samples)
        print(f"  → reports/per_sample.csv 已寫入")

    print("[3/5] 計算 symbol_stats...")
    stats = compute_symbol_stats(samples, today)
    print(f"  共 {len(stats):,} 列 (symbol × side × horizon)")
    write_symbol_stats_csv(REPORTS / "symbol_stats.csv", stats)

    print("[4/5] Grid search 校準 threshold (per-side)...")
    grid = grid_search_thresholds(stats, samples)
    for side in SIDES:
        print(f"  {side}: {grid['by_side'][side]['best']}")

    print("[5/5] 寫 backtest_summary.json + markdown 報告...")
    summary = build_signal_summary(samples, stats, grid, today)
    save_json(SUMMARY, summary)
    report_path = REPORTS / f"signal_validation_{today}.md"
    write_markdown_report(report_path, summary, grid)

    print(f"\n完成。")
    print(f"  data/backtest_summary.json    ({SUMMARY.stat().st_size/1024:.1f} KB)")
    print(f"  reports/symbol_stats.csv       ({(REPORTS / 'symbol_stats.csv').stat().st_size/1024:.1f} KB)")
    print(f"  {report_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
