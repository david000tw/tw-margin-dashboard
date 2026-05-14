"""
agents/ta_runner.py 編排邏輯與輸出測試。LLM 全部 stub,不打 subprocess。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ta_runner import run_single_agent  # type: ignore[import-not-found]  # noqa: E402  # pyright: ignore[reportUnusedImport]


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
