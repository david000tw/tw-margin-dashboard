"""
歷史訊號驗證 + 篩選機制 + 回測產出

跑一次,產出:
  data/backtest_summary.json                    給 dashboard fetch
  reports/per_sample.csv                        ~17 萬筆逐樣本明細(gitignore)
  reports/symbol_stats.csv                      ~6000 列個股×訊號×horizon(gitignore)
  reports/signal_validation_YYYY-MM-DD.md       人讀快照(commit)

關鍵設計:
  - 計價邏輯與 dashboard_all.html:586-627 完全一致(getCloseOnOrBefore /
    getCloseNDaysLater) — Python 側照抄 JS,有 fixture test 守護(test_analyze_signals.py)
  - Train/test 嚴格分離:訓練窗 2021-01-01 ~ 2024-12-31 篩選 → 測試窗 2025-01-01 ~
    2026-04-30 評估,grid search 用 test 窗 Sharpe 校準
  - 不用 GA;規則式 + grid search 對 1182 天樣本量已足夠

詳見 docs/SIGNAL_ANALYSIS.md
"""
from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime
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

HORIZONS   = [1, 5, 10, 20, 60]
SIDES      = ["bull", "bear", "top5"]
SIDE_FIELD = {
    "bull": "bull",
    "bear": "bear",
    "top5": "top5_margin_reduce_inst_buy",
}

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
    twii_dates = sorted(twii.keys())
    out: list[Sample] = []

    for r in merged:
        d = r["date"]
        twii0 = twii.get(d)
        if twii0 is None:
            continue   # 沒大盤基準,整筆跳過

        for side in SIDES:
            for sym in split_names(r.get(SIDE_FIELD[side], "")):
                ticker = sym2t.get(sym)
                if ticker is None:
                    # 嘗試去掉 *(警示符號)再查
                    ticker = sym2t.get(sym.rstrip("*"))
                if ticker is None:
                    continue

                p0 = get_close_on_or_before(prices, ticker, d)
                if p0 is None or p0 == 0:
                    continue

                for h in HORIZONS:
                    pN = get_close_n_days_later(prices, ticker, d, h)
                    if pN is None:
                        continue
                    tret = twii_return(twii, twii_dates, d, h)
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


