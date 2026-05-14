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

from ta_features import SymbolFeatures  # noqa: E402, F401  # pyright: ignore[reportUnusedImport]
from ta_prompts import (                # noqa: E402, F401  # pyright: ignore[reportUnusedImport]
    build_market_analyst_prompt, build_chip_analyst_prompt,
    build_bull_researcher_prompt, build_bear_researcher_prompt,
    build_trader_prompt, build_risk_manager_prompt,
)


def run_single_agent(name: str, prompt: str, llm_call) -> str:
    """
    呼叫一次 LLM。偵測 `agents/predict.py:call_llm` 約定的失敗標記
    ([LLM timeout] / [LLM error rc=X] ...)、空回應 → 回 `[LLM failed: <reason>]`。
    上層收到 `[LLM failed:` 開頭就知道這個 agent 沒輸出實質內容。

    不在這裡 retry,因為 predict.py 的 call_llm 本身已會吸收 timeout/error
    而是回字串標記,retry 由 caller 決定。
    """
    try:
        raw = llm_call(prompt)
    except Exception as e:
        return f"[LLM failed: {type(e).__name__}: {e}]"
    if not raw:
        return "[LLM failed: empty response]"
    if raw.startswith("[LLM timeout]"):
        return "[LLM failed: timeout]"
    if raw.startswith("[LLM error"):
        return f"[LLM failed: {raw}]"
    return raw.strip()
