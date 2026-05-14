"""
agents/ta_features.py 的單元測試。

所有 feature 切片必須嚴格 < d（walk-forward 不洩漏）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from ta_features import chip_features, price_features, past_perf   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


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
                            fx_twii(), sorted(fx_twii().keys()), window=5)
        self.assertEqual(f["window_start"], "2024-01-08")
        self.assertEqual(f["window_end"], "2024-01-12")
        self.assertEqual(len(f["closes"]), 5)
        self.assertEqual(f["closes"], [615, 612, 618, 620, 625])

    def test_ma_calculations(self):
        # MA5 = (615+612+618+620+625)/5 = 618.0
        f = price_features("2330.TW", "2024-01-15", fx_prices(),
                            fx_twii(), sorted(fx_twii().keys()), window=5)
        self.assertAlmostEqual(f["ma5"], 618.0, places=2)

    def test_relative_vs_twii(self):
        # 2330: 615→625 = +1.626%; TWII: 17200→17290 = +0.523%; excess = +1.103%
        f = price_features("2330.TW", "2024-01-15", fx_prices(),
                            fx_twii(), sorted(fx_twii().keys()), window=5)
        self.assertAlmostEqual(f["return_window"], 0.01626, places=4)
        self.assertAlmostEqual(f["twii_return_window"], 0.00523, places=4)
        self.assertAlmostEqual(f["excess_return_window"], 0.01103, places=4)

    def test_late_start_ticker(self):
        # 3008.TW start=2 (csv[0] 對應 dates[2]=01-04); window 01-08~01-12
        # dates idx 4..8 → csv idx 2..6 → 2510, 2530, 2550, 2540, 2560
        f = price_features("3008.TW", "2024-01-15", fx_prices(),
                            fx_twii(), sorted(fx_twii().keys()), window=5)
        self.assertEqual(f["closes"], [2510, 2530, 2550, 2540, 2560])

    def test_missing_ticker_returns_none(self):
        f = price_features("9999.TW", "2024-01-15", fx_prices(),
                            fx_twii(), sorted(fx_twii().keys()), window=5)
        self.assertIsNone(f)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
