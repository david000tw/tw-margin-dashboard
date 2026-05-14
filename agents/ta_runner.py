"""
TradingAgents-lite 編排層:

  run_single_agent     單一 LLM call + 錯誤標記偵測
  run_pipeline         對單一 symbol 跑 stage 1→4 共 6 個 agent
  write_report         寫 markdown 報告 + summary entry

LLM 注入方式:呼叫 run_pipeline(features, llm_call=fn) 傳入 callable,
預設用 agents/predict.py.call_llm(走 claude -p subprocess)。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
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


# ── Markdown / summary 輸出 ────────────────────────────────────

_ROLE_LABELS = [
    ("market", "技術分析師"),
    ("chip", "籌碼分析師"),
    ("bull", "多方研究員"),
    ("bear", "空方研究員"),
    ("trader", "交易員"),
    ("risk", "風險經理"),
]


def write_report(result: dict, *, base_dir: Path = REPORTS) -> Path:
    """
    把 run_pipeline 的 result 寫成 markdown,並更新 summary.json。
    回傳 markdown 檔路徑。

    summary.json 中的 `report_path` 是相對於 base_dir 的路徑
    (例如 "2024-01-15/2330.md"),dashboard 端可直接拼 base_dir 取檔。
    """
    date = result["date"]
    symbol = result["symbol"]
    outdir = base_dir / date
    outdir.mkdir(parents=True, exist_ok=True)

    # ─ markdown ─
    md_lines = [
        f"# {symbol} ({result['ticker']}) 深度分析  {date}",
        "",
        f"STATUS: {result['status']}",
        f"GENERATED_AT: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    for key, label in _ROLE_LABELS:
        md_lines.append(f"## {label}")
        md_lines.append("")
        md_lines.append(result["outputs"].get(key, "(無)"))
        md_lines.append("")

    md_path = outdir / f"{symbol}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    # ─ summary.json (append entry,保留既有的) ─
    sum_path = outdir / "summary.json"
    if sum_path.exists():
        summary = json.loads(sum_path.read_text(encoding="utf-8"))
    else:
        summary = {"date": date, "entries": []}

    # 同一 symbol 重跑時取代既有 entry
    summary["entries"] = [e for e in summary["entries"] if e["symbol"] != symbol]
    summary["entries"].append({
        "symbol": symbol, "ticker": result["ticker"],
        "status": result["status"],
        "report_path": str(md_path.relative_to(base_dir)),
    })
    summary["last_updated"] = datetime.now().isoformat(timespec="seconds")
    sum_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    return md_path
