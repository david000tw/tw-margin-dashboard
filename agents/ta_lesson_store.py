"""
TradingAgents-lite lesson store。

JSONL append-only,load 進 memory cache。query_candidates() 嚴格
walk-forward(lesson.date < before)。同 id 二次 append 取代(idempotent
給 ta_reflect 重跑用)。

落地檔: data/ta_lessons.jsonl
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
LESSONS_JSONL = DATA / "ta_lessons.jsonl"


class LessonStore:
    """Append-only lesson 持久層,walk-forward 查詢。"""

    def __init__(self, path: Path = LESSONS_JSONL):
        self._path = path
        self._cache: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                lesson = json.loads(line)
                self._cache[lesson["id"]] = lesson

    def append(self, lesson: dict) -> None:
        """寫入 lesson(同 id 取代)。atomic-ish:append 一行 + 重寫整檔去重。"""
        self._cache[lesson["id"]] = lesson
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for l in sorted(self._cache.values(), key=lambda x: x["date"]):
                f.write(json.dumps(l, ensure_ascii=False) + "\n")
        os.replace(tmp, self._path)

    def query_candidates(self, before: str) -> list[dict]:
        """回 lesson.date < before 的 lesson list,按 date 升序。"""
        return sorted(
            [l for l in self._cache.values() if l["date"] < before],
            key=lambda x: x["date"],
        )

    def exists(self, lesson_id: str) -> bool:
        return lesson_id in self._cache

    def all(self) -> list[dict]:
        """回所有 lesson(不過濾),按 date 升序。給 backfill 統計用。"""
        return sorted(self._cache.values(), key=lambda x: x["date"])
