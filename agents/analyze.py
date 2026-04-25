"""
台股法人資料多 Agent 分析器。
走 Claude Code 訂閱（透過 langchain-claude-code-cli），免付額外 API 費用。

用法：
    python agents/analyze.py              # 預設分析近30天
    python agents/analyze.py --days 60    # 指定天數
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 讓 agents/ 內的 import 找得到 tools.py
sys.path.insert(0, str(Path(__file__).resolve().parent))

from crewai import Agent, Crew, Process, Task

from claude_code_llm import ClaudeCodeLLM
from tools import (
    analyze_rate_alerts,
    latest_snapshot,
    top_margin_reduce_targets,
    top_stocks_flow,
    twii_vs_alerts,
)

LLM = ClaudeCodeLLM(model="sonnet", timeout=300)


def build_crew(days: int) -> Crew:
    rate_analyst = Agent(
        role="融資率警戒分析師",
        goal=f"找出近{days}天融資率警戒（>=170）的發生頻率、趨勢、及其後大盤表現",
        backstory="你是資深台股風控分析師，擅長解讀融資率變化與市場過熱訊號。",
        tools=[analyze_rate_alerts, twii_vs_alerts],
        llm=LLM,
        verbose=False,
        allow_delegation=False,
    )

    flow_analyst = Agent(
        role="法人動向分析師",
        goal=f"找出近{days}天被法人連續加碼/減碼的熱門與冷門股",
        backstory="你擅長透過三大法人每日進出名單，找出資金明確流向的族群與個股。",
        tools=[top_stocks_flow, top_margin_reduce_targets],
        llm=LLM,
        verbose=False,
        allow_delegation=False,
    )

    snapshot_analyst = Agent(
        role="當日快照分析師",
        goal="解讀最新一日的法人加減碼與融資率",
        backstory="你是盤後即時解讀員，擅長用一兩句話點出當日最值得注意的訊號。",
        tools=[latest_snapshot],
        llm=LLM,
        verbose=False,
        allow_delegation=False,
    )

    chief = Agent(
        role="首席分析師",
        goal="整合三位分析師的報告，產出可執行的行動建議",
        backstory="你是基金操盤手，能從雜訊中萃取關鍵訊號，並給出清楚的結論。",
        llm=LLM,
        verbose=False,
        allow_delegation=False,
    )

    t1 = Task(
        description=(
            f"分析近 {days} 天的融資率警戒情況。使用 analyze_rate_alerts 取得統計，"
            f"再用 twii_vs_alerts 查看警戒日後大盤表現。"
            "產出：警戒頻率、趨勢判斷（過熱/冷卻/正常）、歷史警戒後是否常見回檔。"
        ),
        expected_output="一段約 150 字的繁體中文分析段落。",
        agent=rate_analyst,
    )

    t2 = Task(
        description=(
            f"分析近 {days} 天的法人動向。分別呼叫 top_stocks_flow(side='bull') 與 "
            "top_stocks_flow(side='bear') 找出加碼/減碼熱門股，"
            "再用 top_margin_reduce_targets 找出「融資減且法人買」的強勢股。"
            "產出：3-5 檔值得留意的標的與理由。"
        ),
        expected_output="一份條列式標的清單（繁體中文）。",
        agent=flow_analyst,
    )

    t3 = Task(
        description="呼叫 latest_snapshot 取得最新資料，指出今日三個最值得關注的訊號。",
        expected_output="3 點條列訊號（繁體中文，每點一句話）。",
        agent=snapshot_analyst,
    )

    t4 = Task(
        description=(
            "整合前三位分析師的輸出，寫成一份給基金經理人的每日分析報告。"
            "結構：【一句話結論】、【市場溫度】、【關注標的】、【風險提醒】。"
        ),
        expected_output="結構化繁體中文 Markdown 報告，約 300-400 字。",
        agent=chief,
        context=[t1, t2, t3],
    )

    return Crew(
        agents=[rate_analyst, flow_analyst, snapshot_analyst, chief],
        tasks=[t1, t2, t3, t4],
        process=Process.sequential,
        verbose=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="分析回溯天數")
    args = parser.parse_args()

    crew = build_crew(args.days)
    result = crew.kickoff()

    out_path = Path(__file__).resolve().parent / "latest_report.md"
    out_path.write_text(str(result), encoding="utf-8")
    print("\n" + "=" * 60)
    print(f"報告已寫入：{out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
