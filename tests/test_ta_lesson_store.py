"""
agents/ta_lesson_store.py 的測試。

walk-forward 不變量:query_candidates(before=d) 只回 lesson.date < d。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from ta_lesson_store import LessonStore   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_lesson(date: str, symbol: str, verdict: str = "right_hold") -> dict:
    return {
        "id": f"{date}_{symbol}",
        "date": date,
        "symbol": symbol,
        "ticker": f"{symbol}.TW",
        "outcome": {"verdict": verdict, "trader_action": "hold"},
        "reflection": f"模擬 lesson {date} {symbol}",
        "tags": ["test_tag"],
        "reflected_at": "2026-05-16T10:00:00",
    }


class TestLessonStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "test_lessons.jsonl"

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()
        if self.tmp.parent.exists():
            self.tmp.parent.rmdir()

    def test_append_and_load_roundtrip(self):
        store = LessonStore(path=self.tmp)
        l1 = fx_lesson("2024-01-02", "2330")
        store.append(l1)
        store2 = LessonStore(path=self.tmp)   # 重新 load
        loaded = store2.query_candidates(before="2025-01-01")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "2024-01-02_2330")

    def test_query_strict_before(self):
        store = LessonStore(path=self.tmp)
        store.append(fx_lesson("2024-01-02", "2330"))
        store.append(fx_lesson("2024-01-05", "2454"))
        store.append(fx_lesson("2024-01-10", "1101"))
        result = store.query_candidates(before="2024-01-05")
        ids = {l["id"] for l in result}
        # 01-02 ✓  01-05 排除(== before)  01-10 排除(> before)
        self.assertEqual(ids, {"2024-01-02_2330"})

    def test_exists_check(self):
        store = LessonStore(path=self.tmp)
        store.append(fx_lesson("2024-01-02", "2330"))
        self.assertTrue(store.exists("2024-01-02_2330"))
        self.assertFalse(store.exists("2024-01-02_9999"))

    def test_append_duplicate_id_replaces(self):
        # 同 id 二次 append 應該蓋掉 (idempotent reflect 用)
        store = LessonStore(path=self.tmp)
        store.append(fx_lesson("2024-01-02", "2330", verdict="wrong_direction"))
        store.append(fx_lesson("2024-01-02", "2330", verdict="right_hold"))
        result = store.query_candidates(before="2025-01-01")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["outcome"]["verdict"], "right_hold")

    def test_query_empty_store(self):
        store = LessonStore(path=self.tmp)
        self.assertEqual(store.query_candidates(before="2024-01-01"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
