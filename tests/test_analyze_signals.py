"""
analyze_signals.py 的 unit test。

重點:Python 側的 read_price / get_close_on_or_before / get_close_n_days_later
必須與 dashboard_all.html:586-627 的 JS 邏輯完全一致(否則 Python 算的報告
與 dashboard 顯示的數字會對不起來)。

跑:
    python -m pytest tests/test_analyze_signals.py -q
    python tests/test_analyze_signals.py             (plain unittest)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from analyze_signals import (  # type: ignore[import-not-found]  # noqa: E402
    PRESET_LOOSE, PRESET_STRICT,
    Sample, SymbolStat,
    build_per_sample_table,
    build_signal_summary,
    compute_excess_return,
    compute_symbol_stats,
    evaluate_preset,
    find_idx_on_or_before,
    get_close_n_days_later,
    get_close_on_or_before,
    grid_search_thresholds,
    is_effective_signal,
    read_price,
    split_names,
    twii_return,
    _stats_of,
)


# ─── Fixture: 5 ticker × 6 dates,刻意做出 start>0 + 中間缺值 ───────

def make_prices_fixture():
    """
    dates: 2024-01-02 .. 2024-01-09 (5 個交易日,跳週末)
    A.TW: 從 idx 0 開始,完整
    B.TW: 從 idx 1 開始,中間缺一日
    C.TW: 從 idx 2 開始,只 3 日
    D.TW: 完全沒在 prices(對應 dashboard 找不到 ticker 情況)
    """
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    return {
        "dates": dates,
        "prices": {
            "A.TW": {"start": 0, "csv": "100.0,101.0,102.5,103.0,104.5"},
            "B.TW": {"start": 1, "csv": "200.0,,210.0,212.0"},   # idx 2 缺
            "C.TW": {"start": 2, "csv": "50.0,51.0,52.0"},
        },
    }


def make_twii_fixture():
    return {
        "2024-01-02": 17000.0,
        "2024-01-03": 17050.0,
        "2024-01-04": 17100.0,
        "2024-01-05": 17080.0,
        "2024-01-08": 17200.0,
    }


# ─── Tests ────────────────────────────────────────────────────────

class TestSplitNames(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(split_names("台積電,聯發科,鴻海"), ["台積電", "聯發科", "鴻海"])

    def test_strips_whitespace_and_blanks(self):
        self.assertEqual(split_names(" a , b ,, c "), ["a", "b", "c"])

    def test_empty(self):
        self.assertEqual(split_names(""), [])
        self.assertEqual(split_names(None), [])  # type: ignore[arg-type]


class TestFindIdxOnOrBefore(unittest.TestCase):
    """對應 dashboard getCloseOnOrBefore 找 idx 的邏輯"""

    def setUp(self):
        self.dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]

    def test_exact(self):
        self.assertEqual(find_idx_on_or_before(self.dates, "2024-01-04"), 2)

    def test_weekend_falls_to_friday(self):
        # 2024-01-06 (週六) 不在 dates;應拿 2024-01-05(週五,idx=3)
        self.assertEqual(find_idx_on_or_before(self.dates, "2024-01-06"), 3)
        # 2024-01-07 (週日) 同上
        self.assertEqual(find_idx_on_or_before(self.dates, "2024-01-07"), 3)

    def test_after_last(self):
        self.assertEqual(find_idx_on_or_before(self.dates, "2024-01-15"), 4)

    def test_before_first(self):
        self.assertEqual(find_idx_on_or_before(self.dates, "2023-12-31"), -1)


class TestReadPrice(unittest.TestCase):
    """對應 dashboard readPrice"""

    def test_in_range(self):
        p = {"start": 1, "csv": "100,101,102,103"}
        # idx=2 → cells[2-1]=cells[1]="101"
        self.assertEqual(read_price(p, 2), 101.0)
        self.assertEqual(read_price(p, 1), 100.0)
        self.assertEqual(read_price(p, 4), 103.0)

    def test_before_start(self):
        p = {"start": 2, "csv": "50,51"}
        self.assertIsNone(read_price(p, 0))
        self.assertIsNone(read_price(p, 1))

    def test_after_end(self):
        p = {"start": 0, "csv": "1,2,3"}
        self.assertIsNone(read_price(p, 5))

    def test_empty_cell_returns_none(self):
        p = {"start": 0, "csv": "1,,3"}
        self.assertIsNone(read_price(p, 1))

    def test_negative_idx(self):
        self.assertIsNone(read_price({"start": 0, "csv": "1,2"}, -1))


class TestGetCloseOnOrBefore(unittest.TestCase):
    """完整模擬 dashboard 邏輯:ticker 找不到 → None;週末 → 取前一交易日"""

    def setUp(self):
        self.prices = make_prices_fixture()

    def test_exact_date(self):
        self.assertEqual(get_close_on_or_before(self.prices, "A.TW", "2024-01-03"), 101.0)

    def test_weekend_uses_friday(self):
        # 2024-01-06 (週六) 取 2024-01-05 idx=3 → A.TW cells[3-0]=103.0
        self.assertEqual(get_close_on_or_before(self.prices, "A.TW", "2024-01-06"), 103.0)

    def test_ticker_not_in_prices(self):
        self.assertIsNone(get_close_on_or_before(self.prices, "D.TW", "2024-01-03"))

    def test_date_before_ticker_start(self):
        # B.TW start=1,在 idx=0 (2024-01-02) 應回 None
        self.assertIsNone(get_close_on_or_before(self.prices, "B.TW", "2024-01-02"))

    def test_date_at_empty_cell(self):
        # B.TW idx=2 是空字串
        self.assertIsNone(get_close_on_or_before(self.prices, "B.TW", "2024-01-04"))


class TestGetCloseNDaysLater(unittest.TestCase):
    """對應 dashboard getCloseNDaysLater:在 prices.dates 索引中前進 N 個元素"""

    def setUp(self):
        self.prices = make_prices_fixture()

    def test_n_zero(self):
        # 0 步等同 on_or_before
        self.assertEqual(get_close_n_days_later(self.prices, "A.TW", "2024-01-03", 0), 101.0)

    def test_n_two(self):
        # 從 2024-01-03 (idx=1) 前進 2 個 → idx=3 (2024-01-05) → A.TW = 103.0
        self.assertEqual(get_close_n_days_later(self.prices, "A.TW", "2024-01-03", 2), 103.0)

    def test_overflow_clips_to_last(self):
        # 從 idx=3 前進 100 個 → 落在最末 idx=4 (2024-01-08) → 104.5
        self.assertEqual(get_close_n_days_later(self.prices, "A.TW", "2024-01-05", 100), 104.5)

    def test_lands_on_empty_cell(self):
        # B.TW idx=2 為空;從 2024-01-03 (idx=1) 前進 1 → idx=2 → 應 None
        self.assertIsNone(get_close_n_days_later(self.prices, "B.TW", "2024-01-03", 1))

    def test_from_weekend_starts_at_prev(self):
        # 2024-01-06 (週六) → base_idx=3 (週五);前進 1 → idx=4 (2024-01-08)
        self.assertEqual(get_close_n_days_later(self.prices, "A.TW", "2024-01-06", 1), 104.5)


class TestTwiiReturn(unittest.TestCase):
    """
    twii_return 的關鍵 quirk:date 不在 twii_dates 直接 None(不像 prices 會找前一日)。
    這是 dashboard 既有行為,刻意保留。
    """

    def setUp(self):
        self.twii = make_twii_fixture()
        self.dates = sorted(self.twii.keys())

    def test_basic_5d_later(self):
        # 從 2024-01-02 前進 4 個 → 2024-01-08;return = 17200/17000 - 1
        ret = twii_return(self.twii, self.dates, "2024-01-02", 4)
        self.assertAlmostEqual(ret, 17200/17000 - 1, places=8)

    def test_date_not_in_twii_returns_none(self):
        self.assertIsNone(twii_return(self.twii, self.dates, "2024-01-06", 1))

    def test_overflow_clips(self):
        ret = twii_return(self.twii, self.dates, "2024-01-05", 100)
        # 落在最末 → 17200/17080 - 1
        self.assertAlmostEqual(ret, 17200/17080 - 1, places=8)


class TestComputeExcessReturn(unittest.TestCase):
    def test_outperform(self):
        # 個股 +5%,大盤 +2% → excess +3%
        e = compute_excess_return(p0=100, pN=105, twii0=10000, twiiN=10200)
        self.assertAlmostEqual(e, 0.05 - 0.02, places=8)

    def test_underperform(self):
        e = compute_excess_return(p0=100, pN=95, twii0=10000, twiiN=10200)
        self.assertAlmostEqual(e, -0.05 - 0.02, places=8)


class TestStatsOf(unittest.TestCase):
    def test_basic(self):
        n, avg, std, t, win = _stats_of([0.01, 0.02, 0.03, -0.01])
        self.assertEqual(n, 4)
        self.assertAlmostEqual(avg, 0.0125, places=6)
        self.assertAlmostEqual(win, 0.75, places=6)
        # std,t 大致檢查 sign
        self.assertGreater(std, 0)
        self.assertGreater(t, 0)

    def test_empty(self):
        self.assertEqual(_stats_of([]), (0, 0.0, 0.0, 0.0, 0.0))


class TestIsEffectiveSignal(unittest.TestCase):
    """B5 邊界: n=4 不過、n=5 過; t=1.95 不過、t=1.96 過"""

    def base_stat(self, **kw):
        d = dict(symbol="X", side="bull", horizon=20,
                 train_n=10, train_avg=0.05, train_winrate=0.6, train_t=2.5,
                 recent_n=3)
        d.update(kw)
        return SymbolStat(**d)  # 其餘欄位用 dataclass 預設值

    def test_n_boundary(self):
        params = PRESET_LOOSE  # min_n=3
        self.assertFalse(is_effective_signal(self.base_stat(train_n=2), params))
        self.assertTrue(is_effective_signal(self.base_stat(train_n=3), params))

    def test_t_boundary(self):
        params = {"min_n": 5, "min_avg_excess": 0.0, "min_win_rate": 0.0,
                  "min_t_stat": 1.96, "min_recent_n": 0}
        self.assertFalse(is_effective_signal(self.base_stat(train_t=1.95), params))
        self.assertTrue(is_effective_signal(self.base_stat(train_t=1.96), params))

    def test_strict_rejects_loose_pass(self):
        # 只達 loose 的 stat 不能通過 strict
        loose_passing = self.base_stat(train_n=4, train_avg=0.015, train_winrate=0.51,
                                        train_t=1.7, recent_n=0)
        self.assertTrue(is_effective_signal(loose_passing, PRESET_LOOSE))
        self.assertFalse(is_effective_signal(loose_passing, PRESET_STRICT))


class TestEvaluatePreset(unittest.TestCase):
    """grid search 的單組評估邏輯"""

    def test_no_match_returns_zero(self):
        params = {"min_n": 999, "min_avg_excess": 0, "min_win_rate": 0,
                  "min_t_stat": 0, "min_recent_n": 0}
        r = evaluate_preset([], [], params, side="bull")
        self.assertEqual(r["n_symbols"], 0)
        self.assertEqual(r["test_avg"], 0.0)
        self.assertEqual(r["side"], "bull")


class TestBuildPerSampleTable(unittest.TestCase):
    """端到端 mini fixture: 1 record × 1 side × 1 symbol → 應產 5 horizons 樣本(若都讀得到)"""

    def test_basic_flow(self):
        merged = [{
            "date": "2024-01-02", "rate": 165,
            "bull": "AA", "bear": "", "top5_margin_reduce_inst_buy": "",
        }]
        twii   = make_twii_fixture()
        prices = make_prices_fixture()
        sym2t  = {"AA": "A.TW"}

        samples = build_per_sample_table(merged, twii, prices, sym2t)
        # horizons = [1,5,10,20,60];fixture 只有 5 dates,N>=5 會 clip 到最末
        # 對 A.TW 從 2024-01-02 (idx=0):
        #   h=1 → idx=1 → 101.0   ✓
        #   h=5 → idx=4 → 104.5   ✓ (clipped)
        # twii_return:從 2024-01-02 前進 1 個 → 2024-01-03 (17050)
        bull_h1 = next(s for s in samples if s.side == "bull" and s.horizon == 1)
        self.assertEqual(bull_h1.p0, 100.0)
        self.assertEqual(bull_h1.pN, 101.0)
        self.assertAlmostEqual(bull_h1.twii_ret, 17050/17000 - 1, places=8)
        self.assertAlmostEqual(bull_h1.excess_ret, (101/100 - 1) - (17050/17000 - 1), places=8)

    def test_unknown_symbol_skipped(self):
        merged = [{"date": "2024-01-02", "rate": 165,
                   "bull": "UNKNOWN", "bear": "", "top5_margin_reduce_inst_buy": ""}]
        out = build_per_sample_table(merged, make_twii_fixture(), make_prices_fixture(), {})
        self.assertEqual(out, [])


class TestComputeSymbolStats(unittest.TestCase):
    def test_train_test_split(self):
        # 兩筆樣本,一筆 in train (2024),一筆 in test (2025)
        samples = [
            Sample(date="2024-06-01", side="bull", symbol="X", ticker="X.TW", horizon=20,
                   p0=100, pN=105, ret=0.05, twii_ret=0.02, excess_ret=0.03),
            Sample(date="2025-06-01", side="bull", symbol="X", ticker="X.TW", horizon=20,
                   p0=100, pN=110, ret=0.10, twii_ret=0.04, excess_ret=0.06),
        ]
        stats = compute_symbol_stats(samples, today="2026-01-01")
        s = next(st for st in stats if st.symbol == "X" and st.horizon == 20)
        self.assertEqual(s.train_n, 1)
        self.assertEqual(s.test_n, 1)
        self.assertAlmostEqual(s.train_avg, 0.03, places=6)
        self.assertAlmostEqual(s.test_avg, 0.06, places=6)

    def test_recent_window(self):
        samples = [
            # today 是 2025-06-01;這筆 2024-12-01 距離 ~182 天 → 在 365 內
            Sample(date="2024-12-01", side="bull", symbol="X", ticker="X.TW", horizon=20,
                   p0=100, pN=103, ret=0.03, twii_ret=0.01, excess_ret=0.02),
            # 這筆 2023-01-01 → 距離 >365 天 → 不在 recent
            Sample(date="2023-01-01", side="bull", symbol="X", ticker="X.TW", horizon=20,
                   p0=100, pN=101, ret=0.01, twii_ret=0.005, excess_ret=0.005),
        ]
        stats = compute_symbol_stats(samples, today="2025-06-01")
        s = next(st for st in stats if st.symbol == "X")
        self.assertEqual(s.recent_n, 1)
        self.assertAlmostEqual(s.recent_avg_excess, 0.02, places=6)


class TestGridSearch(unittest.TestCase):
    """grid_search_thresholds per-side 結構"""

    def test_empty_falls_back_loose(self):
        r = grid_search_thresholds([], [])
        self.assertIn("by_side", r)
        for side in ("bull", "bear", "top5"):
            self.assertEqual(r["by_side"][side]["best"], PRESET_LOOSE)
        self.assertGreater(r["n_searched_per_side"], 0)


class TestIsEffectiveSignalBear(unittest.TestCase):
    """bear 訊號:train_avg 必須為負(下跌)、win_rate 應低(漲的勝率低)"""

    def base_bear(self, **kw):
        d = dict(symbol="X", side="bear", horizon=20,
                 train_n=10, train_avg=-0.05, train_winrate=0.30, train_t=-2.5,
                 recent_n=3)
        d.update(kw)
        return SymbolStat(**d)

    def test_negative_avg_passes(self):
        params = {"min_n": 5, "min_avg_excess": 0.03, "min_win_rate": 0.55,
                  "min_t_stat": 1.96, "min_recent_n": 0}
        # train_avg = -0.05 ≤ -0.03 ✓; (1-0.30)=0.70 ≥ 0.55 ✓; |t|=2.5≥1.96 ✓
        self.assertTrue(is_effective_signal(self.base_bear(), params))

    def test_positive_avg_rejected_for_bear(self):
        params = {"min_n": 5, "min_avg_excess": 0.03, "min_win_rate": 0.55,
                  "min_t_stat": 1.96, "min_recent_n": 0}
        # bear 但 train_avg = +0.05 → 拒絕(方向錯)
        self.assertFalse(is_effective_signal(self.base_bear(train_avg=0.05), params))

    def test_high_winrate_rejected_for_bear(self):
        params = {"min_n": 5, "min_avg_excess": 0.03, "min_win_rate": 0.55,
                  "min_t_stat": 1.96, "min_recent_n": 0}
        # bear 但 winrate=0.80 → (1-0.80)=0.20 < 0.55 → 拒絕(漲的勝率太高代表沒下跌)
        self.assertFalse(is_effective_signal(self.base_bear(train_winrate=0.80), params))


class TestBuildSignalSummary(unittest.TestCase):
    def test_summary_structure(self):
        samples = [
            Sample(date="2024-06-01", side="bull", symbol="X", ticker="X.TW", horizon=20,
                   p0=100, pN=105, ret=0.05, twii_ret=0.02, excess_ret=0.03),
        ]
        stats = compute_symbol_stats(samples, today="2026-01-01")
        summary = build_signal_summary(samples, stats, today="2026-01-01")
        self.assertIn("by_side", summary)
        self.assertIn("bull", summary["by_side"])
        self.assertIn("by_horizon", summary["by_side"]["bull"]["all"])
        self.assertIn("20", summary["by_side"]["bull"]["all"]["by_horizon"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
