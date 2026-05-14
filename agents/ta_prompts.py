"""
TradingAgents-lite 的 6 個 agent prompt template。

每個 build_*_prompt(features, prior_outputs={}) 回傳單一字串,
直接餵給 agents/predict.py:call_llm()。

設計原則:
  - 完整 self-contained:不依賴 LLM 記憶,每個 prompt 從零講清楚
  - 嚴格輸出格式:每個 agent 期望輸出 ~150-200 字 plain text(不要 JSON,
    這層的目的是給人讀)
  - prior_outputs 機制:Stage 2/3/4 的 agent 可看前面 stage 的輸出,
    模擬辯論與綜合

借用 TradingAgents 原版的角色定位,改寫為繁中 + 塞入台股 chip 資料。
"""
from __future__ import annotations

from ta_features import SymbolFeatures  # type: ignore[import-not-found]


def _format_chip(chip: dict) -> str:
    lines = [
        f"- bull 榜出現 {chip['bull_count']} 次(近 60 日)",
        f"- bear 榜出現 {chip['bear_count']} 次",
        f"- 融資減+法人買(top5)出現 {chip['top5_count']} 次",
    ]
    if chip["last_top5_date"]:
        lines.append(
            f"- 最近一次 top5: {chip['last_top5_date']} (當日 rate={chip['last_top5_rate']})"
        )
    if chip["bull_avg_rate"] is not None:
        lines.append(
            f"- 出現於 bull 當日平均 rate = {chip['bull_avg_rate']:.1f}"
            f"({'警戒環境' if chip['bull_avg_rate'] >= 170 else '常態環境'}為主)"
        )
    return "\n".join(lines)


def _format_price(price: dict | None) -> str:
    if price is None:
        return "- 無價格資料(可能是新上市或下市)"
    twii_ret = price["twii_return_window"]
    excess_ret = price["excess_return_window"]
    if twii_ret is None or excess_ret is None:
        rel_line = (
            f"- 累積報酬 {price['return_window']*100:+.2f}% "
            "(TWII anchor 缺,無相對表現可算)"
        )
    else:
        rel_line = (
            f"- 累積報酬 {price['return_window']*100:+.2f}% "
            f"vs TWII {twii_ret*100:+.2f}% "
            f"→ 相對表現 {excess_ret*100:+.2f}%"
        )
    return (
        f"- 回看窗: {price['window_start']} ~ {price['window_end']}\n"
        f"- 收盤序列(後 5 筆): {price['closes'][-5:]}\n"
        f"- MA5={price['ma5']:.2f}  MA20={price['ma20']:.2f}\n"
        f"{rel_line}"
    )


def _format_past_perf(pp: dict) -> str:
    total = pp["long_count"] + pp["short_count"]
    if total == 0:
        return "- 過去無 AI 推薦紀錄"
    lines = []
    if pp["long_count"] > 0:
        wr = pp["long_win_count"] / pp["long_count"] * 100
        lines.append(f"- 過去被推薦 long {pp['long_count']} 次,勝 {pp['long_win_count']}({wr:.0f}%)")
    if pp["short_count"] > 0:
        wr = pp["short_win_count"] / pp["short_count"] * 100
        lines.append(f"- 過去被推薦 short {pp['short_count']} 次,勝 {pp['short_win_count']}({wr:.0f}%)")
    return "\n".join(lines)


def _format_market(mc: dict) -> str:
    twii = mc.get("twii")
    if not twii:
        return "- 無 TWII 資料"
    return (
        f"- TWII: {twii['first_date']} ({twii['first_value']:.0f}) "
        f"→ {twii['last_date']} ({twii['last_value']:.0f}) "
        f"({twii['return_pct']:+.2f}%)\n"
        f"- 近 {len(mc['recent_records'])} 天 record 樣本"
    )


def _header(f: SymbolFeatures, role: str) -> str:
    return (
        f"[SYSTEM]\n你是台股分析師團隊中的「{role}」。"
        "輸出必須是繁體中文純文字(不要 JSON、不要 markdown 標題)。\n\n"
        f"[USER]\n=== 分析標的 ===\n"
        f"代號: {f.symbol}  ticker: {f.ticker}  分析日: {f.target_date}\n\n"
        f"=== 籌碼面 ===\n{_format_chip(f.chip)}\n\n"
        f"=== 技術面 ===\n{_format_price(f.price)}\n\n"
        f"=== 過去 AI 推薦紀錄 ===\n{_format_past_perf(f.past_perf)}\n\n"
        f"=== 大盤近況 ===\n{_format_market(f.market_context)}\n"
    )


