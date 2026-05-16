"""
agents/ta_features.py 的單元測試。

所有 feature 切片必須嚴格 < d（walk-forward 不洩漏）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from ta_features import _atr, _candle_pattern, _gap_count, _vol_ratio, chip_features, collect, market_context, past_perf, price_features, SymbolFeatures   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


# ── Fixtures ──────────────────────────────────────────────────

def fx_merged():
    """10 個交易日的 mini merged，2330 在 bull 出現多次、bear 出現 1 次。"""
    return [
        {"date": "2024-01-02", "rate": 160, "bull": "2330,2454", "bear": "1101",          "top5_margin_reduce_inst_buy": "2330"},
        {"date": "2024-01-03", "rate": 162, "bull": "2330",      "bear": "1101",          "top5_margin_reduce_inst_buy": "3034"},
        {"date": "2024-01-04", "rate": 165, "bull": "2454",      "bear": "1102,2330",     "top5_margin_reduce_inst_buy": "2330"},
        {"date": "2024-01-05", "rate": 168, "bull": "3008",      "bear": "2002",          "top5_margin_reduce_inst_buy": "2330"},
        {"date": "2024-01-08", "rate": 171, "bull": "2330",      "bear": "1101",          "top5_margin_reduce_inst_buy": "3034"},
        {"date": "2024-01-09", "rate": 173, "bull": "2454",      "bear": "2002",          "top5_margin_reduce_inst_buy": "2330"},
        {"date": "2024-01-10", "rate": 175, "bull": "3008",      "bear": "1102",          "top5_margin_reduce_inst_buy": "2454"},
        {"date": "2024-01-11", "rate": 172, "bull": "2330",      "bear": "1101",          "top5_margin_reduce_inst_buy": "3008"},
        {"date": "2024-01-12", "rate": 170, "bull": "2454",      "bear": "1101",          "top5_margin_reduce_inst_buy": "2330"},
        {"date": "2024-01-15", "rate": 168, "bull": "2330,3008", "bear": "1101",          "top5_margin_reduce_inst_buy": ""},
    ]


# ── Tests ─────────────────────────────────────────────────────

class TestChipFeatures(unittest.TestCase):
    def test_counts_appearances_strictly_before_d(self):
        # d=2024-01-15，2330 出現在 bull 於 01-02/01-03/01-08/01-11 (4 次，全在 < 01-15)
        # bear 於 01-04 (1 次), top5 於 01-02/01-04/01-05/01-09/01-12 (5 次)
        f = chip_features("2330", "2024-01-15", fx_merged(), window=60)
        self.assertEqual(f["bull_count"], 4)
        self.assertEqual(f["bear_count"], 1)
        self.assertEqual(f["top5_count"], 5)
        # 最近一次 top5 = 01-12，當日 rate=170
        self.assertEqual(f["last_top5_date"], "2024-01-12")
        self.assertEqual(f["last_top5_rate"], 170)

    def test_excludes_record_on_d(self):
        # d=2024-01-15，01-15 record 不能納入（嚴格 <）
        f = chip_features("2330", "2024-01-15", fx_merged(), window=60)
        # 01-15 bull 含 2330，若洩漏 bull_count 會變 5
        self.assertEqual(f["bull_count"], 4)

    def test_window_caps_lookback(self):
        # window=3：d=2024-01-15 往前數 3 個交易日 = 01-10, 01-11, 01-12
        # 2330 在這 3 天 bull 出現 1 次 (01-11)
        f = chip_features("2330", "2024-01-15", fx_merged(), window=3)
        self.assertEqual(f["bull_count"], 1)

    def test_avg_rate_when_in_bull(self):
        # 2330 在 bull 出現於 01-02 (160), 01-03 (162), 01-08 (171), 01-11 (172)
        # 平均 = (160+162+171+172)/4 = 166.25
        f = chip_features("2330", "2024-01-15", fx_merged(), window=60)
        self.assertAlmostEqual(f["bull_avg_rate"], 166.25, places=2)

    def test_unseen_symbol_returns_zeros(self):
        f = chip_features("9999", "2024-01-15", fx_merged(), window=60)
        self.assertEqual(f["bull_count"], 0)
        self.assertEqual(f["bear_count"], 0)
        self.assertEqual(f["top5_count"], 0)
        self.assertIsNone(f["last_top5_date"])
        self.assertIsNone(f["bull_avg_rate"])


def fx_prices():
    """5 個 ticker × 12 dates,3008.TW 故意中間沒資料(start=2)。"""
    return {
        "dates": [
            "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
            "2024-01-12", "2024-01-15", "2024-01-16", "2024-01-17",
        ],
        "prices": {
            "2330.TW": {"start": 0, "csv": "600,602,605,610,615,612,618,620,625,622,628,630"},
            "2454.TW": {"start": 0, "csv": "880,883,890,895,900,905,910,912,920,925,928,930"},
            "1101.TW": {"start": 0, "csv": "40,40.5,41,41.2,40.8,40,39.8,39.5,39.2,39,38.8,38.5"},
            "3008.TW": {"start": 2, "csv": "2500,2520,2510,2530,2550,2540,2560,2570,2580,2590"},
            "2002.TW": {"start": 0, "csv": "23,23.2,23.5,23.3,23,22.8,22.5,22,21.8,21.5,21.2,21"},
        },
    }


def fx_twii():
    return {
        "2024-01-02": 17000.0, "2024-01-03": 17050.0, "2024-01-04": 17080.0,
        "2024-01-05": 17120.0, "2024-01-08": 17200.0, "2024-01-09": 17150.0,
        "2024-01-10": 17220.0, "2024-01-11": 17260.0, "2024-01-12": 17290.0,
        "2024-01-15": 17320.0, "2024-01-16": 17350.0, "2024-01-17": 17380.0,
    }


def fx_prediction_rows():
    """3 筆 prediction + 對應 outcome,2330 被推薦過 2 次 long(一勝一敗)，1101 1 次 short(勝)。"""
    return [
        {"type": "prediction", "date": "2024-01-02", "horizons": [20],
         "long": [{"symbol": "2330"}], "short": [{"symbol": "1101"}]},
        {"type": "outcome", "date": "2024-01-02", "horizon": 20,
         "long_avg_excess": +0.02, "short_avg_excess": -0.01,
         "long_win": True, "short_win": True, "win": True,
         "verified_at": "2024-01-30"},

        {"type": "prediction", "date": "2024-01-08", "horizons": [20],
         "long": [{"symbol": "2330"}, {"symbol": "2454"}], "short": []},
        {"type": "outcome", "date": "2024-01-08", "horizon": 20,
         "long_avg_excess": -0.01, "short_avg_excess": 0.0,
         "long_win": False, "short_win": False, "win": False,
         "verified_at": "2024-02-08"},

        {"type": "prediction", "date": "2024-01-15", "horizons": [20],
         "long": [{"symbol": "2454"}], "short": [{"symbol": "1102"}]},
    ]


class TestPriceFeatures(unittest.TestCase):
    def test_window_strictly_before_d(self):
        f = price_features("2330.TW", "2024-01-15", fx_prices(),
                            fx_twii(), window=5)
        self.assertEqual(f["window_start"], "2024-01-08")
        self.assertEqual(f["window_end"], "2024-01-12")
        self.assertEqual(len(f["closes"]), 5)
        self.assertEqual(f["closes"], [615, 612, 618, 620, 625])

    def test_ma_calculations(self):
        # MA5 = (615+612+618+620+625)/5 = 618.0
        f = price_features("2330.TW", "2024-01-15", fx_prices(),
                            fx_twii(), window=5)
        self.assertAlmostEqual(f["ma5"], 618.0, places=2)

    def test_relative_vs_twii(self):
        # 2330: 615→625 = +1.626%; TWII: 17200→17290 = +0.523%; excess = +1.103%
        f = price_features("2330.TW", "2024-01-15", fx_prices(),
                            fx_twii(), window=5)
        self.assertAlmostEqual(f["return_window"], 0.01626, places=4)
        self.assertAlmostEqual(f["twii_return_window"], 0.00523, places=4)
        self.assertAlmostEqual(f["excess_return_window"], 0.01103, places=4)

    def test_late_start_ticker(self):
        # 3008.TW start=2 (csv[0] 對應 dates[2]=01-04); window 01-08~01-12
        # dates idx 4..8 → csv idx 2..6 → 2510, 2530, 2550, 2540, 2560
        f = price_features("3008.TW", "2024-01-15", fx_prices(),
                            fx_twii(), window=5)
        self.assertEqual(f["closes"], [2510, 2530, 2550, 2540, 2560])

    def test_missing_ticker_returns_none(self):
        f = price_features("9999.TW", "2024-01-15", fx_prices(),
                            fx_twii(), window=5)
        self.assertIsNone(f)

    def test_twii_anchor_miss_returns_none_for_relative(self):
        # 缺 TWII anchor 時:closes/ma 仍可算,但 twii_return_window /
        # excess_return_window 設為 None(對齊 analyze_signals.twii_return 慣例,
        # 不要靜默回傳 0.0 誤導下游)
        twii_partial = {k: v for k, v in fx_twii().items() if k != "2024-01-08"}
        f = price_features("2330.TW", "2024-01-15", fx_prices(),
                            twii_partial, window=5)
        self.assertIsNotNone(f)
        self.assertEqual(len(f["closes"]), 5)
        self.assertAlmostEqual(f["return_window"], 0.01626, places=4)
        self.assertIsNone(f["twii_return_window"])
        self.assertIsNone(f["excess_return_window"])

    def test_bias_ma20_computed(self):
        # window=5,closes=[615,612,618,620,625],ma20 fallback 到整段 mean=618
        # bias = (625-618)/618 * 100 = 1.13%
        f = price_features("2330.TW", "2024-01-15", fx_prices(),
                            fx_twii(), window=5)
        self.assertAlmostEqual(f["bias_ma20"], 1.13, places=1)

    def test_macd_none_when_window_too_short(self):
        # 12 日 fixture < 35 → MACD 應為 None
        f = price_features("2330.TW", "2024-01-15", fx_prices(),
                            fx_twii(), window=5)
        self.assertIsNone(f["macd_dif"])
        self.assertIsNone(f["macd_signal"])
        self.assertIsNone(f["macd_hist"])


class TestMacdLongWindow(unittest.TestCase):
    """MACD 需要 >= 35 日才有意義,用合成 40 日資料測 EMA 收斂與方向。"""

    def _synth_prices(self, closes) -> dict:
        n = len(closes)
        return {
            "dates": [f"2024-{(i // 20) + 1:02d}-{(i % 20) + 1:02d}" for i in range(n)],
            "prices": {"TEST.TW": {"start": 0, "csv": ",".join(str(c) for c in closes)}},
        }

    def _synth_twii(self, prices: dict) -> dict[str, float]:
        return {d: 17000.0 for d in prices["dates"]}

    def test_macd_positive_on_uptrend(self):
        # 連續 40 日上漲 → EMA12 應持續高於 EMA26 → DIF>0、Hist 通常 >0
        closes = [100 + i for i in range(40)]
        prices = self._synth_prices(closes)
        f = price_features("TEST.TW", "2026-01-01", prices,
                            self._synth_twii(prices), window=40)
        self.assertIsNotNone(f["macd_dif"])
        self.assertGreater(f["macd_dif"], 0, "上升趨勢 DIF 應為正")
        self.assertIsNotNone(f["macd_signal"])
        self.assertIsNotNone(f["macd_hist"])

    def test_macd_negative_on_downtrend(self):
        closes = [200 - i for i in range(40)]
        prices = self._synth_prices(closes)
        f = price_features("TEST.TW", "2026-01-01", prices,
                            self._synth_twii(prices), window=40)
        self.assertIsNotNone(f["macd_dif"])
        self.assertLess(f["macd_dif"], 0, "下降趨勢 DIF 應為負")


class TestPastPerf(unittest.TestCase):
    def test_counts_past_long_appearances(self):
        p = past_perf("2330", "2024-02-15", fx_prediction_rows())
        self.assertEqual(p["long_count"], 2)
        self.assertEqual(p["long_win_count"], 1)
        self.assertEqual(p["short_count"], 0)

    def test_short_side(self):
        p = past_perf("1101", "2024-02-15", fx_prediction_rows())
        self.assertEqual(p["short_count"], 1)
        self.assertEqual(p["short_win_count"], 1)
        self.assertEqual(p["long_count"], 0)

    def test_excludes_prediction_on_or_after_d(self):
        p = past_perf("2454", "2024-01-15", fx_prediction_rows())
        self.assertEqual(p["long_count"], 1)
        self.assertEqual(p["long_win_count"], 0)

    def test_unverified_prediction_counted_but_no_win(self):
        p = past_perf("1102", "2024-02-15", fx_prediction_rows())
        self.assertEqual(p["short_count"], 1)
        self.assertEqual(p["short_win_count"], 0)

    def test_unseen_symbol(self):
        p = past_perf("9999", "2024-02-15", fx_prediction_rows())
        self.assertEqual(p["long_count"], 0)
        self.assertEqual(p["short_count"], 0)


class TestMarketContext(unittest.TestCase):
    def test_recent_records_strictly_before_d(self):
        ctx = market_context("2024-01-15", fx_merged(), fx_twii(), n_recent=30)
        # 所有 record 都 < d
        self.assertGreater(len(ctx["recent_records"]), 0)
        for r in ctx["recent_records"]:
            self.assertLess(r["date"], "2024-01-15")

    def test_twii_summary_strictly_before_d(self):
        ctx = market_context("2024-01-15", fx_merged(), fx_twii(), n_recent=30)
        twii = ctx["twii"]
        self.assertIsNotNone(twii)
        # TWII first/last 都 < d;1/02 → 1/12
        self.assertEqual(twii["first_date"], "2024-01-02")
        self.assertEqual(twii["last_date"], "2024-01-12")
        # 17000 → 17290 = +1.7058...%
        self.assertAlmostEqual(twii["return_pct"], 1.7058823, places=4)

    def test_window_caps_recent(self):
        ctx = market_context("2024-01-15", fx_merged(), fx_twii(), n_recent=3)
        # 最近 3 個 record(< 2024-01-15): 01-10, 01-11, 01-12
        dates = [r["date"] for r in ctx["recent_records"]]
        self.assertEqual(dates, ["2024-01-10", "2024-01-11", "2024-01-12"])

    def test_empty_twii_returns_none_summary(self):
        ctx = market_context("2024-01-15", fx_merged(), {}, n_recent=30)
        self.assertIsNone(ctx["twii"])


class TestCollect(unittest.TestCase):
    def test_collect_returns_dataclass_with_all_fields(self):
        # fixture 只有 12 個交易日,price_window 用 5 才有資料(預設 20 會 None)
        result = collect(
            symbol="2330", ticker="2330.TW", d="2024-01-15",
            merged=fx_merged(), prices=fx_prices(),
            twii=fx_twii(), prediction_rows=fx_prediction_rows(),
            price_window=5,
        )
        # SymbolFeatures dataclass 各 field 都齊
        self.assertIsInstance(result, SymbolFeatures)
        self.assertEqual(result.symbol, "2330")
        self.assertEqual(result.ticker, "2330.TW")
        self.assertEqual(result.target_date, "2024-01-15")
        # chip: 同 TestChipFeatures.test_counts_appearances_strictly_before_d
        self.assertEqual(result.chip["bull_count"], 4)
        # price: 不為 None
        self.assertIsNotNone(result.price)
        # past_perf: 2330 過去 long 2 次
        self.assertEqual(result.past_perf["long_count"], 2)
        # market_context: 近 30 天 record
        self.assertGreater(len(result.market_context["recent_records"]), 0)
        # 所有 record 都 < d
        for r in result.market_context["recent_records"]:
            self.assertLess(r["date"], "2024-01-15")

    def test_collect_with_missing_price_returns_price_none(self):
        result = collect(
            symbol="9999", ticker="9999.TW", d="2024-01-15",
            merged=fx_merged(), prices=fx_prices(),
            twii=fx_twii(), prediction_rows=fx_prediction_rows(),
        )
        self.assertIsNone(result.price)
        # chip / past_perf 仍可算(只是會是 0)
        self.assertEqual(result.chip["bull_count"], 0)
        self.assertEqual(result.past_perf["long_count"], 0)

    def test_collect_is_frozen(self):
        result = collect(
            symbol="2330", ticker="2330.TW", d="2024-01-15",
            merged=fx_merged(), prices=fx_prices(),
            twii=fx_twii(), prediction_rows=fx_prediction_rows(),
            price_window=5,
        )
        # frozen=True 不可被修改(FrozenInstanceError 是 dataclasses.FrozenInstanceError)
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            result.symbol = "9999"  # type: ignore[misc]

    def test_collect_with_lessons(self):
        sample_lesson = {
            "id": "2024-01-02_2330", "date": "2024-01-02", "symbol": "2330",
            "reflection": "test lesson", "tags": ["test"],
        }
        result = collect(
            symbol="2330", ticker="2330.TW", d="2024-01-15",
            merged=fx_merged(), prices=fx_prices(),
            twii=fx_twii(), prediction_rows=fx_prediction_rows(),
            price_window=5,
            lessons=[sample_lesson],
        )
        self.assertEqual(len(result.lessons), 1)
        self.assertEqual(result.lessons[0]["id"], "2024-01-02_2330")

    def test_collect_lessons_defaults_to_empty(self):
        result = collect(
            symbol="2330", ticker="2330.TW", d="2024-01-15",
            merged=fx_merged(), prices=fx_prices(),
            twii=fx_twii(), prediction_rows=fx_prediction_rows(),
            price_window=5,
        )
        self.assertEqual(result.lessons, [])


class TestATR(unittest.TestCase):
    def test_atr_basic(self):
        # 5 日 OHLC, true range 計算對
        # day1: H-L = 10
        # day2: max(H-L=12, |H-prev_C|=11, |L-prev_C|=2) = 12
        # day3: max(8, 10, 5) = 10
        # 平均 ~ (10+12+10) / 3 ≈ 10.67 (3 日 ATR)
        highs = [105, 112, 108]
        lows  = [95, 100, 100]
        closes= [100, 110, 102]
        atr = _atr(highs, lows, closes, period=3)
        self.assertGreater(atr, 0)
        self.assertLess(atr, 20)

    def test_atr_insufficient_data(self):
        # 不足 period → 回 0 或 None
        atr = _atr([100], [99], [99.5], period=14)
        self.assertIsNone(atr)


class TestGapCount(unittest.TestCase):
    def test_gap_threshold(self):
        # opens 跟前日 close 比偏離 > 0.5%
        # day2 open 105 vs day1 close 100 → +5% → gap
        # day3 open 103 vs day2 close 100 → +3% → gap
        # day4 open 110 vs day3 close 110 → 0% → no gap
        # day5 open 102 vs day4 close 103 → -1% → gap (>0.5%)
        opens =  [100, 105, 103, 110, 102]
        closes = [100, 100, 110, 103, 110]
        count = _gap_count(opens, closes, threshold=0.005)
        self.assertEqual(count, 3)

    def test_no_gaps(self):
        # 所有 open ≈ 前日 close → 0 gaps
        opens =  [100, 100.1, 99.9, 100.2, 100.0]
        closes = [100, 100, 100, 100, 100]
        count = _gap_count(opens, closes, threshold=0.005)
        self.assertEqual(count, 0)


class TestVolumeRatio(unittest.TestCase):
    def test_vol_ratio_5_20(self):
        # 5 日平均 vs 20 日平均
        vols = [1_000_000] * 15 + [2_000_000] * 5  # 後 5 日量翻倍
        avg5, avg20, ratio = _vol_ratio(vols)
        self.assertAlmostEqual(avg5, 2_000_000)
        self.assertAlmostEqual(avg20, 1_250_000)
        self.assertAlmostEqual(ratio, 1.6)

    def test_vol_ratio_insufficient_data(self):
        avg5, avg20, ratio = _vol_ratio([100, 200])
        self.assertIsNone(avg5)


class TestCandlePattern(unittest.TestCase):
    def test_hammer(self):
        # 錘頭:實體小 + 下影線長 + 收紅
        # open=98, close=100 (實體 2), high=101, low=90 (下影線 8)
        pattern = _candle_pattern(open_=98, high=101, low=90, close=100)
        self.assertEqual(pattern, "錘頭")

    def test_doji(self):
        # 十字:實體 < 全長 10%
        pattern = _candle_pattern(open_=100, high=105, low=95, close=100.2)
        self.assertEqual(pattern, "十字")

    def test_normal_candle(self):
        # 一般 K 線(無特殊型態)
        pattern = _candle_pattern(open_=100, high=103, low=99, close=102)
        self.assertIsNone(pattern)


if __name__ == "__main__":
    unittest.main(verbosity=2)
