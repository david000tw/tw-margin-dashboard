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
  python pipeline.py rebuild                     更新 dashboard_all.html header
  python pipeline.py check                       驗證資料完整性
  python pipeline.py status                      顯示目前資料概況
  python pipeline.py dates                       列出所有已有日期

rate_alert 已移除,dashboard 直接由 `rate >= 170` 推導。
需求:Python 3.8+。
"""

import sys
import json
import re
import argparse
import tempfile
import os
import time
from datetime import datetime
from pathlib import Path

# Windows 主控台(cp950)無法顯示 emoji,強制改 utf-8
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


class PipelineError(RuntimeError):
    """所有指令失敗都 raise 這個;__main__ 會 catch 並 exit 1。"""


def year_file(year: str) -> Path:
    return DATA / f"stock_data_{year}.json"


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_write_text(path: Path, content: str) -> None:
    """寫入暫存檔再 rename,降低中斷時破壞既有檔的風險。

    Windows 上防毒即時掃描(Defender 等)會在新檔建立後立刻開啟讀取,
    導致 os.replace 與 tmp.unlink 短暫 WinError 32。遇到就稍後重試。
    """
    path = Path(path)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(content, encoding="utf-8")
        last_err = None
        for delay in (0, 0.2, 0.5, 1.0, 2.0):
            if delay:
                time.sleep(delay)
            try:
                os.replace(tmp, path)
                return
            except PermissionError as e:
                last_err = e
        raise last_err
    except Exception:
        for delay in (0, 0.2, 0.5, 1.0, 2.0):
            if delay:
                time.sleep(delay)
            try:
                tmp.unlink()
                break
            except FileNotFoundError:
                break
            except PermissionError:
                continue
        raise


def save_json(path: Path, obj, indent: int = 2):
    _atomic_write_text(
        Path(path),
        json.dumps(obj, ensure_ascii=False, indent=indent),
    )


# ── Schema 驗證 ──────────────────────────────────────────────

def validate_record(r: dict) -> None:
    """不符規格即 raise ValueError。"""
    missing = [k for k in REQUIRED_FIELDS if k not in r]
    if missing:
        raise ValueError(f"缺少欄位: {missing}")
    if not DATE_RE.match(r["date"]):
        raise ValueError(f"日期格式錯誤 (需 YYYY-MM-DD): {r['date']!r}")
    try:
        datetime.strptime(r["date"], "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"日期不是合法日期 (月份/日數越界): {r['date']!r}")
    if not isinstance(r["rate"], int) or not (100 <= r["rate"] <= 250):
        raise ValueError(f"rate 需為 100–250 的整數: {r['rate']!r}")
    for k in ("bull", "bear", "top5_margin_reduce_inst_buy"):
        if not isinstance(r[k], str):
            raise ValueError(f"{k} 需為字串: {r[k]!r}")
    if "rate_alert" in r:
        raise ValueError(
            "rate_alert 欄位已移除(由 dashboard 依 rate>=170 推導);請從 record 拿掉此欄位。"
        )


# ── 新增一筆資料 ─────────────────────────────────────────────

def append_record(record: dict):
    """先在記憶體組好兩個檔的最終內容,再依序 atomic write。

    若第一個 save_json 已成功、第二個失敗(磁碟問題),下次跑 `pipeline.py check`
    會偵測到 year/merged 不同步並明確報錯。
    """
    validate_record(record)
    d    = record["date"]
    year = d[:4]

    # Step 1:準備 year 檔的新內容
    yfile = year_file(year)
    if yfile.exists():
        ydata = load_json(yfile)
    else:
        ydata = {"year": int(year), "trading_days": 0, "data": []}
    year_existing = {r["date"] for r in ydata["data"]}

    # Step 2:準備 merged 的新內容
    merged = load_json(MERGED) if MERGED.exists() else []
    merged_existing = {r["date"] for r in merged}

    already_year   = d in year_existing
    already_merged = d in merged_existing

    if already_year and already_merged:
        print(f"[skip] {d} 已存在於兩個檔案")
        return

    if not already_year:
        ydata["data"].append(record)
        ydata["data"].sort(key=lambda x: x["date"])
        ydata["trading_days"] = len(ydata["data"])

    if not already_merged:
        merged.append(record)
        merged.sort(key=lambda x: x["date"])

    # Step 3:兩個都寫入。先寫 merged(dashboard 讀這份),
    # 再寫 year(純備份);若 year 寫入失敗,下次 check 會明確報不同步,
    # 不會讓 dashboard 顯示異常。
    if not already_merged:
        save_json(MERGED, merged)
        print(f"[ok] 寫入 all_data_merged.json (共 {len(merged)} 筆)")
    if not already_year:
        save_json(yfile, ydata)
        print(f"[ok] 寫入 {yfile.name} (共 {ydata['trading_days']} 筆)")

    print("\n完成。建議接著跑 'python pipeline.py check' 驗證後再 rebuild。")


# ── 重建 Dashboard ───────────────────────────────────────────

def rebuild_dashboard():
    """更新 dashboard_all.html header 的日期範圍與筆數。

    資料由 fetch('./data/*.json') 動態載入,rebuild 只同步 header fallback。
    """
    if not DASH.exists():
        raise PipelineError("找不到 dashboard_all.html")

    merged = load_json(MERGED)
    if not merged:
        raise PipelineError("all_data_merged.json 為空,無法 rebuild header")

    dates = sorted(r["date"] for r in merged)
    content = DASH.read_text(encoding="utf-8")

    sub_re = re.compile(r'(<div class="sub">)[^<]*(</div>)')
    if not sub_re.search(content):
        raise PipelineError(
            'dashboard_all.html 找不到 header 標記 <div class="sub">...</div>'
        )
    new_sub = f"{dates[0]} ～ {dates[-1]} &nbsp;·&nbsp; 共 {len(merged):,} 個交易日"
    content = sub_re.sub(rf'\g<1>{new_sub}\g<2>', content, count=1)

    _atomic_write_text(DASH, content)
    print(f"[ok] Dashboard header 已更新 ({len(merged)} 筆, {dates[0]} ~ {dates[-1]})")


# ── 檢查 ────────────────────────────────────────────────────

def check():
    """驗證資料完整性;失敗時 raise PipelineError。"""
    errors = []
    warnings = []

    if not MERGED.exists():
        raise PipelineError(f"找不到 {MERGED}")
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
            errors.append(f"[{yfile.name}] 結構異常(缺 data 欄位)")
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

    # 4. TWII 缺漏(warning)
    if TWII.exists():
        twii = load_json(TWII)
        missing_twii = [d for d in dates if d not in twii]
    else:
        warnings.append("[twii] twii_all.json 不存在")
        missing_twii = []
    if missing_twii:
        warnings.append(f"[twii] 缺漏 {len(missing_twii)} 天: {missing_twii[:10]}{' ...' if len(missing_twii)>10 else ''}")

    # 5. rate_alert 殘留(舊欄位防呆)
    stale = [r["date"] for r in merged if "rate_alert" in r]
    if stale:
        errors.append(f"[legacy] 仍有 rate_alert 欄位殘留: {stale[:5]}")

    # 6. manual_review 清單提醒
    review_file = DATA / "manual_review.txt"
    pending_review = []
    if review_file.exists():
        pending_review = [
            line.strip() for line in review_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if pending_review:
            warnings.append(f"[review] 待人工處理 {len(pending_review)} 筆: {pending_review[:5]}")

    alerts = sum(1 for r in merged if r["rate"] >= RATE_ALERT_THRESHOLD)
    print(f"Check 結果")
    print(f"  筆數       : {len(merged)}")
    print(f"  警戒日     : {alerts} (rate >= {RATE_ALERT_THRESHOLD}%)")
    print(f"  TWII 缺漏  : {len(missing_twii)} 天")
    print(f"  人工待處理 : {len(pending_review)} 筆")

    if warnings:
        print("\n[WARN]")
        for w in warnings:
            print(f"  - {w}")
    if errors:
        print("\n[ERROR]")
        for e in errors:
            print(f"  - {e}")
        raise PipelineError(f"{len(errors)} 項錯誤")
    print("\n檢查通過")


# ── 查詢工具 ────────────────────────────────────────────────

def list_dates():
    if not MERGED.exists():
        print("尚無 merged 檔")
        return
    merged = load_json(MERGED)
    dates  = sorted(r["date"] for r in merged)
    if not dates:
        print("尚無資料")
        return
    print(f"共 {len(dates)} 個交易日, 範圍: {dates[0]} ~ {dates[-1]}")


def show_status():
    if not MERGED.exists():
        print("尚無 merged 檔")
        return
    merged = load_json(MERGED)
    twii = load_json(TWII) if TWII.exists() else {}
    if not merged:
        print("資料概況")
        print("  法人資料  : 0 筆 (尚無資料)")
        print(f"  TWII 資料 : {len(twii)} 筆")
        return
    dates = sorted(r["date"] for r in merged)
    last  = merged[-1]
    missing_twii = [d for d in dates if d not in twii]
    alerts = [r for r in merged if r["rate"] >= RATE_ALERT_THRESHOLD]
    print("資料概況")
    print(f"  法人資料  : {len(merged)} 筆  ({dates[0]} ~ {dates[-1]})")
    print(f"  TWII 資料 : {len(twii)} 筆")
    print(f"  TWII 缺漏 : {len(missing_twii)} 天")
    print(f"  警戒日    : {len(alerts)} 天 (融資率 >={RATE_ALERT_THRESHOLD}%)")
    print(f"  最新一筆  : {last['date']} - 融資率 {last['rate']}%")


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


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "status"
    args = argv[1:]

    if cmd == "append":
        if not args:
            print(__doc__); return 1
        record = _parse_append(args)
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
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PipelineError as e:
        print(f"\n[FAIL] {e}", file=sys.stderr)
        sys.exit(1)
