"""
agents/ta_outcome.py 測試:verdict 邏輯 + T+N excess 計算。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ta_outcome import verdict, parse_trader_section, compute_outcome, parse_report_md   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_prices():
    return {
        "dates": [
            "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
            "2024-01-12", "2024-01-15", "2024-01-16", "2024-01-17",
        ],
        "prices": {
            "2330.TW": {"start": 0, "csv": "600,602,605,610,615,612,618,620,625,622,628,630"},
            "1101.TW": {"start": 0, "csv": "40,40.5,41,41.2,40.8,40,39.8,39.5,39.2,39,38.8,38.5"},
        },
    }


def fx_twii():
    return {
        "2024-01-02": 17000.0, "2024-01-03": 17050.0, "2024-01-04": 17080.0,
        "2024-01-05": 17120.0, "2024-01-08": 17200.0, "2024-01-09": 17150.0,
        "2024-01-10": 17220.0, "2024-01-11": 17260.0, "2024-01-12": 17290.0,
        "2024-01-15": 17320.0, "2024-01-16": 17350.0, "2024-01-17": 17380.0,
    }


class TestVerdict(unittest.TestCase):
    def test_buy_right_direction(self):
        self.assertEqual(verdict("buy", 0.02), "right_direction")

    def test_buy_wrong_direction(self):
        self.assertEqual(verdict("buy", -0.02), "wrong_direction")

    def test_sell_right_direction(self):
        self.assertEqual(verdict("sell", -0.02), "right_direction")

    def test_sell_wrong_direction(self):
        self.assertEqual(verdict("sell", 0.02), "wrong_direction")

    def test_hold_right_hold(self):
        self.assertEqual(verdict("hold", 0.02), "right_hold")
        self.assertEqual(verdict("hold", -0.02), "right_hold")

    def test_hold_missed_long(self):
        self.assertEqual(verdict("hold", 0.08), "missed_long")

    def test_hold_avoided_loss(self):
        self.assertEqual(verdict("hold", -0.08), "avoided_loss")

    def test_hold_wrong_direction_marginal(self):
        self.assertEqual(verdict("hold", 0.04), "wrong_direction")
        self.assertEqual(verdict("hold", -0.04), "wrong_direction")


class TestParseTraderSection(unittest.TestCase):
    def test_parses_action_conviction_horizon_rationale(self):
        md = """
## 交易員

ACTION: hold
CONVICTION: 0.45
HORIZON: short
RATIONALE: 綜合四份報告後,多空訊號相互抵消,難以形成高信心方向判斷。
"""
        result = parse_trader_section(md)
        self.assertEqual(result["action"], "hold")
        self.assertAlmostEqual(result["conviction"], 0.45)
        self.assertEqual(result["horizon"], "short")
        self.assertIn("綜合四份", result["rationale"])

    def test_returns_none_when_no_trader_section(self):
        md = "## 技術分析師\n技術面看多。"
        self.assertIsNone(parse_trader_section(md))

    def test_extracts_action_case_insensitive(self):
        md = "## 交易員\nACTION: Buy\nCONVICTION: 0.7\nHORIZON: long\nRATIONALE: x"
        result = parse_trader_section(md)
        self.assertEqual(result["action"], "buy")


class TestComputeOutcome(unittest.TestCase):
    def test_compute_with_full_horizons(self):
        sym2t = {"2330": "2330.TW"}
        result = compute_outcome(
            date="2024-01-02", symbol="2330",
            trader={"action": "buy", "conviction": 0.7, "horizon": "short",
                    "rationale": "..."},
            prices=fx_prices(), twii=fx_twii(),
            twii_dates=sorted(fx_twii().keys()), sym2t=sym2t,
            horizons=[5, 10],
        )
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["actual_excess_t5"], 0.01118, places=3)
        self.assertEqual(result["verdict"], "right_direction")

    def test_returns_none_when_horizon_not_reached(self):
        result = compute_outcome(
            date="2024-01-15", symbol="2330",
            trader={"action": "buy", "conviction": 0.7, "horizon": "short",
                    "rationale": "..."},
            prices=fx_prices(), twii=fx_twii(),
            twii_dates=sorted(fx_twii().keys()), sym2t={"2330": "2330.TW"},
            horizons=[10],
        )
        self.assertIsNone(result)

    def test_unknown_symbol_returns_none(self):
        result = compute_outcome(
            date="2024-01-02", symbol="9999",
            trader={"action": "buy", "conviction": 0.7, "horizon": "short",
                    "rationale": "..."},
            prices=fx_prices(), twii=fx_twii(),
            twii_dates=sorted(fx_twii().keys()), sym2t={"2330": "2330.TW"},
            horizons=[5],
        )
        self.assertIsNone(result)


class TestParseReportMd(unittest.TestCase):
    def test_parses_all_six_sections(self):
        md = """# 2330 (2330.TW) 深度分析  2024-01-15

STATUS: ok

## 技術分析師

技術面短評內容。

## 籌碼分析師

籌碼面短評內容。

## 多方研究員

多方論述。

## 空方研究員

空方論述。

## 交易員

ACTION: hold
CONVICTION: 0.5
HORIZON: short
RATIONALE: x

## 風險經理

MAX_POSITION_PCT: 10
"""
        tmp = Path(tempfile.mkdtemp()) / "r.md"
        tmp.write_text(md, encoding="utf-8")
        try:
            sections = parse_report_md(tmp)
            self.assertEqual(set(sections.keys()), {"market", "chip", "bull", "bear", "trader", "risk"})
            self.assertIn("技術面短評", sections["market"])
            self.assertIn("ACTION: hold", sections["trader"])
            self.assertIn("MAX_POSITION_PCT", sections["risk"])
        finally:
            tmp.unlink()
            tmp.parent.rmdir()

    def test_missing_section_returns_empty_string(self):
        md = "## 技術分析師\n只有這個。"
        tmp = Path(tempfile.mkdtemp()) / "r.md"
        tmp.write_text(md, encoding="utf-8")
        try:
            sections = parse_report_md(tmp)
            self.assertIn("只有這個", sections["market"])
            self.assertEqual(sections["chip"], "")
            self.assertEqual(sections["trader"], "")
        finally:
            tmp.unlink()
            tmp.parent.rmdir()


if __name__ == "__main__":
    unittest.main(verbosity=2)
