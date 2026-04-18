"""
台股借券融資 每日資料更新 Pipeline
====================================
用途：每日新增一筆 Scantrader 分析結果，同步更新所有資料檔與 Dashboard

使用方式：
  python pipeline.py append  <json_file>   ← 新增一天資料（JSON 檔路徑）
  python pipeline.py rebuild               ← 重建 dashboard_all.html
  python pipeline.py status                ← 顯示目前資料概況
  python pipeline.py dates                 ← 列出所有已有日期

資料格式（json_file 內容）：
  {
    "date": "2026-04-15",
    "bull": "台積電,聯發科,鴻海",
    "bear": "長榮,陽明",
    "rate": 163,
    "rate_alert": true,
    "top5_margin_reduce_inst_buy": "台積電,富邦金,玉山金,中信金,國泰金"
  }
"""

import sys, json, re
from pathlib import Path

BASE   = Path(__file__).parent
DATA   = BASE / "data"
MERGED = DATA / "all_data_merged.json"
TWII   = DATA / "twii_all.json"
DASH   = BASE / "dashboard_all.html"


def year_file(year: str) -> Path:
    return DATA / f"stock_data_{year}.json"

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 新增一筆資料 ─────────────────────────────────────────────

def append_record(record: dict):
    d    = record["date"]   # e.g. "2026-04-15"
    year = d[:4]

    # 1. 寫入對應年份檔
    yfile = year_file(year)
    if not yfile.exists():
        ydata = {"year": int(year), "trading_days": 0, "data": []}
    else:
        ydata = load_json(yfile)

    if d in {r["date"] for r in ydata["data"]}:
        print(f"⚠️  {d} 已存在於 {yfile.name}，略過")
    else:
        ydata["data"].append(record)
        ydata["data"].sort(key=lambda x: x["date"])
        ydata["trading_days"] = len(ydata["data"])
        save_json(yfile, ydata)
        print(f"✅  寫入 {yfile.name}（共 {ydata['trading_days']} 筆）")

    # 2. 寫入 all_data_merged.json
    merged = load_json(MERGED)
    if d in {r["date"] for r in merged}:
        print(f"⚠️  {d} 已存在於 all_data_merged.json，略過")
    else:
        merged.append(record)
        merged.sort(key=lambda x: x["date"])
        save_json(MERGED, merged)
        print(f"✅  寫入 all_data_merged.json（共 {len(merged)} 筆）")

    print(f"\n📊  完成！執行 'python pipeline.py rebuild' 更新 Dashboard")


# ── 重建 Dashboard ───────────────────────────────────────────

def rebuild_dashboard():
    """將最新的 all_data_merged.json + twii_all.json 重新嵌入 dashboard_all.html"""
    if not DASH.exists():
        print("❌  找不到 dashboard_all.html")
        return

    merged = load_json(MERGED)
    twii   = load_json(TWII)

    content = DASH.read_text(encoding="utf-8")

    # 替換 RAW 資料
    raw_js = json.dumps(merged, ensure_ascii=False, separators=(',', ':'))
    s = content.find('const RAW = [')
    e = content.find('];\n', s) + 3
    content = content[:s] + f'const RAW = {raw_js};\n' + content[e:]

    # 替換 TWII 資料
    twii_js = json.dumps(twii, ensure_ascii=False, separators=(',', ':'))
    s = content.find('const TWII = {')
    e = content.find(';\n', s) + 2
    content = content[:s] + f'const TWII = {twii_js};\n' + content[e:]

    # 更新 header 日期與總筆數
    dates = sorted(r["date"] for r in merged)
    sub   = f'{dates[0]} ～ {dates[-1]} &nbsp;·&nbsp; 共 {len(merged):,} 個交易日'
    content = re.sub(
        r'\d{4}-\d{2}-\d{2} ～ \d{4}-\d{2}-\d{2} &nbsp;·&nbsp; 共 [\d,]+ 個交易日',
        sub, content
    )

    DASH.write_text(content, encoding="utf-8")
    print(f"✅  Dashboard 已更新（{len(merged)} 筆，{dates[0]} ～ {dates[-1]}）")


# ── 查詢工具 ────────────────────────────────────────────────

def list_dates():
    merged = load_json(MERGED)
    dates  = sorted(r["date"] for r in merged)
    print(f"共 {len(dates)} 個交易日，範圍：{dates[0]} ～ {dates[-1]}")

def show_status():
    merged = load_json(MERGED)
    twii   = load_json(TWII)
    dates  = sorted(r["date"] for r in merged)
    last   = merged[-1] if merged else {}
    missing_twii = [d for d in dates if d not in twii]
    alerts = [r for r in merged if r.get("rate_alert")]
    print("📊  資料概況")
    print(f"  法人資料  ：{len(merged)} 筆  ({dates[0]} ～ {dates[-1]})")
    print(f"  TWII 資料 ：{len(twii)} 筆")
    print(f"  TWII 缺漏 ：{len(missing_twii)} 天")
    print(f"  警戒日    ：{len(alerts)} 天 (融資率 ≥160%)")
    print(f"  最新一筆  ：{last.get('date')} — 融資率 {last.get('rate')}%")


# ── 主程式 ───────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "append" and len(sys.argv) == 3:
        record = load_json(Path(sys.argv[2]))
        append_record(record)
    elif cmd == "rebuild":
        rebuild_dashboard()
    elif cmd == "dates":
        list_dates()
    elif cmd == "status":
        show_status()
    else:
        print(__doc__)
