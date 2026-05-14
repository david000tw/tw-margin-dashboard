"""
TradingAgents-lite 編排層:

  run_single_agent     單一 LLM call + 錯誤標記偵測
  run_pipeline         對單一 symbol 跑 stage 1→4 共 6 個 agent
  write_report         寫 markdown 報告 + summary entry

LLM 注入方式:呼叫 run_pipeline(features, llm_call=fn) 傳入 callable,
預設用 agents/predict.py.call_llm(走 claude -p subprocess)。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
REPORTS = DATA / "ta_reports"

sys.path.insert(0, str(BASE / "agents"))
sys.path.insert(0, str(BASE / "scripts"))

from ta_features import SymbolFeatures  # type: ignore[import-not-found]  # noqa: E402,F401  # pyright: ignore[reportUnusedImport]
from ta_prompts import build_market_analyst_prompt, build_chip_analyst_prompt, build_bull_researcher_prompt, build_bear_researcher_prompt, build_trader_prompt, build_risk_manager_prompt  # type: ignore[import-not-found]  # noqa: E402


def run_single_agent(name: str, prompt: str, llm_call) -> str:
    """
    呼叫一次 LLM。偵測 `agents/predict.py:call_llm` 約定的失敗標記
    ([LLM timeout] / [LLM error rc=X] ...)、空回應 → 回 `[LLM failed: <name>: <reason>]`。
    `name` 帶在訊息裡讓 caller 知道是哪個 agent 掛掉(stage 1→4 全跑掛時尤其有用)。

    不在這裡 retry,因為 predict.py 的 call_llm 本身已會吸收 timeout/error
    而是回字串標記,retry 由 caller 決定。
    """
    try:
        raw = llm_call(prompt)
    except Exception as e:
        return f"[LLM failed: {name}: {type(e).__name__}: {e}]"
    if not raw:
        return f"[LLM failed: {name}: empty response]"
    if raw.startswith("[LLM timeout]"):
        return f"[LLM failed: {name}: timeout]"
    if raw.startswith("[LLM error"):
        return f"[LLM failed: {name}: {raw}]"
    return raw.strip()


def run_pipeline(features: SymbolFeatures, *, llm_call) -> dict:
    """
    對單一 SymbolFeatures 跑 stage 1→4 共 6 個 agent。
    任何單一 agent 失敗都不會中斷後面;status 反映整體成功度。

    回傳:
      {
        "symbol": str, "ticker": str, "date": str,
        "outputs": {market, chip, bull, bear, trader, risk},
        "status": "ok" | "partial" | "failed",
      }

      ok      = 6 個都成功
      partial = 至少 1 個成功
      failed  = 6 個都 [LLM failed:...]
    """
    outputs: dict[str, str] = {}

    # Stage 1: 平行概念,sequential 實作
    outputs["market"] = run_single_agent(
        "market", build_market_analyst_prompt(features), llm_call,
    )
    outputs["chip"] = run_single_agent(
        "chip", build_chip_analyst_prompt(features), llm_call,
    )

    # Stage 2: bull/bear 看 stage 1
    prior_stage1 = {"market": outputs["market"], "chip": outputs["chip"]}
    outputs["bull"] = run_single_agent(
        "bull", build_bull_researcher_prompt(features, prior_stage1), llm_call,
    )
    outputs["bear"] = run_single_agent(
        "bear", build_bear_researcher_prompt(features, prior_stage1), llm_call,
    )

    # Stage 3: trader 看 stage 1+2
    prior_stage12 = {**prior_stage1, "bull": outputs["bull"], "bear": outputs["bear"]}
    outputs["trader"] = run_single_agent(
        "trader", build_trader_prompt(features, prior_stage12), llm_call,
    )

    # Stage 4: risk 看 stage 1+2+3
    prior_all = {**prior_stage12, "trader": outputs["trader"]}
    outputs["risk"] = run_single_agent(
        "risk", build_risk_manager_prompt(features, prior_all), llm_call,
    )

    failed = sum(1 for v in outputs.values() if v.startswith("[LLM failed:"))
    if failed == 0:
        status = "ok"
    elif failed == len(outputs):
        status = "failed"
    else:
        status = "partial"

    return {
        "symbol": features.symbol,
        "ticker": features.ticker,
        "date": features.target_date,
        "outputs": outputs,
        "status": status,
    }
