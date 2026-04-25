"""
CrewAI agent 用的分析工具。
把重計算的統計在 Python 先算好，再把「摘要」餵給 LLM，避免塞太多 token。
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from crewai.tools import tool

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MERGED = DATA_DIR / "all_data_merged.json"
TWII = DATA_DIR / "twii_all.json"
ALERT_THRESHOLD = 170


def _load_merged() -> list[dict]:
    with MERGED.open(encoding="utf-8") as f:
        return json.load(f)


def _load_twii() -> dict[str, float]:
    with TWII.open(encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return {r["date"]: float(r.get("close", 0)) for r in raw}
    return {k: float(v) for k, v in raw.items()}


def _filter_recent(records: list[dict], days: int) -> list[dict]:
    today = datetime.today().date()
    cutoff = today - timedelta(days=days)
    out = []
    for r in records:
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if d >= cutoff:
            out.append(r)
    return sorted(out, key=lambda r: r["date"])


def _split_names(s: str) -> list[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


@tool("analyze_rate_alerts")
def analyze_rate_alerts(days: int = 30) -> str:
    """統計近 N 天融資率警戒（rate>=170）狀況。回傳摘要文字。"""
    recs = _filter_recent(_load_merged(), days)
    if not recs:
        return f"近{days}天無資料"
    total = len(recs)
    alerts = [r for r in recs if r.get("rate", 0) >= ALERT_THRESHOLD]
    avg = sum(r.get("rate", 0) for r in recs) / total
    max_r = max(recs, key=lambda r: r.get("rate", 0))
    lines = [
        f"近{days}天共{total}筆資料",
        f"平均融資率：{avg:.1f}",
        f"最高融資率：{max_r['rate']}（{max_r['date']}）",
        f"警戒天數（rate>=170）：{len(alerts)} 天",
    ]
    if alerts:
        lines.append("警戒日期：" + ", ".join(f"{a['date']}({a['rate']})" for a in alerts[:15]))
    return "\n".join(lines)


@tool("top_stocks_flow")
def top_stocks_flow(days: int = 30, side: str = "bull", top_n: int = 10) -> str:
    """找出近 N 天被法人加碼(bull)或減碼(bear)最頻繁的股票前 N 名。"""
    if side not in ("bull", "bear"):
        return "side 參數必須是 'bull' 或 'bear'"
    recs = _filter_recent(_load_merged(), days)
    counter: Counter[str] = Counter()
    for r in recs:
        for name in _split_names(r.get(side, "")):
            counter[name] += 1
    if not counter:
        return f"近{days}天無 {side} 資料"
    top = counter.most_common(top_n)
    label = "加碼" if side == "bull" else "減碼"
    lines = [f"近{days}天被法人{label}最多次的股票（共統計 {len(recs)} 個交易日）："]
    for i, (name, cnt) in enumerate(top, 1):
        lines.append(f"  {i}. {name}：{cnt} 次")
    return "\n".join(lines)


@tool("top_margin_reduce_targets")
def top_margin_reduce_targets(days: int = 30, top_n: int = 10) -> str:
    """找出 top5_margin_reduce_inst_buy（融資減少＋法人買超）最常入榜的個股。"""
    recs = _filter_recent(_load_merged(), days)
    counter: Counter[str] = Counter()
    for r in recs:
        for name in _split_names(r.get("top5_margin_reduce_inst_buy", "")):
            counter[name] += 1
    if not counter:
        return f"近{days}天無相關資料"
    lines = [f"近{days}天「融資減少且法人買超」最常入 top5 的個股："]
    for i, (name, cnt) in enumerate(counter.most_common(top_n), 1):
        lines.append(f"  {i}. {name}：{cnt} 次")
    return "\n".join(lines)


@tool("twii_vs_alerts")
def twii_vs_alerts(days: int = 60) -> str:
    """比對融資警戒日之後 N 日大盤（TWII）表現。回傳各警戒日後 1/5/10 日漲跌摘要。"""
    recs = _filter_recent(_load_merged(), days)
    twii = _load_twii()
    dates_sorted = sorted(twii.keys())
    alerts = [r for r in recs if r.get("rate", 0) >= ALERT_THRESHOLD]
    if not alerts:
        return f"近{days}天無警戒日"

    def close_after(base_date: str, n: int) -> float | None:
        try:
            idx = dates_sorted.index(base_date)
        except ValueError:
            return None
        target = idx + n
        if target >= len(dates_sorted):
            return None
        return twii[dates_sorted[target]]

    lines = [f"近{days}天共 {len(alerts)} 個警戒日，觀察其後 TWII 表現："]
    for a in alerts[-10:]:
        d = a["date"]
        base = twii.get(d)
        if base is None:
            continue
        row = [f"  {d} (rate={a['rate']}, TWII={base:.0f})"]
        for n in (1, 5, 10):
            future = close_after(d, n)
            if future is not None:
                pct = (future - base) / base * 100
                row.append(f"+{n}日:{pct:+.2f}%")
            else:
                row.append(f"+{n}日:N/A")
        lines.append(" ".join(row))
    return "\n".join(lines)


@tool("latest_snapshot")
def latest_snapshot() -> str:
    """取得最新一筆資料（當日法人加減碼與融資率）。"""
    recs = _load_merged()
    if not recs:
        return "無資料"
    latest = sorted(recs, key=lambda r: r["date"])[-1]
    return (
        f"最新資料日：{latest['date']}\n"
        f"融資率：{latest.get('rate', 'N/A')}\n"
        f"法人加碼（bull）：{latest.get('bull', '-')}\n"
        f"法人減碼（bear）：{latest.get('bear', '-')}\n"
        f"top5 融資減法人買：{latest.get('top5_margin_reduce_inst_buy', '-')}"
    )
