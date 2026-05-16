"""
agents/ta_reflect.py 測試。LLM stub。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from ta_reflect import build_reflection_prompt, parse_reflection_response, reflect_one   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_outcome() -> dict:
    return {
        "date": "2026-05-04",
        "symbol": "2330",
        "trader_action": "hold",
        "trader_conviction": 0.45,
        "trader_horizon": "short",
        "trader_rationale_excerpt": "綜合四份報告後,多空訊號相互抵消...",
        "actual_excess_t5": 0.012,
        "actual_excess_t10": 0.082,
        "actual_excess_t20": 0.045,
        "verdict": "missed_long",
        "primary_horizon": 10,
    }


def fx_report_sections() -> dict[str, str]:
    return {
        "market": "技術面 MA5/MA20 看似多頭排列,但動能轉弱...",
        "chip": "三榜皆空,籌碼面中性偏空觀望。",
        "bull": "MA20 仍提供支撐,歷史 long 勝率高...",
        "bear": "短線動能轉弱,跌幅 5.7% 尚未止穩...",
        "trader": "ACTION: hold\nCONVICTION: 0.45\n...",
        "risk": "同意,但建議觀察條件...",
    }


class TestBuildReflectionPrompt(unittest.TestCase):
    def test_includes_outcome_and_all_sections(self):
        prompt = build_reflection_prompt(fx_outcome(), fx_report_sections())
        # 三個 horizon 的 excess 都要在
        self.assertIn("+1.20%", prompt)    # T+5
        self.assertIn("+8.20%", prompt)    # T+10
        self.assertIn("+4.50%", prompt)    # T+20
        # verdict
        self.assertIn("missed_long", prompt)
        # Trader 決策
        self.assertIn("hold", prompt)
        # 其他 agent 摘要
        self.assertIn("三榜皆空", prompt)


class TestParseReflectionResponse(unittest.TestCase):
    def test_parses_clean_json(self):
        raw = '{"reflection": "我那天判斷錯了...", "tags": ["chip_silent", "tech_strong"]}'
        result = parse_reflection_response(raw)
        self.assertEqual(result["reflection"], "我那天判斷錯了...")
        self.assertEqual(result["tags"], ["chip_silent", "tech_strong"])

    def test_handles_json_with_surrounding_text(self):
        raw = '反思如下:\n{"reflection": "x", "tags": ["a"]}\n以上'
        result = parse_reflection_response(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["reflection"], "x")

    def test_returns_none_on_invalid_json(self):
        self.assertIsNone(parse_reflection_response("not json at all"))

    def test_returns_none_on_missing_fields(self):
        raw = '{"reflection": "x"}'   # 缺 tags
        self.assertIsNone(parse_reflection_response(raw))


class TestReflectOne(unittest.TestCase):
    def test_happy_path(self):
        stub = lambda _: ('{"reflection": "我把 chip 缺席解讀成偏空了", '
                          '"tags": ["chip_silent", "tech_strong"]}')
        lesson = reflect_one(fx_outcome(), fx_report_sections(), llm_call=stub)
        self.assertEqual(lesson["id"], "2026-05-04_2330")
        self.assertEqual(lesson["date"], "2026-05-04")
        self.assertEqual(lesson["symbol"], "2330")
        self.assertIn("chip 缺席", lesson["reflection"])
        self.assertEqual(lesson["tags"], ["chip_silent", "tech_strong"])
        self.assertEqual(lesson["outcome"]["verdict"], "missed_long")

    def test_llm_failure_returns_failed_marker(self):
        stub = lambda _: "[LLM timeout]"
        lesson = reflect_one(fx_outcome(), fx_report_sections(), llm_call=stub)
        self.assertEqual(lesson["id"], "2026-05-04_2330")
        self.assertTrue(lesson.get("reflect_failed"))
        self.assertIn("reason", lesson)

    def test_non_json_response_returns_failed_marker(self):
        stub = lambda _: "我覺得 trader 那天錯了"
        lesson = reflect_one(fx_outcome(), fx_report_sections(), llm_call=stub)
        self.assertTrue(lesson.get("reflect_failed"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
