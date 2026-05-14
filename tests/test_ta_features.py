"""
agents/ta_features.py 的單元測試。

所有 feature 切片必須嚴格 < d（walk-forward 不洩漏）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


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
        from ta_features import chip_features  # type: ignore[import-not-found]
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
        from ta_features import chip_features  # type: ignore[import-not-found]
        # d=2024-01-15，01-15 record 不能納入（嚴格 <）
        f = chip_features("2330", "2024-01-15", fx_merged(), window=60)
        # 01-15 bull 含 2330，若洩漏 bull_count 會變 5
        self.assertEqual(f["bull_count"], 4)

    def test_window_caps_lookback(self):
        from ta_features import chip_features  # type: ignore[import-not-found]
        # window=3：d=2024-01-15 往前數 3 個交易日 = 01-10, 01-11, 01-12
        # 2330 在這 3 天 bull 出現 1 次 (01-11)
        f = chip_features("2330", "2024-01-15", fx_merged(), window=3)
        self.assertEqual(f["bull_count"], 1)

    def test_avg_rate_when_in_bull(self):
        from ta_features import chip_features  # type: ignore[import-not-found]
        # 2330 在 bull 出現於 01-02 (160), 01-03 (162), 01-08 (171), 01-11 (172)
        # 平均 = (160+162+171+172)/4 = 166.25
        f = chip_features("2330", "2024-01-15", fx_merged(), window=60)
        self.assertAlmostEqual(f["bull_avg_rate"], 166.25, places=2)

    def test_unseen_symbol_returns_zeros(self):
        from ta_features import chip_features  # type: ignore[import-not-found]
        f = chip_features("9999", "2024-01-15", fx_merged(), window=60)
        self.assertEqual(f["bull_count"], 0)
        self.assertEqual(f["bear_count"], 0)
        self.assertEqual(f["top5_count"], 0)
        self.assertIsNone(f["last_top5_date"])
        self.assertIsNone(f["bull_avg_rate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