def compute_symbol_stats(samples: list[Sample], today: str) -> list[SymbolStat]:
    # 索引:(symbol, side, horizon) -> list[Sample]
    idx: dict[tuple[str, str, int], list[Sample]] = {}
    for s in samples:
        idx.setdefault((s.symbol, s.side, s.horizon), []).append(s)

    today_d = datetime.strptime(today, "%Y-%m-%d").date()

    stats: list[SymbolStat] = []
    for (sym, side, h), group in idx.items():
        all_excess = [s.excess_ret for s in group]
        n, avg, std, t, win = _stats_of(all_excess)

        recent_excess = [
            s.excess_ret for s in group
            if (today_d - datetime.strptime(s.date, "%Y-%m-%d").date()).days <= 365
        ]
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

    bear 訊號預期 excess 為負(預警下跌),所以方向與門檻都反向:
      - bull/top5:train_avg >= +min_avg_excess、train_winrate >= min_win_rate
      - bear:    train_avg <= -min_avg_excess、(1-train_winrate) >= min_win_rate
                                              ↑ 敗率 = 下跌率,bear 要這個高
    abs(train_t) 與 train_n / recent_n 三個門檻 side 共用。
    """
    if stat.train_n < params["min_n"]:
        return False
    if abs(stat.train_t) < params["min_t_stat"]:
        return False
    if stat.recent_n < params["min_recent_n"]:
        return False

    if stat.side == "bear":
        if stat.train_avg > -params["min_avg_excess"]:
            return False
        if (1.0 - stat.train_winrate) < params["min_win_rate"]:
            return False
    else:
        if stat.train_avg < params["min_avg_excess"]:
            return False
        if stat.train_winrate < params["min_win_rate"]:
            return False

    return True


def evaluate_preset(
    stats: list[SymbolStat],
    samples: list[Sample],
    params: dict,
    side: str,
    horizon: int = 20,
) -> dict:
    """
    對單一 side 的 stats 用 train 統計篩 → test 窗等權績效。
    Sharpe 取 abs(avg)/std(bear 負 alpha 越多代表訊號越強,排序時取絕對值)。
    """
    selected: set[str] = {
        st.symbol
        for st in stats
        if st.side == side and st.horizon == horizon and is_effective_signal(st, params)
    }
    if not selected:
        return {"params": params, "side": side, "n_symbols": 0,
                "test_n": 0, "test_avg": 0.0, "test_sharpe": 0.0, "test_winrate": 0.0}

    test_excess = [
        s.excess_ret for s in samples
        if s.side == side and s.horizon == horizon
        and s.symbol in selected
        and TEST_START <= s.date <= TEST_END
    ]
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
    """每個 side 各搜尋 PARAM_GRID,用 test 窗 abs Sharpe 排序選最佳。"""
    combos = list(_iter_grid())
    by_side: dict[str, dict] = {}
    for side in SIDES:
        results = [evaluate_preset(stats, samples, p, side=side, horizon=20) for p in combos]
        valid = [r for r in results if r["test_n"] >= 10]
        valid.sort(key=lambda r: r["test_sharpe"], reverse=True)
        if valid:
            by_side[side] = {"best": valid[0]["params"], "top10": valid[:10]}
        else:
            by_side[side] = {"best": dict(PRESET_LOOSE), "top10": []}
    return {"by_side": by_side, "n_searched_per_side": len(combos)}


# ── Build summary JSON for dashboard ─────────────────────────

def build_signal_summary(samples: list[Sample], stats: list[SymbolStat], today: str) -> dict:
    today_d = datetime.strptime(today, "%Y-%m-%d").date()
    recent_lo = (today_d.replace(year=today_d.year - 1)).strftime("%Y-%m-%d")

    _ = stats   # 留待未來把 stats 直接餵進 by_side(目前都從 samples 重算)
    by_side: dict[str, dict] = {}
    for side in SIDES:
        side_samples = [s for s in samples if s.side == side]
        by_side[side] = {
            "all":    _aggregate_excess(side_samples),
            "recent": _aggregate_excess(side_samples, date_lo=recent_lo),
            "train":  _aggregate_excess(side_samples, TRAIN_START, TRAIN_END),
            "test":   _aggregate_excess(side_samples, TEST_START,  TEST_END),
            "filtered_presets": {},   # 補 by build_presets
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
    }


def _load_symbol_display() -> dict[str, str]:
    """讀 data/symbol_index.json 拿 {symbol → display}。不存在則回空 dict。"""
    if not SYMBOL_IDX.exists():
        return {}
    idx = json.loads(SYMBOL_IDX.read_text(encoding="utf-8"))
    return {s: info.get("display", s) for s, info in idx.get("by_symbol", {}).items()}


def build_presets(stats: list[SymbolStat], samples: list[Sample], grid: dict, today: str) -> dict:
    """
    對 each side 套用三組 preset:loose / recommended (per-side grid 最佳) / strict。

    每組輸出:
      params            該 preset 的 5 個門檻值
      n_symbols         入選股數
      by_horizon        篩選後股票池全期 T+N 表現(dashboard 圖 1「filtered」線)
      test_window       篩選後在 test 窗 T+N 表現(dashboard 圖 1「test」線,真 out-of-sample)
      recent_window     篩選後近 1 年 T+N 表現
      symbols_top20     入選個股清單(bear 越負越前,其他越正越前)
    """
    today_d = datetime.strptime(today, "%Y-%m-%d").date()
    recent_lo = today_d.replace(year=today_d.year - 1).strftime("%Y-%m-%d")
    display_map = _load_symbol_display()

    out: dict[str, dict[str, dict]] = {side: {} for side in SIDES}
    for side in SIDES:
        presets = {
            "loose":       dict(PRESET_LOOSE),
            "recommended": grid["by_side"][side]["best"],
            "strict":      dict(PRESET_STRICT),
        }
        for name, params in presets.items():
            selected_pairs = {
                (st.symbol, st.horizon)
                for st in stats
                if st.side == side and is_effective_signal(st, params)
            }
            n_symbols = len(set(p[0] for p in selected_pairs))
            sel_samples = [
                s for s in samples
                if s.side == side and (s.symbol, s.horizon) in selected_pairs
            ]
            agg = _aggregate_excess(sel_samples)
            agg["params"] = params
            agg["n_symbols"] = n_symbols
            agg["test_window"]   = _aggregate_excess(sel_samples, TEST_START, TEST_END)
            agg["recent_window"] = _aggregate_excess(sel_samples, date_lo=recent_lo)

            cand = [
                st for st in stats
                if st.side == side and st.horizon == 20 and is_effective_signal(st, params)
            ]
            cand.sort(key=lambda st: st.train_avg, reverse=(side != "bear"))
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
            out[side][name] = agg
    return out


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
    if not all(p.exists() for p in (MERGED, TWII, PRICES, FETCH_LOG)):
        miss = [p.name for p in (MERGED, TWII, PRICES, FETCH_LOG) if not p.exists()]
        print(f"[ERR] 缺少資料檔: {miss}", file=sys.stderr)
        return 1

    # 確保 symbol_index 最新(讓 top20 帶得到 display 名稱)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from symbol_resolve import write_index as _write_symbol_index  # type: ignore[import-not-found]
        _write_symbol_index()
    except Exception as e:
        print(f"[WARN] symbol_index 更新失敗,top20 將顯示原 symbol: {e}", file=sys.stderr)

    print("[1/6] 載入資料...")
    merged = json.loads(MERGED.read_text(encoding="utf-8"))
    twii   = {k: float(v) for k, v in json.loads(TWII.read_text(encoding="utf-8")).items()}
    prices = json.loads(PRICES.read_text(encoding="utf-8"))
    sym2t  = json.loads(FETCH_LOG.read_text(encoding="utf-8"))["symbol_to_ticker"]
    print(f"  merged={len(merged)} 筆, twii={len(twii)} 天, prices.dates={len(prices['dates'])} 天 × {len(prices['prices'])} ticker")
    print(f"  symbol_to_ticker={len(sym2t)} 筆")

    today = max(r["date"] for r in merged)

    print("[2/6] 建 per-sample table...")
    samples = build_per_sample_table(merged, twii, prices, sym2t)
    print(f"  共 {len(samples):,} 筆樣本")

    print("[3/6] 寫 reports/per_sample.csv ...")
    write_samples_csv(REPORTS / "per_sample.csv", samples)

    print("[4/6] 計算 symbol_stats...")
    stats = compute_symbol_stats(samples, today)
    print(f"  共 {len(stats):,} 列 (symbol × side × horizon)")
    write_symbol_stats_csv(REPORTS / "symbol_stats.csv", stats)

    print("[5/6] Grid search 校準 threshold (per-side)...")
    grid = grid_search_thresholds(stats, samples)
    for side in SIDES:
        print(f"  {side}: {grid['by_side'][side]['best']}")

    print("[6/6] 寫 backtest_summary.json + markdown 報告...")
    summary = build_signal_summary(samples, stats, today)
    presets_by_side = build_presets(stats, samples, grid, today)
    for side in SIDES:
        summary["by_side"][side]["filtered_presets"] = presets_by_side[side]
    summary["recommended_thresholds"] = {
        side: grid["by_side"][side]["best"] for side in SIDES
    }
    summary["grid_search_top10_by_side"] = {
        side: grid["by_side"][side]["top10"] for side in SIDES
    }

    SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_path = REPORTS / f"signal_validation_{today}.md"
    write_markdown_report(report_path, summary, grid)

    print(f"\n完成。")
    print(f"  data/backtest_summary.json    ({SUMMARY.stat().st_size/1024:.1f} KB)")
    print(f"  reports/per_sample.csv         ({(REPORTS / 'per_sample.csv').stat().st_size/1024/1024:.2f} MB)")
    print(f"  reports/symbol_stats.csv       ({(REPORTS / 'symbol_stats.csv').stat().st_size/1024:.1f} KB)")
    print(f"  {report_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