def build_market_analyst_prompt(f: SymbolFeatures) -> str:
    return _header(f, "技術分析師") + (
        "\n=== 你的任務 ===\n"
        "純從技術面解讀:價格相對 TWII 的強弱、均線排列、近期動能。\n"
        "輸出 ~150 字短評,結尾一句總結偏多 / 偏空 / 中性。\n"
    )


def build_chip_analyst_prompt(f: SymbolFeatures) -> str:
    return _header(f, "籌碼分析師") + (
        "\n=== 你的任務 ===\n"
        "純從籌碼面解讀:bull/bear/top5 榜單出現的頻率與環境(警戒 vs 常態)、"
        "融資減+法人買的訊號意義。\n"
        "輸出 ~150 字短評,結尾一句總結籌碼是否站在多方或空方。\n"
    )


def build_bull_researcher_prompt(f: SymbolFeatures, prior: dict) -> str:
    return _header(f, "多方研究員") + (
        f"\n=== Stage 1 分析師報告 ===\n"
        f"[技術面報告]\n{prior.get('market', '(無)')}\n\n"
        f"[籌碼面報告]\n{prior.get('chip', '(無)')}\n\n"
        "=== 你的任務 ===\n"
        "你必須站在多方立場。即使資料偏空,也要挖出可能上漲的理由 ── "
        "但理由要紮根於上述資料,不能空話。\n"
        "重點:(a) 為何技術 / 籌碼支持上漲 (b) 短期催化是什麼 (c) 進場時機觀點\n"
        "輸出 ~200 字。\n"
    )


def build_bear_researcher_prompt(f: SymbolFeatures, prior: dict) -> str:
    return _header(f, "空方研究員") + (
        f"\n=== Stage 1 分析師報告 ===\n"
        f"[技術面報告]\n{prior.get('market', '(無)')}\n\n"
        f"[籌碼面報告]\n{prior.get('chip', '(無)')}\n\n"
        "=== 你的任務 ===\n"
        "你必須站在空方立場。即使資料偏多,也要找出潛在風險 ── "
        "但要紮根上述資料。\n"
        "重點:(a) 技術 / 籌碼上的警訊 (b) 可能下跌的觸發 (c) 反向風險評估\n"
        "輸出 ~200 字。\n"
    )


def build_trader_prompt(f: SymbolFeatures, prior: dict) -> str:
    return _header(f, "交易員") + (
        f"\n=== 前面 4 份報告 ===\n"
        f"[技術面報告]\n{prior.get('market', '(無)')}\n\n"
        f"[籌碼面報告]\n{prior.get('chip', '(無)')}\n\n"
        f"[多方論點]\n{prior.get('bull', '(無)')}\n\n"
        f"[空方論點]\n{prior.get('bear', '(無)')}\n\n"
        "=== 你的任務 ===\n"
        "綜合上述 4 份報告,做出交易決策。\n"
        "輸出格式(嚴格遵守,每行一個欄位):\n"
        "ACTION: buy | sell | hold\n"
        "CONVICTION: 0.0 ~ 1.0(對方向的信心)\n"
        "HORIZON: short | medium | long\n"
        "RATIONALE: <~200 字>說明為何選 buy/sell/hold,有沒有偏向多空哪一邊\n"
    )


def build_risk_manager_prompt(f: SymbolFeatures, prior: dict) -> str:
    return _header(f, "風險經理") + (
        f"\n=== 前面 5 份報告 ===\n"
        f"[技術面報告]\n{prior.get('market', '(無)')}\n\n"
        f"[籌碼面報告]\n{prior.get('chip', '(無)')}\n\n"
        f"[多方論點]\n{prior.get('bull', '(無)')}\n\n"
        f"[空方論點]\n{prior.get('bear', '(無)')}\n\n"
        f"[交易決策]\n{prior.get('trader', '(無)')}\n\n"
        "=== 你的任務 ===\n"
        "做最終風險把關。即使前面看好,也要點出風險;即使前面看壞,也要點出反彈可能。\n"
        "輸出格式(嚴格遵守):\n"
        "MAX_POSITION_PCT: 0 ~ 100(建議倉位上限,佔組合 %)\n"
        "MAIN_RISKS: <條列 2-3 點>\n"
        "REBUTTAL: <~100 字>如果不同意上面的交易決策,在這裡反駁;同意就寫「同意,理由 ...」\n"
    )
