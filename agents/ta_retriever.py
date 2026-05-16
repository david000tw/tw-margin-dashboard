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
import warnings
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol, Sequence

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


class EmbeddingRetriever:
    """sentence-transformers cosine sim retriever。

    第一次用 lazy load 模型 (~2-5 sec)。需要先:
        pip install sentence-transformers
    沒裝會 raise ImportError, caller(make_retriever)會 fallback 到 ClaudeRetriever。
    """

    def __init__(
        self,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
        _embed_fn=None,
    ):
        self._model_name = model_name
        if _embed_fn is not None:
            # 測試注入用
            self._embed_fn = _embed_fn
            self._model = None
        else:
            self._model = None  # lazy load
            self._embed_fn = None

    def _ensure_model(self):
        if self._embed_fn is not None or self._model is not None:
            return
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        self._model = SentenceTransformer(self._model_name)
        self._embed_fn = lambda texts: self._model.encode(  # type: ignore[union-attr]
            list(texts), show_progress_bar=False, convert_to_numpy=True,
        )

    def _embed(self, texts: Sequence[str]):
        self._ensure_model()
        assert self._embed_fn is not None
        return self._embed_fn(texts)

    def retrieve(
        self, query: str, candidates: list[dict], k: int,
    ) -> list[dict]:
        if not candidates:
            return []
        import numpy as np
        cand_texts = [(c.get("reflection") or "")[:500] for c in candidates]
        vecs = self._embed([query] + cand_texts)
        q_vec = vecs[0]
        c_vecs = vecs[1:]
        # cosine = dot / (|q|*|c|)
        q_norm = np.linalg.norm(q_vec) + 1e-9
        c_norms = np.linalg.norm(c_vecs, axis=1) + 1e-9
        scores = (c_vecs @ q_vec) / (c_norms * q_norm)
        top_idx = np.argsort(-scores)[:k]
        return [candidates[i] for i in top_idx]


COMPARE_LOG = BASE / "data" / "retriever_compare.jsonl"


class CompareRetriever:
    """同時跑 A (embedding) + B (claude),log 差異到 jsonl, 回 primary 的結果。"""

    def __init__(
        self,
        a: LessonRetriever | None,
        b: LessonRetriever,
        primary: str = "claude",
        log_path: Path = COMPARE_LOG,
    ):
        self._a = a
        self._b = b
        self._primary = primary
        self._log_path = log_path

    def retrieve(
        self, query: str, candidates: list[dict], k: int,
    ) -> list[dict]:
        result_a = self._a.retrieve(query, candidates, k) if self._a else []
        result_b = self._b.retrieve(query, candidates, k)
        a_ids = [l.get("id") for l in result_a]
        b_ids = [l.get("id") for l in result_b]
        overlap = len(set(a_ids) & set(b_ids))
        self._log({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "query": query,
            "candidate_count": len(candidates),
            "k": k,
            "primary": self._primary,
            "a_picked": a_ids,
            "b_picked": b_ids,
            "overlap": overlap,
        })
        return result_a if self._primary == "embedding" else result_b

    def _log(self, entry: dict) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def make_retriever(
    name: str,
    *,
    primary: str = "claude",
    _force_embedding_fail: bool = False,
    _claude_llm=None,
) -> LessonRetriever:
    """retriever 工廠。name in {"claude", "embedding", "compare", "none"}。

    name="embedding" 但裝套件失敗 → fallback to ClaudeRetriever + warning。
    name="compare" → CompareRetriever (a=Embedding, b=Claude, 回 primary 結果)。
    """
    if name == "claude":
        return ClaudeRetriever(llm_call=_claude_llm)
    if name == "embedding":
        try:
            if _force_embedding_fail:
                raise ImportError("forced for test")
            r = EmbeddingRetriever()
            r._ensure_model()   # 立即 trigger lazy load,失敗才能在這 catch
            return r
        except ImportError as e:
            warnings.warn(
                f"sentence-transformers 不可用 ({e}),"
                " fallback 到 ClaudeRetriever。"
                " 安裝指令: pip install sentence-transformers"
            )
            return ClaudeRetriever(llm_call=_claude_llm)
    if name == "compare":
        try:
            if _force_embedding_fail:
                raise ImportError("forced for test")
            a: LessonRetriever | None = EmbeddingRetriever()
            a._ensure_model()  # type: ignore[attr-defined]
        except ImportError:
            warnings.warn("compare 模式 embedding 不可用,a=None,只跑 b")
            a = None
        b = ClaudeRetriever(llm_call=_claude_llm)
        return CompareRetriever(a=a, b=b, primary=primary)
    if name == "none":
        class _NullRetriever:
            def retrieve(self, query, candidates, k):  # pyright: ignore[reportUnusedParameter]
                return []
        return _NullRetriever()
    raise ValueError(f"unknown retriever name: {name}")
