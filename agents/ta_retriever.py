"""
TradingAgents-lite lesson retriever。

Protocol:
  retrieve(query, candidates, k) → list[dict] (top-k 相似 lesson)

實作:
  ClaudeRetriever:   用 claude -p 做 LLM-as-retriever ranking, 零安裝
  EmbeddingRetriever: sentence-transformers cosine sim, 需裝 ~1.5GB (Task 4)
  CompareRetriever:  兩個都跑 log 差異 (Task 5)

Walk-forward 不靠 retriever 守 — store 層已過濾 date<d 的 candidates,
retriever 純粹做相關度排序。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable, Protocol

BASE = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(BASE / "agents"))


class LessonRetriever(Protocol):
    def retrieve(
        self, query: str, candidates: list[dict], k: int,
    ) -> list[dict]:
        ...


def _format_candidates_numbered(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates, start=1):
        ref = (c.get("reflection") or "")[:200]
        lines.append(f"{i}. [{c.get('date')} {c.get('symbol', '?')}] {ref}")
    return "\n".join(lines)


def _parse_selected_json(raw: str) -> list[int]:
    if not raw:
        return []
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    indices = obj.get("selected")
    if not isinstance(indices, list):
        return []
    return [i for i in indices if isinstance(i, int)]


class ClaudeRetriever:
    """用 claude -p 從 candidates 挑 top-k 最相關的 lesson。

    優點:零安裝,重用既有 subprocess wrapper。
    缺點:慢(~10 秒/retrieval),會多一次 LLM call。
    """

    def __init__(self, llm_call: Callable[[str], str] | None = None):
        if llm_call is None:
            from predict import call_llm  # type: ignore[import-not-found]
            llm_call = lambda p: call_llm(p)
        self._llm_call = llm_call

    def retrieve(
        self, query: str, candidates: list[dict], k: int,
    ) -> list[dict]:
        if not candidates:
            return []
        prompt = self._build_prompt(query, candidates, k)
        raw = self._llm_call(prompt)
        indices = _parse_selected_json(raw)
        selected = []
        for idx in indices[:k]:
            if 1 <= idx <= len(candidates):
                selected.append(candidates[idx - 1])
        return selected

    def _build_prompt(self, query: str, candidates: list[dict], k: int) -> str:
        return f"""[SYSTEM]
你是 lesson 相關度評估員。你的任務:從一堆過去的 lesson 中,挑出 k 個跟「當前情境」最語意相關的。

[USER]
=== 當前情境 ===
{query}

=== 過去 lesson 候選(1-based 編號) ===
{_format_candidates_numbered(candidates)}

=== 任務 ===
從上述 {len(candidates)} 個 lesson 中挑 {k} 個跟當前情境最相關的。
只回 JSON,不要任何說明:
{{"selected": [3, 7, 15]}}
"""
