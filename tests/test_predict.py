"""
agents/predict.py 的 walk-forward 切窗測試。

最重要:確保任何給定 d 的 prompt context 都不外漏 d 之後的資料。
這個測試是整個 AI 預測閉環的安全網,違反 = 整批回填白做。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
from predict import (   # type: ignore[import-not-found]  # noqa: E402
    slice_merged_strict,
    slice_twii_strict,
    past_verified_predictions,
    walk_forward_context,
    date_minus_trading_days,
    load_jsonl,
    append_jsonl,
    last_prediction_date,
)


# ── Fixtures ──────────────────────────────────────────────────

def fx_merged():
    """8 個交易日的 mini merged。"""
    return [
        {"date": "2024-01-02", "rate": 160, "bull": "2330,2454", "bear": "1101", "top5_margin_reduce_inst_buy": "2330"},
        {"date": "2024-01-03", "rate": 162, "bull": "2330", "bear": "1101", "top5_margin_reduce_inst_buy": "3034"},
        {"date": "2024-01-04", "rate": 165, "bull": "2454", "bear": "1102", "top5_margin_reduce_inst_buy": "2330"},
        {"date": "2024-01-05", "rate": 168, "bull": "3008", "bear": "2002", "top5_margin_reduce_inst_buy": "2330"},
        {"date": "2024-01-08", "rate": 171, "bull": "2330", "bear": "1101", "top5_margin_reduce_inst_buy": "3034"},
        {"date": "2024-01-09", "rate": 173, "bull": "2454", "bear": "2002", "top5_margin_reduce_inst_buy": "2330"},
        {"date": "2024-01-10", "rate": 175, "bull": "3008", "bear": "1102", "top5_margin_reduce_inst_buy": "2454"},
        {"date": "2024-01-11", "rate": 172, "bull": "2330", "bear": "1101", "top5_margin_reduce_inst_buy": "3008"},
    ]


def fx_twii():
    return {
        "2024-01-02": 17000.0,
        "2024-01-03": 17050.0,
        "2024-01-04": 17080.0,
        "2024-01-05": 17120.0,
        "2024-01-08": 17200.0,
        "2024-01-09": 17150.0,
        "2024-01-10": 17220.0,
        "2024-01-11": 17260.0,
    }


def fx_prices():
    """5 個 ticker × 8 dates,刻意做出 start>0 + 中間缺值。"""
    return {
        "dates": [
            "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
        ],
        "prices": {
            "2330.TW": {"start": 0, "csv": "600,602,605,610,615,612,618,620"},
            "2454.TW": {"start": 1, "csv": "880,883,890,895,900,905,910"},
            "1101.TW": {"start": 0, "csv": "40,40.5,41,41.2,40.8,40,39.8,39.5"},
            "3008.TW": {"start": 2, "csv": "2500,2520,2510,2530,2550,2540"},
            "2002.TW": {"start": 0, "csv": "23,23.2,23.5,23.3,23,22.8,22.5,22"},
        },
    }


def fx_predictions():
    """3 筆 prediction + 對應 T+max_h(=20 預設)的 outcome。
    為了測試方便,把 max_horizon=2 視為 'max',用 fx_prices 的 8 個交易日做估算。"""
    return [
        {"type": "prediction", "date": "2024-01-02", "horizons": [2], "long": [{"symbol": "2330"}], "short": [{"symbol": "1101"}]},
        {"type": "prediction", "date": "2024-01-03", "horizons": [2], "long": [{"symbol": "2454"}], "short": []},
        {"type": "prediction", "date": "2024-01-05", "horizons": [2], "long": [], "short": []},
        # outcomes:max_horizon=2 對應 T+2 收盤;在 fx_prices 中 d+2 仍 < 末日的才應被當作 verified
        {"type": "outcome", "date": "2024-01-02", "horizon": 2, "long_avg_excess": +0.01, "short_avg_excess": -0.02, "long_win": True, "short_win": True, "win": True, "verified_at": "2024-01-04"},
        {"type": "outcome", "date": "2024-01-03", "horizon": 2, "long_avg_excess": -0.005, "short_avg_excess": +0.01, "long_win": False, "short_win": False, "win": False, "verified_at": "2024-01-05"},
        {"type": "outcome", "date": "2024-01-05", "horizon": 2, "long_avg_excess": +0.02, "short_avg_excess": -0.03, "long_win": True, "short_win": True, "win": True, "verified_at": "2024-01-09"},
    ]


def fx_price_dates():
    """price_dates 給 walk-forward 用(8 個交易日)。"""
    return [
        "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
        "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
    ]


# ── Tests ─────────────────────────────────────────────────────

class TestSliceMergedStrict(unittest.TestCase):
    def test_excludes_d_itself(self):
        m = slice_merged_strict(fx_merged(), "2024-01-05")
        self.assertNotIn("2024-01-05", [r["date"] for r in m])
        self.assertEqual(m[-1]["date"], "2024-01-04")

    def test_n_recent_caps_length(self):
        m = slice_merged_strict(fx_merged(), "2024-01-11", n_recent=3)
        self.assertEqual(len(m), 3)
        self.assertEqual(m[0]["date"], "2024-01-08")

    def test_before_first_returns_empty(self):
        self.assertEqual(slice_merged_strict(fx_merged(), "2023-12-01"), [])


class TestSliceTwiiStrict(unittest.TestCase):
    def test_excludes_d(self):
        t = slice_twii_strict(fx_twii(), "2024-01-05")
        self.assertNotIn("2024-01-05", t)
        self.assertIn("2024-01-04", t)
        self.assertEqual(max(t.keys()), "2024-01-04")


class TestDateMinusTradingDays(unittest.TestCase):
    def test_basic(self):
        # price_dates idx: 0=01-02, 1=01-03, 2=01-04, 3=01-05, 4=01-08, 5=01-09, 6=01-10, 7=01-11
        # d=2024-01-09 (idx=5),退 2 個 → idx=3 → 2024-01-05
        self.assertEqual(date_minus_trading_days(fx_price_dates(), "2024-01-09", 2), "2024-01-05")

    def test_weekend_uses_prev_trading_day(self):
        # 2024-01-06 (週六) → idx=3 (2024-01-05),退 2 → idx=1 → 2024-01-03
        self.assertEqual(date_minus_trading_days(fx_price_dates(), "2024-01-06", 2), "2024-01-03")

    def test_underflow_returns_none(self):
        # d=2024-01-03 (idx=1),退 5 → idx=-4 → None
        self.assertIsNone(date_minus_trading_days(fx_price_dates(), "2024-01-03", 5))


class TestPastVerifiedPredictions(unittest.TestCase):
    """關鍵:用 prediction.date+max_h <= safe_cutoff 判定無 leak,而非 verified_at。"""

    def test_filters_leaky_prediction(self):
        """
        d=2024-01-04 (idx=2), max_horizon=2 → safe_cutoff=price_dates[0]=2024-01-02
        條件:prediction.date < safe_cutoff,即 < 01-02 → fx 中沒有
        """
        result = past_verified_predictions(
            fx_predictions(), "2024-01-04", fx_price_dates(),
            k=20, max_horizon=2,
        )
        self.assertEqual(result, [])

    def test_includes_safe_predictions(self):
        """
        d=2024-01-09 (idx=5), max_horizon=2 → safe_cutoff=price_dates[3]=2024-01-05
        條件:prediction.date < 2024-01-05
        - 01-02 prediction: 01-02 < 01-05 ✓ 入選
        - 01-03 prediction: 01-03 < 01-05 ✓ 入選
        - 01-05 prediction: 01-05 == safe_cutoff,排除(若入選其 T+2 outcome 收盤=d=01-09 leak)
        """
        result = past_verified_predictions(
            fx_predictions(), "2024-01-09", fx_price_dates(),
            k=20, max_horizon=2,
        )
        dates = [e["prediction"]["date"] for e in result]
        self.assertEqual(sorted(dates), ["2024-01-02", "2024-01-03"])

    def test_underflow_returns_empty(self):
        """d 太早,price_dates 中退 max_h 已超出範圍 → 沒有 safe prediction。"""
        result = past_verified_predictions(
            fx_predictions(), "2024-01-03", fx_price_dates(),
            k=20, max_horizon=20,
        )
        self.assertEqual(result, [])

    def test_k_caps_returned_count(self):
        """k 限制最多回幾筆。"""
        result = past_verified_predictions(
            fx_predictions(), "2024-01-09", fx_price_dates(),
            k=1, max_horizon=2,
        )
        # 取最近 1 筆
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["prediction"]["date"], "2024-01-03")


class TestWalkForwardContext(unittest.TestCase):
    def test_full_context_no_leak(self):
        """整合測試:walk_forward_context 回傳所有欄位都嚴格 < d。"""
        ctx = walk_forward_context(
            "2024-01-09",
            merged=fx_merged(),
            twii=fx_twii(),
            rows=fx_predictions(),
            price_dates=fx_price_dates(),
        )
        self.assertEqual(ctx.target_date, "2024-01-09")
        for r in ctx.recent_merged:
            self.assertLess(r["date"], "2024-01-09")
        self.assertLess(max(ctx.recent_twii.keys()), "2024-01-09")
        # feedback 用預設 max_horizon=20,fx_price_dates 太短會回 []
        # 改用較小 max 才能驗證
        ctx2 = walk_forward_context(
            "2024-01-09",
            merged=fx_merged(), twii=fx_twii(),
            rows=fx_predictions(), price_dates=fx_price_dates(),
        )
        # 用預設 (MAX_HORIZON=20) 在 8 天 fx 中應為空
        self.assertEqual(ctx2.feedback, [])


class TestJsonlIO(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp()) / "test.jsonl"

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()
        if self.tmp.parent.exists():
            self.tmp.parent.rmdir()

    def test_append_and_load_roundtrip(self):
        rows_in = [
            {"type": "prediction", "date": "2024-01-02", "long": [{"symbol": "2330"}]},
            {"type": "outcome", "date": "2024-01-02", "horizon": 5, "win": True},
        ]
        for r in rows_in:
            append_jsonl(r, self.tmp)
        rows_out = load_jsonl(self.tmp)
        self.assertEqual(rows_out, rows_in)

    def test_load_empty_file(self):
        self.assertEqual(load_jsonl(self.tmp), [])

    def test_last_prediction_date(self):
        for r in [
            {"type": "prediction", "date": "2024-01-02"},
            {"type": "outcome", "date": "2024-01-02", "horizon": 5},
            {"type": "prediction", "date": "2024-01-03"},
        ]:
            append_jsonl(r, self.tmp)
        self.assertEqual(last_prediction_date(self.tmp), "2024-01-03")

    def test_last_prediction_date_none_if_empty(self):
        self.assertIsNone(last_prediction_date(self.tmp))


class TestParseLlmResponse(unittest.TestCase):
    """LLM 輸出 parser:擷取 JSON block,結構不對回 None。"""

    def test_clean_json(self):
        from predict import parse_llm_response   # type: ignore[import-not-found]
        raw = '{"long":[{"symbol":"2330","conviction":0.8}],"short":[],"rationale":"x"}'
        d = parse_llm_response(raw)
        self.assertEqual(d["long"][0]["symbol"], "2330")

    def test_json_wrapped_with_text(self):
        from predict import parse_llm_response   # type: ignore[import-not-found]
        raw = '我推薦如下:\n```json\n{"long":[{"symbol":"2330"}],"short":[{"symbol":"1101"}]}\n```\n以上。'
        d = parse_llm_response(raw)
        self.assertIsNotNone(d)
        self.assertEqual(d["long"][0]["symbol"], "2330")
        # conviction 缺被填 0.5
        self.assertEqual(d["long"][0]["conviction"], 0.5)

    def test_missing_required_key(self):
        from predict import parse_llm_response   # type: ignore[import-not-found]
        self.assertIsNone(parse_llm_response('{"long":[]}'))   # 缺 short
        self.assertIsNone(parse_llm_response('{"foo":"bar"}'))

    def test_non_json(self):
        from predict import parse_llm_response   # type: ignore[import-not-found]
        self.assertIsNone(parse_llm_response("LLM 跑掛了沒給 JSON"))

    def test_long_not_list(self):
        from predict import parse_llm_response   # type: ignore[import-not-found]
        self.assertIsNone(parse_llm_response('{"long":"2330","short":[]}'))


class TestFilterToUniverse(unittest.TestCase):
    def test_keeps_only_universe_members(self):
        from predict import filter_to_universe  # type: ignore[import-not-found]
        parsed = {
            "long": [{"symbol": "2330"}, {"symbol": "9999"}],   # 9999 不在 universe
            "short": [{"symbol": "1101"}, {"symbol": "8888"}],
        }
        out = filter_to_universe(parsed, ["2330", "1101", "2454"])
        self.assertEqual([e["symbol"] for e in out["long"]], ["2330"])
        self.assertEqual([e["symbol"] for e in out["short"]], ["1101"])


class TestBuildUniverse(unittest.TestCase):
    def test_intersect_recent_records_and_sym2t(self):
        from predict import build_universe, walk_forward_context  # type: ignore[import-not-found]
        ctx = walk_forward_context(
            "2024-01-09", merged=fx_merged(), twii=fx_twii(),
            rows=[], price_dates=fx_price_dates(),
        )
        # sym2t 不含 1102/3008/2002 → 即使在 record 中也不入 universe
        sym2t = {"2330": "2330.TW", "2454": "2454.TW", "1101": "1101.TW", "9999": "9999.TW"}
        universe = build_universe(ctx, sym2t)
        # 9999 不在 record → 不在 universe
        self.assertNotIn("9999", universe)
        # 2330 在 record 且 sym2t 有對應 → 入選
        self.assertIn("2330", universe)
        # 1102 在 record 中但 sym2t 沒對應 → 不入選
        self.assertNotIn("1102", universe)


class TestPredictOneDay(unittest.TestCase):
    """完整 predict 流程,用 stub LLM 不打 subprocess。"""

    def _ctx(self, d="2024-01-09"):
        from predict import walk_forward_context  # type: ignore[import-not-found]
        return walk_forward_context(
            d, merged=fx_merged(), twii=fx_twii(),
            rows=fx_predictions(), price_dates=fx_price_dates(),
        )

    def test_happy_path(self):
        from predict import predict_one_day  # type: ignore[import-not-found]
        ctx = self._ctx()
        sym2t = {"2330": "2330.TW", "2454": "2454.TW", "1101": "1101.TW",
                 "3008": "3008.TW", "2002": "2002.TW"}
        stub = lambda p: '{"long":[{"symbol":"2330","conviction":0.7}],"short":[{"symbol":"1101","conviction":0.5}],"rationale":"test"}'
        p = predict_one_day("2024-01-09", ctx=ctx, sym2t=sym2t, _llm_call=stub)
        self.assertEqual(p["type"], "prediction")
        self.assertEqual(p["date"], "2024-01-09")
        self.assertEqual(p["long"][0]["symbol"], "2330")
        self.assertIn("context_summary", p)

    def test_non_json_response_fails_after_retry(self):
        from predict import predict_one_day  # type: ignore[import-not-found]
        ctx = self._ctx()
        sym2t = {"2330": "2330.TW", "2454": "2454.TW"}
        calls = [0]
        def stub(p):
            calls[0] += 1
            return "嗨我不會給 JSON"
        p = predict_one_day("2024-01-09", ctx=ctx, sym2t=sym2t, _llm_call=stub)
        self.assertEqual(p["type"], "prediction_failed")
        self.assertEqual(p["reason"], "non_json_output_after_retry")
        self.assertEqual(calls[0], 2)   # 一次原始 + 一次 retry

    def test_retry_recovers(self):
        from predict import predict_one_day  # type: ignore[import-not-found]
        ctx = self._ctx()
        sym2t = {"2330": "2330.TW", "2454": "2454.TW", "1101": "1101.TW", "3008": "3008.TW", "2002": "2002.TW"}
        calls = [0]
        def stub(p):
            calls[0] += 1
            if calls[0] == 1:
                return "亂講話"
            return '{"long":[{"symbol":"2330"}],"short":[{"symbol":"1101"}]}'
        p = predict_one_day("2024-01-09", ctx=ctx, sym2t=sym2t, _llm_call=stub)
        self.assertEqual(p["type"], "prediction")
        self.assertEqual(calls[0], 2)

    def test_empty_universe_short_circuits(self):
        from predict import predict_one_day, walk_forward_context  # type: ignore[import-not-found]
        ctx = walk_forward_context(
            "2024-01-09", merged=[], twii={}, rows=[], price_dates=[],
        )
        called = [False]
        def stub(p):
            called[0] = True
            return '{"long":[],"short":[]}'
        p = predict_one_day("2024-01-09", ctx=ctx, sym2t={}, _llm_call=stub)
        self.assertEqual(p["type"], "prediction_failed")
        self.assertIn("empty_universe", p["reason"])
        self.assertFalse(called[0], "empty universe 不應呼叫 LLM")

    def test_filters_out_of_universe_recommendations(self):
        from predict import predict_one_day  # type: ignore[import-not-found]
        ctx = self._ctx()
        sym2t = {"2330": "2330.TW", "2454": "2454.TW", "1101": "1101.TW", "3008": "3008.TW", "2002": "2002.TW"}
        # LLM 推薦含一個 universe 外的 9999
        stub = lambda p: '{"long":[{"symbol":"2330"},{"symbol":"9999"}],"short":[{"symbol":"1101"}]}'
        p = predict_one_day("2024-01-09", ctx=ctx, sym2t=sym2t, _llm_call=stub)
        long_syms = [e["symbol"] for e in p["long"]]
        self.assertIn("2330", long_syms)
        self.assertNotIn("9999", long_syms)


class TestVerifyPredictions(unittest.TestCase):
    """T+N outcome 計算 + idempotent 重跑。"""

    def test_compute_outcome_long_win(self):
        from verify_predictions import compute_outcome   # type: ignore[import-not-found]
        # 假設 d=2024-01-02 (idx 0),h=2 → idx 2 = 2024-01-04
        # 2330.TW: csv="600,602,605,..."  → p0=600, p2=605, ret=+0.833%
        # TWII: 17000 → 17080, twii_ret=+0.471%
        # excess = +0.0083 - 0.0047 = +0.0036 > 0 → long_win=True
        p = {"date": "2024-01-02", "horizons": [2],
             "long": [{"symbol": "2330"}], "short": [{"symbol": "1101"}]}
        prices = fx_prices()
        twii = fx_twii()
        twii_dates = sorted(twii.keys())
        sym2t = {"2330": "2330.TW", "1101": "1101.TW"}
        out = compute_outcome(p, 2, prices=prices, twii=twii,
                              twii_dates=twii_dates, sym2t=sym2t)
        self.assertIsNotNone(out)
        self.assertEqual(out["type"], "outcome")
        self.assertEqual(out["horizon"], 2)
        self.assertTrue(out["long_win"], f"long_avg_excess={out['long_avg_excess']}")
        self.assertEqual(out["long_n_resolved"], 1)
        # 1101: 40 → 41, ret=+2.5%; excess = +2.5% - 0.471% = ~+2% > 0 → short_win=False
        self.assertFalse(out["short_win"])
        self.assertTrue(out["win"])    # long_win=True 即 win

    def test_compute_outcome_horizon_not_reached(self):
        from verify_predictions import compute_outcome   # type: ignore[import-not-found]
        # d=2024-01-11 (末日 idx 7),h=5 → step_forward 卡在 idx 7,沒前進 → None
        p = {"date": "2024-01-11", "horizons": [5],
             "long": [{"symbol": "2330"}], "short": []}
        out = compute_outcome(p, 5, prices=fx_prices(), twii=fx_twii(),
                              twii_dates=sorted(fx_twii().keys()),
                              sym2t={"2330": "2330.TW"})
        self.assertIsNone(out)

    def test_compute_outcome_short_win_negative_excess(self):
        from verify_predictions import compute_outcome   # type: ignore[import-not-found]
        # 偽造一筆 short 推薦,看 short_win 邏輯方向
        # 1101: 40 → 41 ret=+2.5%, twii=+0.471% → excess=+2% > 0 → short 應算 lose(預期跌)
        p = {"date": "2024-01-02", "horizons": [2],
             "long": [], "short": [{"symbol": "1101"}]}
        out = compute_outcome(p, 2, prices=fx_prices(), twii=fx_twii(),
                              twii_dates=sorted(fx_twii().keys()),
                              sym2t={"1101": "1101.TW"})
        self.assertFalse(out["short_win"])
        self.assertFalse(out["win"])

    def test_run_idempotent(self):
        """重跑兩次,outcome 行不重複。"""
        import tempfile
        from verify_predictions import run   # type: ignore[import-not-found]
        tmp = Path(tempfile.mkdtemp()) / "test.jsonl"
        try:
            # 寫一筆 prediction
            append_jsonl({"type": "prediction", "date": "2024-01-02",
                          "horizons": [2], "long": [{"symbol": "2330"}],
                          "short": [{"symbol": "1101"}]}, tmp)
            sym2t = {"2330": "2330.TW", "1101": "1101.TW"}
            r1 = run(prices=fx_prices(), twii=fx_twii(), sym2t=sym2t, jsonl=tmp, verbose=False)
            self.assertEqual(r1["appended"], 1)
            r2 = run(prices=fx_prices(), twii=fx_twii(), sym2t=sym2t, jsonl=tmp, verbose=False)
            self.assertEqual(r2["appended"], 0)   # 第二次不重寫
            self.assertEqual(r2["skipped_existing"], 1)
        finally:
            if tmp.exists():
                tmp.unlink()
            tmp.parent.rmdir()


if __name__ == "__main__":
    unittest.main(verbosity=2)
