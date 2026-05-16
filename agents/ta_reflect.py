"""
TradingAgents-lite reflection 層。

對 ta_outcome 寫的 outcome,組 prompt 餵 LLM,解析 JSON 輸出寫進
LessonStore。Idempotent — 已 reflected 的 outcome skip。

落地檔: data/ta_lessons.jsonl (via LessonStore)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

sys.path.insert(0, str(BASE / "agents"))

from ta_lesson_store import LessonStore   # type: ignore[import-not-found]  # noqa: E402
from ta_outcome import parse_report_md   # type: ignore[import-not-found]  # noqa: E402


def build_reflection_prompt(outcome: dict, report_sections: dict[str, str]) -> str:
    """組反思 prompt。outcome 含 3 個 horizon excess + verdict;
    report_sections 含 6 個 agent 的 markdown text。"""
    return f"""[SYSTEM]
你是台股分析師團隊的「教練」。針對單一交易決策的事後結果,
寫一段反思,幫 Trader 下次避免犯類似錯誤。

[USER]
=== 決策回顧 ===
日期: {outcome['date']}  標的: {outcome['symbol']}
Trader 那天:
  ACTION: {outcome['trader_action']}
  CONVICTION: {outcome['trader_conviction']}
  HORIZON: {outcome['trader_horizon']}
  RATIONALE 摘要: {outcome.get('trader_rationale_excerpt', '')}

=== 實際結果 ===
T+5 excess return: {outcome['actual_excess_t5']*100:+.2f}%
T+10 excess return: {outcome['actual_excess_t10']*100:+.2f}%
T+20 excess return: {outcome.get('actual_excess_t20', 0)*100:+.2f}%
verdict: {outcome['verdict']}

=== 同日其他 agent 報告摘要 ===
[Market Analyst] {report_sections.get('market', '(無)')[:300]}
[Chip Analyst]  {report_sections.get('chip', '(無)')[:300]}
[Bull]          {report_sections.get('bull', '(無)')[:300]}
[Bear]          {report_sections.get('bear', '(無)')[:300]}
[Risk Manager]  {report_sections.get('risk', '(無)')[:300]}

=== 你的任務 ===
1. 用繁體中文 200-300 字反思:
   - Trader 判斷哪裡對 / 哪裡錯
   - 是哪個上游 agent (Market/Chip/Bull/Bear) 把 Trader 帶歪
   - 下次遇到類似情境應該注意什麼

2. 從以下 tag 池挑 3-5 個最貼切的:
   chip_silent, chip_active, chip_alert_concentrated,
   tech_strong, tech_weak, tech_high_volatility, tech_overbought, tech_oversold,
   alert_day, normal_day, alert_persistent,
   rate_high, rate_borderline,
   bull_outperformed, bear_outperformed, bull_bear_balanced

只輸出 JSON,不要說明:
{{"reflection": "...", "tags": ["chip_silent", "tech_strong"]}}
"""


def parse_reflection_response(raw: str) -> dict | None:
    """從 LLM 輸出抓 reflection JSON。失敗回 None。"""
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "reflection" not in obj or "tags" not in obj:
        return None
    if not isinstance(obj["tags"], list):
        return None
    return obj


def reflect_one(
    outcome: dict, report_sections: dict[str, str],
    *, llm_call, ticker: str | None = None,
) -> dict:
    """對單一 outcome 跑反思,回 lesson dict(可能含 reflect_failed marker)。"""
    lesson_id = f"{outcome['date']}_{outcome['symbol']}"
    now = datetime.now().isoformat(timespec="seconds")
    base = {
        "id": lesson_id,
        "date": outcome["date"],
        "symbol": outcome["symbol"],
        "ticker": ticker or f"{outcome['symbol']}.TW",
        "outcome": {
            "verdict": outcome["verdict"],
            "trader_action": outcome["trader_action"],
            "trader_conviction": outcome["trader_conviction"],
            "actual_excess_t10": outcome["actual_excess_t10"],
        },
        "reflected_at": now,
    }

    prompt = build_reflection_prompt(outcome, report_sections)
    try:
        raw = llm_call(prompt)
    except Exception as e:
        return {**base, "reflect_failed": True,
                "reason": f"{type(e).__name__}: {e}"}

    if not raw or raw.startswith("[LLM timeout]") or raw.startswith("[LLM error"):
        return {**base, "reflect_failed": True,
                "reason": f"llm_marker: {(raw or '')[:200]}"}

    parsed = parse_reflection_response(raw)
    if parsed is None:
        return {**base, "reflect_failed": True,
                "reason": "non_json_output",
                "raw_excerpt": raw[:300]}

    return {**base,
            "reflection": parsed["reflection"][:1000],
            "tags": parsed["tags"][:10]}


def run_reflections(
    *, store: LessonStore, llm_call,
    outcomes_dir: Path = DATA / "ta_outcomes",
    reports_dir: Path = DATA / "ta_reports",
    verbose: bool = True,
) -> dict:
    """掃 outcomes_dir,對未 reflect 的跑反思寫進 store。"""
    appended = 0
    skipped_existing = 0
    failed = 0

    for date_dir in sorted(outcomes_dir.glob("*")):
        if not date_dir.is_dir():
            continue
        for outcome_path in sorted(date_dir.glob("*.json")):
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
            lesson_id = f"{outcome['date']}_{outcome['symbol']}"

            existing = next(
                (l for l in store.all() if l["id"] == lesson_id),
                None,
            )
            if existing and not existing.get("reflect_failed"):
                skipped_existing += 1
                continue

            report_path = reports_dir / outcome["date"] / f"{outcome['symbol']}.md"
            if not report_path.exists():
                if verbose:
                    print(f"  {lesson_id}: report missing, skip")
                continue
            sections = parse_report_md(report_path)

            lesson = reflect_one(outcome, sections, llm_call=llm_call)
            store.append(lesson)
            if lesson.get("reflect_failed"):
                failed += 1
            else:
                appended += 1

    if verbose:
        print(f"ta_reflect: appended={appended} "
              f"skipped_existing={skipped_existing} failed={failed}")
    return {"appended": appended, "skipped_existing": skipped_existing,
            "failed": failed}


def main() -> int:
    sys.path.insert(0, str(BASE / "agents"))
    from predict import call_llm   # type: ignore[import-not-found]
    store = LessonStore()
    run_reflections(store=store, llm_call=lambda p: call_llm(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
