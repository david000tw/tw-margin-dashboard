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

import json
import re
from pathlib import Path

BASE       = Path(__file__).resolve().parent.parent
DATA       = BASE / "data"
MERGED     = DATA / "all_data_merged.json"
STOCK_MAP  = DATA / "stock_map.json"
ALIASES    = DATA / "stock_aliases.json"
FETCH_LOG  = DATA / "stock_fetch_log.json"
INDEX_FILE = DATA / "symbol_index.json"

CODE_RE = re.compile(r"^(\d{4,6})([A-Z]*)$")


def _load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def extract_symbols_from_merged(merged: list[dict]) -> set[str]:
    syms: set[str] = set()
    for r in merged:
        for fld in ("bull", "bear", "top5_margin_reduce_inst_buy"):
            for s in (r.get(fld) or "").split(","):
                s = s.strip()
                if s:
                    syms.add(s)
    return syms


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

    INDEX_FILE.write_text(
        json.dumps(idx, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
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
