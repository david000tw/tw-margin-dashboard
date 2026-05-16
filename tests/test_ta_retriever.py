"""
agents/ta_retriever.py 測試。LLM 全部 stub。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from ta_retriever import ClaudeRetriever, EmbeddingRetriever, make_retriever   # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]


def fx_candidates() -> list[dict]:
    return [
        {"id": "2024-01-02_2330", "date": "2024-01-02", "reflection": "三榜皆空但漲 8%, 不該偏空"},
        {"id": "2024-01-05_1101", "date": "2024-01-05", "reflection": "技術面強勢但 bear 訊號失效"},
        {"id": "2024-01-08_2454", "date": "2024-01-08", "reflection": "MACD 黃金交叉初啟,可進"},
        {"id": "2024-01-10_2002", "date": "2024-01-10", "reflection": "警戒環境下三榜皆空多空難判"},
        {"id": "2024-01-12_3008", "date": "2024-01-12", "reflection": "融資減+法人買多次出現確實有效"},
    ]


class TestClaudeRetriever(unittest.TestCase):
    def test_parses_selected_indices(self):
        stub = lambda _: '{"selected": [1, 3, 5]}'
        r = ClaudeRetriever(llm_call=stub)
        result = r.retrieve("三榜皆空 + 技術強勢", fx_candidates(), k=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["id"], "2024-01-02_2330")
        self.assertEqual(result[1]["id"], "2024-01-08_2454")
        self.assertEqual(result[2]["id"], "2024-01-12_3008")

    def test_handles_out_of_range_indices(self):
        stub = lambda _: '{"selected": [1, 99, 3]}'
        r = ClaudeRetriever(llm_call=stub)
        result = r.retrieve("query", fx_candidates(), k=3)
        self.assertEqual(len(result), 2)
        self.assertEqual({l["id"] for l in result},
                         {"2024-01-02_2330", "2024-01-08_2454"})

    def test_handles_non_json_response(self):
        stub = lambda _: "我覺得 lesson 1 最像"
        r = ClaudeRetriever(llm_call=stub)
        result = r.retrieve("query", fx_candidates(), k=3)
        self.assertEqual(result, [])

    def test_empty_candidates(self):
        stub = lambda _: '{"selected": []}'
        r = ClaudeRetriever(llm_call=stub)
        result = r.retrieve("query", [], k=3)
        self.assertEqual(result, [])

    def test_k_caps_returned_count(self):
        stub = lambda _: '{"selected": [1, 2, 3, 4, 5]}'
        r = ClaudeRetriever(llm_call=stub)
        result = r.retrieve("query", fx_candidates(), k=2)
        self.assertEqual(len(result), 2)


class TestEmbeddingRetrieverWithStub(unittest.TestCase):
    """用 stub embedder 不真的 import sentence-transformers。"""

    def test_cosine_similarity_ranking(self):
        # stub embedder:把 text 轉成 128 維 one-hot(第一個字 char code 對應的 dim 設 1)
        # → 同首字 cosine=1.0,不同首字 cosine=0.0,符合 A-prefix-wins 直覺
        import numpy as np
        def stub_embed(texts):
            vecs = np.zeros((len(texts), 128), dtype=np.float32)
            for i, t in enumerate(texts):
                if t:
                    vecs[i, ord(t[0]) % 128] = 1.0
            return vecs

        r = EmbeddingRetriever(_embed_fn=stub_embed)
        # query "AB" 跟 candidates 比
        candidates = [
            {"id": "c1", "reflection": "AB lesson"},     # 開頭 A → 高相似
            {"id": "c2", "reflection": "XY lesson"},     # 開頭 X → 低相似
            {"id": "c3", "reflection": "AZ lesson"},     # 開頭 A → 也高
        ]
        result = r.retrieve("AB", candidates, k=2)
        self.assertEqual(len(result), 2)
        ids = {l["id"] for l in result}
        # c1 跟 c3 都是 A 開頭, 應入選
        self.assertIn("c1", ids)
        self.assertNotIn("c2", ids)

    def test_empty_candidates(self):
        import numpy as np
        r = EmbeddingRetriever(_embed_fn=lambda texts: np.zeros((len(texts), 2)))
        self.assertEqual(r.retrieve("query", [], k=3), [])


class TestEmbeddingRetrieverFallback(unittest.TestCase):
    """sentence-transformers 不存在時的 ImportError 處理。"""

    def test_make_retriever_falls_back_to_claude_when_st_missing(self):
        # 假裝 sentence-transformers 安裝失敗 → 應 fallback 回 ClaudeRetriever
        stub_llm = lambda _: '{"selected": [1]}'
        r = make_retriever("embedding", _force_embedding_fail=True, _claude_llm=stub_llm)
        self.assertIsInstance(r, ClaudeRetriever)


if __name__ == "__main__":
    unittest.main(verbosity=2)
