"""
agents/ta_runner.py 編排邏輯與輸出測試。LLM 全部 stub,不打 subprocess。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ta_runner import run_single_agent, run_pipeline  # type: ignore[import-not-found]  # noqa: E402  # pyright: ignore[reportUnusedImport]
from ta_features import SymbolFeatures  # type: ignore[import-not-found]  # noqa: E402


class TestRunSingleAgent(unittest.TestCase):
    def test_happy_path_returns_llm_text(self):
        stub = lambda _: "技術面看多,均線多頭排列。"
        out = run_single_agent("market", "prompt", stub)
        self.assertEqual(out, "技術面看多,均線多頭排列。")

    def test_timeout_marker_returns_fallback(self):
        stub = lambda _: "[LLM timeout]"
        out = run_single_agent("market", "prompt", stub)
        self.assertTrue(out.startswith("[LLM failed:"))
        self.assertIn("timeout", out.lower())

    def test_error_marker_returns_fallback(self):
        stub = lambda _: "[LLM error rc=1] xxx"
        out = run_single_agent("market", "prompt", stub)
        self.assertTrue(out.startswith("[LLM failed:"))

    def test_empty_response_returns_fallback(self):
        stub = lambda _: ""
        out = run_single_agent("market", "prompt", stub)
        self.assertTrue(out.startswith("[LLM failed:"))
        self.assertIn("empty", out.lower())


class TestRunPipeline(unittest.TestCase):
    def _make_features(self):
        """簡化 fixture,只填 ta_runner 需要的欄位。"""
        return SymbolFeatures(
            symbol="2330", ticker="2330.TW", target_date="2024-01-15",
            chip={"bull_count": 4, "bear_count": 1, "top5_count": 4,
                  "last_top5_date": "2024-01-12", "last_top5_rate": 170,
                  "bull_avg_rate": 166.25},
            price={"window_start": "2024-01-08", "window_end": "2024-01-12",
                   "closes": [615, 612, 618, 620, 625],
                   "ma5": 618.0, "ma20": 618.0, "ma_window": 618.0,
                   "return_window": 0.0163, "twii_return_window": 0.0052,
                   "excess_return_window": 0.0111},
            past_perf={"long_count": 2, "long_win_count": 1,
                       "short_count": 0, "short_win_count": 0},
            market_context={"recent_records": [{"date": "2024-01-12"}],
                            "twii": None},
        )

    def test_all_six_agents_called_in_order(self):
        calls: list[str] = []

        def stub(prompt: str) -> str:
            # 用 prompt 內容區分是哪個 agent(看 system 行的角色文字)
            for role, key in [
                ("技術分析師", "market"), ("籌碼分析師", "chip"),
                ("多方研究員", "bull"), ("空方研究員", "bear"),
                ("交易員", "trader"), ("風險經理", "risk"),
            ]:
                if role in prompt:
                    calls.append(key)
                    return f"[{key}] stub output"
            return "[unknown agent]"

        result = run_pipeline(self._make_features(), llm_call=stub)
        # 6 個 agent 都被呼叫
        self.assertEqual(calls, ["market", "chip", "bull", "bear", "trader", "risk"])
        self.assertEqual(result["status"], "ok")
        for key in ("market", "chip", "bull", "bear", "trader", "risk"):
            self.assertIn(key, result["outputs"])
            self.assertEqual(result["outputs"][key], f"[{key}] stub output")

    def test_prior_outputs_passed_to_later_agents(self):
        seen_prompts: dict[str, str] = {}

        def stub(prompt: str) -> str:
            for role, key in [("技術分析師", "market"), ("籌碼分析師", "chip"),
                              ("多方研究員", "bull"), ("空方研究員", "bear"),
                              ("交易員", "trader"), ("風險經理", "risk")]:
                if role in prompt:
                    seen_prompts[key] = prompt
                    return f"<<<{key}-output>>>"
            return ""

        run_pipeline(self._make_features(), llm_call=stub)
        # bull 看得到 market 的 output
        self.assertIn("<<<market-output>>>", seen_prompts["bull"])
        # bear 看得到 chip 的 output
        self.assertIn("<<<chip-output>>>", seen_prompts["bear"])
        # trader 看得到所有 stage 1+2 的 output
        for prior in ("market", "chip", "bull", "bear"):
            self.assertIn(f"<<<{prior}-output>>>", seen_prompts["trader"])
        # risk 看得到 trader
        self.assertIn("<<<trader-output>>>", seen_prompts["risk"])

    def test_single_agent_failure_continues_pipeline(self):
        def stub(prompt: str) -> str:
            if "籌碼分析師" in prompt:
                return "[LLM timeout]"
            return "ok output"

        result = run_pipeline(self._make_features(), llm_call=stub)
        # status 標 partial,後面 agent 還是有跑
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["outputs"]["chip"].startswith("[LLM failed:"))
        # trader 仍有 ok output(prior 內 chip section 會顯示 failed 標記但不阻斷)
        self.assertEqual(result["outputs"]["trader"], "ok output")

    def test_all_failed(self):
        stub = lambda _: "[LLM timeout]"
        result = run_pipeline(self._make_features(), llm_call=stub)
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
