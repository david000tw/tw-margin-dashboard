"""
AI 預測閉環:每日呼叫 LLM 給 long/short 推薦,寫進 data/ai_predictions.jsonl;
backfill 模式可對歷史 1182 天回填,**嚴格 walk-forward**(不看任何 d 之後的資料)。

Phase A(本檔下半):walk_forward_context() 與相關 slicing — 不依賴 LLM,可獨立測試
Phase B(本檔上半):predict_one_day() 呼叫 ClaudeCodeLLM、append_prediction 寫 jsonl

Walk-forward 不變量(violation 會讓回填整批白做):
  對日期 d 的 prediction,prompt context 只能來自:
    1. merged 中 record.date < d
    2. twii 中 date < d
    3. prices.dates 索引到 < d 的部分(csv 對應切)
    4. ai_predictions.jsonl 中 prediction.date < d 且 outcome.verified_at < d
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
JSONL = DATA / "ai_predictions.jsonl"

# 共享 helpers
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))
from pipeline import load_json, save_json, _atomic_write_text   # type: ignore[import-not-found]  # noqa: E402,F401
from analyze_signals import (                                    # type: ignore[import-not-found]  # noqa: E402
    find_idx_on_or_before, step_forward, read_price,
    get_close_at_idx, compute_excess_return,
)
from symbol_resolve import SIDE_CONFIG                           # type: ignore[import-not-found]  # noqa: E402,F401


# ── Phase A:walk-forward 切窗 ──────────────────────────────────

MAX_HORIZON = 20      # 雲端 plan:用 PRIMARY_HORIZON 當 leak 判定保守上界


@dataclass(frozen=True)
class WalkForwardContext:
    """日期 d 的 prompt 可見資料(嚴格 < d)。價格不進 prompt — verify 階段才用。"""
    target_date: str
    recent_merged: list[dict]              # 最近 30 天 record(< d)
    recent_twii: dict[str, float]          # < d 的 TWII
    feedback: list[dict]                   # 過去已 verified 的 prediction-outcome 配對


def slice_merged_strict(merged: list[dict], d: str, *, n_recent: int = 30) -> list[dict]:
    """回傳近 n 天 < d 的 record(時序排序)。"""
    earlier = [r for r in merged if r["date"] < d]
    return earlier[-n_recent:]


def slice_twii_strict(twii: dict[str, float], d: str) -> dict[str, float]:
    """{date → close} 中保留 < d 的 entries。"""
    return {k: v for k, v in twii.items() if k < d}


def date_minus_trading_days(price_dates: list[str], d: str, n: int) -> str | None:
    """從 d 在 price_dates 中的位置往前退 n 個交易日,回傳該日期(ISO)。退超界回 None。"""
    base_idx = find_idx_on_or_before(price_dates, d)
    if base_idx < 0:
        return None
    target = base_idx - n
    if target < 0:
        return None
    return price_dates[target]


def past_verified_predictions(
    rows: list[dict], d: str, price_dates: list[str], *,
    k: int = 20, max_horizon: int = MAX_HORIZON,
) -> list[dict]:
    """
    從 jsonl 已讀的 rows 撈最近 k 筆**已可安全餵 prompt** 的 prediction+outcome。

    判定條件(雲端 plan 修正版,比 verified_at<d 更嚴謹):
      prediction.date 必須早到「outcome 計算用到的最遠 T+max_horizon 收盤日仍 < d」
      等價於 prediction.date <= date_minus_trading_days(d, max_horizon)

    這樣才確保餵進 prompt 的 outcome 沒看到 d 當日或之後的任何價格。

    回傳 list of {prediction, outcome_h_max} (按時序,取最近 k 筆)。
    """
    safe_cutoff = date_minus_trading_days(price_dates, d, max_horizon)
    if safe_cutoff is None:
        return []

    outcomes_idx: dict[tuple[str, int], dict] = {}
    for row in rows:
        if row.get("type") == "outcome":
            outcomes_idx[(row["date"], row["horizon"])] = row

    # safe_cutoff = price_dates[idx_of(d) - max_horizon]
    # 條件:prediction.date < safe_cutoff(嚴格小於),否則該 prediction 的
    # T+max_h outcome 收盤日 == d,leak 進 prompt
    enriched: list[dict] = []
    for row in rows:
        if row.get("type") != "prediction":
            continue
        pdate = row["date"]
        if pdate >= safe_cutoff:
            continue
        out_h = outcomes_idx.get((pdate, max_horizon))
        if out_h is None:
            continue
        enriched.append({"prediction": row, "outcome_h_max": out_h})

    return enriched[-k:]


def walk_forward_context(
    d: str,
    *,
    merged: list[dict],
    twii: dict[str, float],
    rows: list[dict],
    price_dates: list[str],
    n_recent_merged: int = 30,
    feedback_k: int = 20,
) -> WalkForwardContext:
    """組整套 walk-forward context;不切 prices(prompt 不需要價格)。"""
    return WalkForwardContext(
        target_date=d,
        recent_merged=slice_merged_strict(merged, d, n_recent=n_recent_merged),
        recent_twii=slice_twii_strict(twii, d),
        feedback=past_verified_predictions(rows, d, price_dates, k=feedback_k),
    )


# ── jsonl 讀寫 ────────────────────────────────────────────────

def load_jsonl(path: Path = JSONL) -> list[dict]:
    """讀整個 jsonl;不存在回 []。"""
    if not path.exists():
        return []
    import json
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(row: dict, path: Path = JSONL) -> None:
    """append 1 行,os.fsync 確保斷電也保得住。"""
    import json
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def last_prediction_date(path: Path = JSONL) -> str | None:
    """掃整個 jsonl 找 type=prediction 的最後一筆 date(用於 backfill resume)。"""
    rows = load_jsonl(path)
    pdates = [r["date"] for r in rows if r.get("type") == "prediction"]
    return max(pdates) if pdates else None


# ── Phase B:LLM call + parser ─────────────────────────────────

DEFAULT_HORIZONS = [5, 10, 20]


def call_llm(prompt: str, *, model: str = "sonnet", timeout: int = 300) -> str:
    """
    直接 subprocess `claude -p` 走 Claude Code 訂閱(免 API key)。
    參考 agents/claude_code_llm.py:60-86 的呼叫,不依賴 crewai。
    timeout 內回 stdout;timeout / 非零 exit code 回標記字串(不 raise)。
    """
    import os
    import subprocess
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "text",
        "--tools", "",
        "--append-system-prompt",
        "You are serving as a pure language model. Respond directly without using any tools.",
    ]
    try:
        result = subprocess.run(
            cmd, input=prompt, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "[LLM timeout]"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[:500]
        return f"[LLM error rc={result.returncode}] {err}"
    return result.stdout.strip()


def build_universe(ctx: WalkForwardContext, sym2t: dict) -> list[str]:
    """
    LLM 可推薦的 symbol 候選:近 30 天 record 出現過 ∩ sym2t 有對應 ticker。
    sym2t 已經是 fetch_prices.py 確認過 yfinance 抓得到價的子集,verify 時
    都拿得到 T+N 收盤;也避免 LLM 推稀有 / OCR 錯字 symbol。
    """
    syms: set[str] = set()
    for r in ctx.recent_merged:
        for fld in ("bull", "bear", "top5_margin_reduce_inst_buy"):
            for s in (r.get(fld) or "").split(","):
                s = s.strip()
                if not s:
                    continue
                t = sym2t.get(s) or sym2t.get(s.rstrip("*"))
                if t:
                    syms.add(s)
    return sorted(syms)


def build_prompt(ctx: WalkForwardContext, universe: list[str]) -> str:
    """組 LLM prompt(系統 + user)。輸出單一字串給 ClaudeCodeLLM。"""
    d = ctx.target_date
    L: list[str] = []

    L.append("[SYSTEM]")
    L.append(
        "你是台股量化分析師。基於提供的籌碼面資料(scantrader 整理的外資借券 + "
        "融資資訊)與最近你自己的預測 vs 實際結果,給出今日的 long 5 / short 5 推薦。"
    )
    L.append("")
    L.append(f"只能從以下代號中選股(共 {len(universe)} 檔,皆為近 30 天活躍且有股價資料):")
    L.append(", ".join(universe))
    L.append("")
    L.append("輸出嚴格 JSON,不要在 JSON 之外加任何說明文字、不要 markdown 標題、不要 code block:")
    L.append('{"long":[{"symbol":"2330","conviction":0.7},...×5],')
    L.append(' "short":[{"symbol":"1101","conviction":0.5},...×5],')
    L.append(' "rationale":"<200 字"}')
    L.append("")
    L.append("conviction 是 0-1 的信心,反映你對該檔在 T+5/10/20 內超越大盤的把握。")
    L.append("")

    L.append("[USER]")
    L.append(f"=== 預測日 d={d} ===")
    L.append("")

    L.append(f"## 最近 {len(ctx.recent_merged)} 天 record")
    L.append("date        rate  bull(看多代號)              bear(看空代號)              top5(融資減+法人買)")
    for r in ctx.recent_merged:
        L.append(
            f"{r['date']}  {r['rate']:>3}  "
            f"{(r.get('bull') or '')[:60]:60}  "
            f"{(r.get('bear') or '')[:60]:60}  "
            f"{(r.get('top5_margin_reduce_inst_buy') or '')[:40]}"
        )
    L.append("")

    twii_dates = sorted(ctx.recent_twii.keys())[-30:]
    if twii_dates:
        first, last = twii_dates[0], twii_dates[-1]
        first_v, last_v = ctx.recent_twii[first], ctx.recent_twii[last]
        L.append(
            f"## TWII 走勢: {first} ({first_v:.0f}) → {last} ({last_v:.0f}) "
            f"({(last_v/first_v - 1)*100:+.2f}%)"
        )
        L.append("")

    if ctx.feedback:
        L.append("## 過去你的預測與 T+20 實際結果(按時序)")
        for entry in ctx.feedback:
            p = entry["prediction"]
            o = entry["outcome_h_max"]
            longs = ",".join(x.get("symbol", "") for x in p.get("long", []))
            shorts = ",".join(x.get("symbol", "") for x in p.get("short", []))
            la = o.get("long_avg_excess", 0.0)
            sa = o.get("short_avg_excess", 0.0)
            lw = "Lwin" if o.get("long_win") else "Llose"
            sw = "Swin" if o.get("short_win") else "Slose"
            L.append(f"  {p['date']}  L=[{longs}]  S=[{shorts}]  T+20: L{la:+.3f}/{lw} S{sa:+.3f}/{sw}")
        L.append("")
        L.append("請反思 lose case 的共同模式,調整今日推薦。")
        L.append("")

    L.append(f"請輸出 d={d} 的推薦 JSON,長 5 短 5,只用 universe 內的代號。")
    return "\n".join(L)


def parse_llm_response(text: str) -> dict | None:
    """擷取 LLM 輸出的第一個 JSON block;結構不對回 None。"""
    import json
    import re
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "long" not in obj or "short" not in obj:
        return None
    if not isinstance(obj["long"], list) or not isinstance(obj["short"], list):
        return None
    for entries in (obj["long"], obj["short"]):
        for e in entries:
            if not isinstance(e, dict) or "symbol" not in e:
                return None
            e.setdefault("conviction", 0.5)
    obj.setdefault("rationale", "")
    return obj


def filter_to_universe(parsed: dict, universe: list[str]) -> dict:
    """LLM 偶爾會推 universe 之外的代號,這裡刪掉。回傳清理後的 dict。"""
    uset = set(universe)
    parsed["long"] = [e for e in parsed["long"] if e.get("symbol") in uset]
    parsed["short"] = [e for e in parsed["short"] if e.get("symbol") in uset]
    return parsed


def predict_one_day(
    d: str,
    *,
    ctx: WalkForwardContext,
    sym2t: dict,
    model: str = "sonnet",
    horizons: list[int] | None = None,
    timeout: int = 180,
    _llm_call=None,   # 注入用,測試時可塞 stub
) -> dict:
    """
    主入口:給定 d 與已切好的 walk-forward ctx,呼叫 LLM 拿推薦,回傳 prediction dict。
    LLM 失敗(timeout / 非 JSON / 兩次重試都失敗)會回 type=prediction_failed 不會 raise。
    """
    from datetime import datetime
    horizons = horizons or DEFAULT_HORIZONS
    universe = build_universe(ctx, sym2t)
    if not universe:
        return {
            "type": "prediction_failed", "date": d, "model": model,
            "reason": "empty_universe(沒有近 30 天活躍且有股價的標的)",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    prompt = build_prompt(ctx, universe)

    if _llm_call is None:
        _llm_call = lambda p: call_llm(p, model=model, timeout=timeout)

    try:
        raw = _llm_call(prompt)
    except Exception as e:
        return {
            "type": "prediction_failed", "date": d, "model": model,
            "reason": f"llm_call_error: {type(e).__name__}: {e}",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    parsed = parse_llm_response(raw)
    if parsed is None:
        try:
            raw2 = _llm_call(prompt + "\n\n[再次強調] 只輸出單一 JSON object,不要任何其他文字。")
        except Exception as e:
            return {
                "type": "prediction_failed", "date": d, "model": model,
                "reason": f"llm_retry_error: {type(e).__name__}: {e}",
                "raw_excerpt": (raw or "")[:300],
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        parsed = parse_llm_response(raw2)

    if parsed is None:
        return {
            "type": "prediction_failed", "date": d, "model": model,
            "reason": "non_json_output_after_retry",
            "raw_excerpt": (raw or "")[:500],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    parsed = filter_to_universe(parsed, universe)
    return {
        "type": "prediction",
        "date": d,
        "model": model,
        "horizons": horizons,
        "long": parsed["long"],
        "short": parsed["short"],
        "rationale": parsed.get("rationale", "")[:300],
        "context_summary": {
            "recent_merged_n": len(ctx.recent_merged),
            "feedback_n": len(ctx.feedback),
            "universe_size": len(universe),
        },
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def append_prediction(p: dict, path: Path = JSONL) -> None:
    append_jsonl(p, path)
