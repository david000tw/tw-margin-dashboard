"""
股票符號解析:把 record 中混雜的股號/股名統一映射到 (code, name, ticker, display)。

資料來源優先順序:
  1. data/stock_aliases.json   人工 / 自動補表(凌駕官方對照,給興櫃/已下市/縮寫差異用)
  2. data/stock_map.json       TWSE/TPEx OpenAPI 的官方簡稱 → code
  3. data/stock_fetch_log.json 之前 fetch_prices.py 產生的 symbol_to_ticker

Output: data/symbol_index.json
{
  "by_symbol": {
    "2330":   {"code":"2330", "name":"台積電", "ticker":"2330.TW", "display":"2330 台積電"},
    "台積電":  {"code":"2330", "name":"台積電", "ticker":"2330.TW", "display":"2330 台積電"},
    "5080":   {"code":"5080", "name": null,    "ticker":"5080.TW", "display":"5080"},
    "新巨群":  {"code": null,  "name":"新巨群",  "ticker": null,    "display":"新巨群 (待補)"}
  },
  "stats": {"total": ..., "resolved": ..., "only_code": ..., "only_name": ...}
}

供 fetch_prices.py 與 analyze_signals.py 與 dashboard 共用。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE       = Path(__file__).resolve().parent.parent
DATA       = BASE / "data"
MERGED     = DATA / "all_data_merged.json"
STOCK_MAP  = DATA / "stock_map.json"
ALIASES    = DATA / "stock_aliases.json"
FETCH_LOG  = DATA / "stock_fetch_log.json"
INDEX_FILE = DATA / "symbol_index.json"

# record schema 的三個欄位 → 內部 side 簡稱
SIDE_FIELDS = {
    "bull": "bull",
    "bear": "bear",
    "top5": "top5_margin_reduce_inst_buy",
}

# 三組訊號的方向。bull/top5 期望正 alpha,bear 期望負 alpha;
# 篩選邏輯與 dashboard 排序都靠 sign 驅動,而非 if side=="bear" 的字串特例。
SIDE_CONFIG = {
    "bull": {"sign": +1, "label": "Bull (法人買)"},
    "bear": {"sign": -1, "label": "Bear (借券加)"},
    "top5": {"sign": +1, "label": "Top5 (融資減+法人買)"},
}

# 主要分析 horizon。grid search、symbols_top20、dashboard cards 都以這個為準。
PRIMARY_HORIZON = 20

CODE_RE = re.compile(r"^(\d{4,6})([A-Z]*)$")


def normalize_symbol(s: str) -> str:
    """
    Canonical 規範化:給 alias / fuzzy / fetch 等所有層級共用。
    去掉警示符號 *、KY/DR 後綴、全形/半形空白。
    """
    if not s:
        return ""
    return (
        s.strip()
         .replace("*", "")
         .replace("-KY", "")
         .replace("-DR", "")
         .replace(" ", "")
         .replace("　", "")
    )


def _load_pipeline_io():
    """Lazy import pipeline.load_json/save_json,共用 atomic write + Defender retry。"""
    sys.path.insert(0, str(BASE))
    from pipeline import load_json, save_json   # type: ignore[import-not-found]
    return load_json, save_json


def _load(path: Path, default):
    """殘留入口,內部走 pipeline.load_json;檔不在回 default。"""
    if not path.exists():
        return default
    load_json, _ = _load_pipeline_io()
    return load_json(path)


def extract_symbols_from_merged(merged: list[dict]) -> set[str]:
    """從 merged 萃取所有 unique symbol(bull / bear / top5 三欄)。共用入口。"""
    syms: set[str] = set()
    for r in merged:
        for fld in SIDE_FIELDS.values():
            for s in (r.get(fld) or "").split(","):
                s = s.strip()
                if s:
                    syms.add(s)
    return syms


def split_names(s: str) -> list[str]:
    """逗號分隔股名字串 → list,strip 並去空。共用入口(與 agents/tools._split_names 等價)。"""
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def build_index(
    syms: set[str],
    stock_map: dict,
    aliases: dict,
    sym_to_ticker: dict,
) -> dict:
    """
    對每個 symbol 解析 (code, name, ticker, display)。

    aliases 結構:
        {"新巨群": {"code": "1234", "name": "新巨群股份"}}     # 補完
    手動或自動補進去的對照,凌駕 stock_map(處理 TWSE 簡稱與 record 用法不一致)。

    code_to_name(從 stock_map 反向建,上市優先):用於股號 → 名稱反查。
    """
    code_to_name: dict[str, str] = {}
    for n, info in stock_map.items():
        code_to_name.setdefault(info["code"], n)
    # aliases 也覆蓋 code_to_name(若 aliases 有 name);
    # 跳過 lookup_aliases 寫進的 meta entries(_unmatched / _candidates_*)
    for s, info in aliases.items():
        if s.startswith("_") or not isinstance(info, dict):
            continue
        if info.get("code") and info.get("name"):
            code_to_name[info["code"]] = info["name"]

    by_symbol: dict[str, dict] = {}
    stats = {"total": len(syms), "resolved": 0, "only_code": 0, "only_name": 0, "neither": 0}

    for s in syms:
        # alias 優先(跳過 meta entries)
        a = aliases.get(s)
        if isinstance(a, dict) and (a.get("code") or a.get("name")):
            code = a.get("code")
            name = a.get("name")
        else:
            base = s.rstrip("*")
            m = CODE_RE.fullmatch(base)
            if m:
                code = m.group(1)
                name = code_to_name.get(code)
            else:
                info = stock_map.get(base)
                if info is None:
                    code = None
                    name = base
                else:
                    code = info["code"]
                    name = base

        ticker = sym_to_ticker.get(s) or sym_to_ticker.get(s.rstrip("*"))

        if code and name:
            display = f"{code} {name}"
            stats["resolved"] += 1
        elif code:
            display = code
            stats["only_code"] += 1
        elif name:
            display = f"{name} (待補)"
            stats["only_name"] += 1
        else:
            display = s
            stats["neither"] += 1

        by_symbol[s] = {
            "code": code, "name": name, "ticker": ticker, "display": display
        }

    return {"by_symbol": by_symbol, "stats": stats}


def write_index(merged: list[dict] | None = None) -> dict:
    """完整流程:讀資料 → 建 index → 寫檔。回傳 index dict。"""
    merged_data: list[dict] = merged if merged is not None else _load(MERGED, [])
    stock_map = _load(STOCK_MAP, {})
    aliases   = _load(ALIASES, {})
    fetch_log = _load(FETCH_LOG, {})
    sym_to_t  = fetch_log.get("symbol_to_ticker", {})

    syms = extract_symbols_from_merged(merged_data)
    idx  = build_index(syms, stock_map, aliases, sym_to_t)

    _, save_json = _load_pipeline_io()
    save_json(INDEX_FILE, idx)
    return idx


def main() -> int:
    idx = write_index()
    s = idx["stats"]
    print(f"symbol_index 已寫入 {INDEX_FILE.relative_to(BASE)}")
    print(f"  總 symbol     : {s['total']}")
    print(f"  完整解析(code+name): {s['resolved']} ({s['resolved']/s['total']*100:.1f}%)")
    print(f"  只有股號      : {s['only_code']}")
    print(f"  只有股名(待補): {s['only_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
