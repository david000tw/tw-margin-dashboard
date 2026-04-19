"""
台股借券融資 每日資料更新 Pipeline
====================================

用途：每日新增一筆 Scantrader 分析結果，同步更新所有資料檔與 Dashboard。

使用方式：
  python pipeline.py append <json_file>          從 JSON 檔讀取一筆 record
  python pipeline.py append --date 2026-04-18 \\
                            --rate 172 \\
                            --bull "台積電,聯發科" \\
                            --bear "長榮,陽明" \\
                            --top5 "台積電,富邦金,玉山金,中信金,國泰金"
  python pipeline.py rebuild                     更新 dashboard_all.html header（資料已改 fetch）
  python pipeline.py check                       驗證資料完整性
  python pipeline.py status                      顯示目前資料概況
  python pipeline.py dates                       列出所有已有日期

資料格式（json_file 內容或 CLI 參數）：
  {
    "date": "2026-04-18",
    "bull": "台積電,聯發科,鴻海",
    "bear": "長榮,陽明",
    "rate": 172,
    "top5_margin_reduce_inst_buy": "台積電,富邦金,玉山金,中信金,國泰金"
  }

  rate_alert 已移除，dashboard 直接由 `rate >= 170` 推導。
"""

import sys
import json
import re
import argparse
from pathlib import Path

# Windows 主控台（cp950）無法顯示 emoji，強制改 utf-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE   = Path(__file__).parent
DATA   = BASE / "data"
MERGED = DATA / "all_data_merged.json"
TWII   = DATA / "twii_all.json"
DASH   = BASE / "dashboard_all.html"

RATE_ALERT_THRESHOLD = 170
REQUIRED_FIELDS = ("date", "bull", "bear", "rate", "top5_margin_reduce_inst_buy")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def year_file(year: str) -> Path:
    return DATA / f"stock_data_{year}.json"


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path: Path, obj, indent: int = 2):
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=indent),
        encoding="utf-8",
    )


# ── Schema 驗證 ──────────────────────────────────────────────

def validate_record(r: dict) -> None:
    """不符規格即 raise ValueError。"""
    missing = [k for k in REQUIRED_FIELDS if k not in r]
    if missing:
        raise ValueError(f"缺少欄位: {missing}")
    if not DATE_RE.match(r["date"]):
        raise ValueError(f"日期格式錯誤 (需 YYYY-MM-DD): {r['date']!r}")
    if not isinstance(r["rate"], int) or not (100 <= r["rate"] <= 250):
        raise ValueError(f"rate 需為 100–250 的整數: {r['rate']!r}")
    for k in ("bull", "bear", "top5_margin_reduce_inst_buy"):
        if not isinstance(r[k], str):
            raise ValueError(f"{k} 需為字串: {r[k]!r}")
    if "rate_alert" in r:
        raise ValueError(
            "rate_alert 欄位已移除（由 dashboard 依 rate>=170 推導）；請從 record 拿掉此欄位。"
        )


# ── 新增一筆資料 ─────────────────────────────────────────────

def append_record(record: dict):
    validate_record(record)
    d    = record["date"]
    year = d[:4]

    # 1. 寫入對應年份檔
    yfile = year_file(year)
    if not yfile.exists():
        ydata = {"year": int(year), "trading_days": 0, "data": []}
    else:
        ydata = load_json(yfile)

    if d in {r["date"] for r in ydata["data"]}:
        print(f"⚠️  {d} 已存在於 {yfile.name},略過")
    else:
        ydata["data"].append(record)
        ydata["data"].sort(key=lambda x: x["date"])
        ydata["trading_days"] = len(ydata["data"])
        save_json(yfile, ydata)
        print(f"✅  寫入 {yfile.name}（共 {ydata['trading_days']} 筆）")

    # 2. 寫入 all_data_merged.json
    merged = load_json(MERGED)
    if d in {r["date"] for r in merged}:
        print(f"⚠️  {d} 已存在於 all_data_merged.json,略過")
    else:
        merged.append(record)
        merged.sort(key=lambda x: x["date"])
        save_json(MERGED, merged)
        print(f"✅  寫入 all_data_merged.json（共 {len(merged)} 筆）")

    print("\n📊  完成!執行 'python pipeline.py rebuild' 更新 Dashboard header")


# ── 重建 Dashboard ───────────────────────────────────────────

def rebuild_dashboard():
    """更新 dashboard_all.html header 的日期範圍與筆數。

    資料已改由 fetch('./data/*.json') 動態載入,rebuild 只負責同步 header。
    """
    if not DASH.exists():
        print("❌  找不到 dashboard_all.html")
        sys.exit(1)

    merged = load_json(MERGED)
    dates  = sorted(r["date"] for r in merged)
    content = DASH.read_text(encoding="utf-8")

    # Header 是 <div class="sub">...</div>,初始顯示「載入中…」,
    # JS fetch 成功後會被 updateHeader() 覆蓋為最新日期範圍。
    # rebuild 把這裡的 fallback 文字同步為 build 時的資料範圍,
    # 讓 JS 還沒載入完、或 fetch 失敗時也能看到合理文字。
    sub_re = re.compile(r'(<div class="sub">)[^<]*(</div>)')
    if not sub_re.search(content):
        raise RuntimeError(
            'dashboard_all.html 找不到 header 標記 <div class="sub">...</div>'
        )
    new_sub = f"{dates[0]} ～ {dates[-1]} &nbsp;·&nbsp; 共 {len(merged):,} 個交易日"
    content = sub_re.sub(rf'\g<1>{new_sub}\g<2>', content, count=1)

    DASH.write_text(content, encoding="utf-8")
    print(f"✅  Dashboard header fallback 已更新（{len(merged)} 筆,{dates[0]} ～ {dates[-1]}）")


# ── 檢查 ────────────────────────────────────────────────────

def check():
    """驗證資料完整性;失敗時以 exit code 1 結束。"""
    errors = []
    warnings = []

    merged = load_json(MERGED)

    # 1. schema
    for r in merged:
        try:
            validate_record(r)
        except ValueError as e:
            errors.append(f"[schema] {r.get('date','?')}: {e}")

    # 2. 日期唯一
    dates = [r["date"] for r in merged]
    if len(dates) != len(set(dates)):
        dup = {d for d in dates if dates.count(d) > 1}
        errors.append(f"[merged] 重複日期: {sorted(dup)}")

    # 3. year 檔筆數總和 == merged
    year_total = 0
    year_dates = set()
    for yfile in sorted(DATA.glob("stock_data_*.json")):
        y = load_json(yfile)
        if not (isinstance(y, dict) and "data" in y):
            errors.append(f"[{yfile.name}] 結構異常（缺 data 欄位）")
            continue
        year_total += len(y["data"])
        year_dates.update(r["date"] for r in y["data"])
    if year_total != len(merged):
        errors.append(f"[sync] year 檔總和 {year_total} != merged {len(merged)}")
    missing_in_year = set(dates) - year_dates
    extra_in_year   = year_dates - set(dates)
    if missing_in_year:
        errors.append(f"[sync] merged 有但 year 檔缺: {sorted(missing_in_year)[:10]}")
    if extra_in_year:
        errors.append(f"[sync] year 檔有但 merged 缺: {sorted(extra_in_year)[:10]}")

    # 4. TWII 缺漏（warning）
    twii = load_json(TWII)
    missing_twii = [d for d in dates if d not in twii]
    if missing_twii:
        warnings.append(f"[twii] 缺漏 {len(missing_twii)} 天: {missing_twii[:10]}{' …' if len(missing_twii)>10 else ''}")

    # 5. rate_alert 殘留（舊欄位防呆）
    stale = [r["date"] for r in merged if "rate_alert" in r]
    if stale:
        errors.append(f"[legacy] 仍有 rate_alert 欄位殘留: {stale[:5]}")

    alerts = sum(1 for r in merged if r["rate"] >= RATE_ALERT_THRESHOLD)
    print(f"📊  Check 結果")
    print(f"  筆數       : {len(merged)}")
    print(f"  警戒日     : {alerts} (rate ≥ {RATE_ALERT_THRESHOLD}%)")
    print(f"  TWII 缺漏  : {len(missing_twii)} 天")

    if warnings:
        print("\n⚠️  警告:")
        for w in warnings:
            print(f"  - {w}")
    if errors:
        print("\n❌  錯誤:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("\n✅  檢查通過")


# ── 查詢工具 ────────────────────────────────────────────────

def list_dates():
    merged = load_json(MERGED)
    dates  = sorted(r["date"] for r in merged)
    print(f"共 {len(dates)} 個交易日,範圍：{dates[0]} ～ {dates[-1]}")


def show_status():
    merged = load_json(MERGED)
    twii   = load_json(TWII)
    dates  = sorted(r["date"] for r in merged)
    last   = merged[-1] if merged else {}
    missing_twii = [d for d in dates if d not in twii]
    alerts = [r for r in merged if r["rate"] >= RATE_ALERT_THRESHOLD]
    print("📊  資料概況")
    print(f"  法人資料  ：{len(merged)} 筆  ({dates[0]} ～ {dates[-1]})")
    print(f"  TWII 資料 ：{len(twii)} 筆")
    print(f"  TWII 缺漏 ：{len(missing_twii)} 天")
    print(f"  警戒日    ：{len(alerts)} 天 (融資率 ≥{RATE_ALERT_THRESHOLD}%)")
    print(f"  最新一筆  ：{last.get('date')} — 融資率 {last.get('rate')}%")


# ── CLI ──────────────────────────────────────────────────────

def _parse_append(argv):
    """支援兩種形式:
       append path/to/record.json
       append --date ... --rate ... --bull ... --bear ... --top5 ...
    """
    if len(argv) == 1 and not argv[0].startswith("--"):
        return load_json(Path(argv[0]))
    p = argparse.ArgumentParser(prog="pipeline.py append")
    p.add_argument("--date", required=True)
    p.add_argument("--rate", required=True, type=int)
    p.add_argument("--bull", required=True)
    p.add_argument("--bear", required=True)
    p.add_argument("--top5", required=True, dest="top5")
    ns = p.parse_args(argv)
    return {
        "date": ns.date,
        "bull": ns.bull,
        "bear": ns.bear,
        "rate": ns.rate,
        "top5_margin_reduce_inst_buy": ns.top5,
    }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "append":
        if len(sys.argv) < 3:
            print(__doc__); sys.exit(1)
        record = _parse_append(sys.argv[2:])
        append_record(record)
    elif cmd == "rebuild":
        rebuild_dashboard()
    elif cmd == "check":
        check()
    elif cmd == "status":
        show_status()
    elif cmd == "dates":
        list_dates()
    else:
        print(__doc__)
