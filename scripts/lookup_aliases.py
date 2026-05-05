"""
對 unknown_names(stock_fetch_log.json 裡查不到 ticker 的股名)做別名推導,
產出 data/stock_aliases.json 給 symbol_resolve.py 套用。

策略(由信任度高到低):
  1. strip(*) 後直接命中 stock_map → high confidence
  2. ISIN 一覽表(TWSE 含上市/上櫃/興櫃完整清單)用 normalize(去 *、-KY、空白)後
     雙邊比對唯一命中 → high confidence(會抓到 stock_map 漏的興櫃股)
  3. 與 stock_map 名字做 substring 雙向比對且唯一候選 → high confidence
  4. 多重候選 → 寫入 _candidates_<name> 給用戶手工選
  5. 完全沒候選 → 留 _unmatched 清單

用法:
  python scripts/lookup_aliases.py              # dry-run,印推導結果不寫檔
  python scripts/lookup_aliases.py --write      # 寫入 data/stock_aliases.json
  python scripts/lookup_aliases.py --no-isin    # 跳過 ISIN 抓(要連 TWSE,慢一些)
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
STOCK_MAP = DATA / "stock_map.json"
FETCH_LOG = DATA / "stock_fetch_log.json"
ALIASES   = DATA / "stock_aliases.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def find_substring_matches(name: str, smap: dict) -> list[str]:
    """雙向 substring:smap 名稱包含 name,或 name 包含 smap 名稱。"""
    return [n for n in smap if name in n or n in name]


def _normalize(s: str) -> str:
    """去掉 *(警示)、-KY / -DR(後綴)、空白(全形/半形)。"""
    return s.strip().replace("*", "").replace("-KY", "").replace("-DR", "").replace(" ", "").replace("　", "")


def fetch_isin_normalize_index() -> dict[str, list[tuple[str, str, str]]]:
    """
    抓 TWSE ISIN 一覽表(strMode=2 上市,strMode=4 上櫃含興櫃),只留 CFICode 開頭 'E'
    的普通股類,建 normalize(name) → [(orig_name, code, market)] 索引。

    比 TWSE/TPEx OpenAPI 多含興櫃股票。
    """
    import requests
    import pandas as pd

    out: dict[str, list[tuple[str, str, str]]] = {}
    for mode, market in [(2, "TW"), (4, "TWO")]:
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        r = requests.get(url, timeout=30)
        r.encoding = "big5"
        dfs = pd.read_html(io.StringIO(r.text))
        df = dfs[0]
        df.columns = df.iloc[0]
        df = df[1:].reset_index(drop=True)
        for _, row in df.iterrows():
            s = row.iloc[0]
            cfi = row.get("CFICode", "") or ""
            if not isinstance(s, str):
                continue
            if not (isinstance(cfi, str) and cfi.startswith("E")):
                continue
            m = re.match(r"^(\d{4,6}[A-Z]*)\s+(.+)$", s)
            if not m:
                continue
            code, name = m.group(1), m.group(2).strip()
            out.setdefault(_normalize(name), []).append((name, code, market))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="寫入 data/stock_aliases.json(預設只印)")
    ap.add_argument("--no-isin", action="store_true", help="跳過 ISIN normalize 比對(不連 TWSE)")
    args = ap.parse_args()

    smap = load(STOCK_MAP)          # {name: {code, market}}
    flog = load(FETCH_LOG)
    unknown = flog.get("unknown_names", [])
    existing_aliases = load(ALIASES) if ALIASES.exists() else {}

    isin_idx: dict[str, list[tuple[str, str, str]]] = {}
    if not args.no_isin:
        try:
            print("[1/2] 抓 TWSE ISIN 一覽表(含興櫃)...")
            isin_idx = fetch_isin_normalize_index()
            print(f"  ISIN 普通股 normalize 索引: {len(isin_idx)} 個 entry")
        except Exception as e:
            print(f"[WARN] ISIN 抓取失敗,跳過該層比對: {e}", file=sys.stderr)

    print("[2/2] 比對 unknown_names...")
    auto: dict[str, dict] = {}              # 高信心,寫入 aliases
    candidates: dict[str, list] = {}         # 多重候選,給人手工挑
    unmatched: list[str] = []

    for u in unknown:
        if u in existing_aliases and isinstance(existing_aliases[u], dict):
            continue
        base = u.rstrip("*")

        # Layer 1: strip(*) 直接命中 stock_map
        if base in smap:
            info = smap[base]
            auto[u] = {"code": info["code"], "name": base, "source": "strip_star"}
            continue

        # Layer 2: ISIN normalize 唯一命中(抓 stock_map 漏的興櫃 + 警示符號錯位)
        norm = _normalize(base)
        isin_hits = isin_idx.get(norm, [])
        if len(isin_hits) == 1:
            orig_name, code, _ = isin_hits[0]
            auto[u] = {"code": code, "name": orig_name, "source": "isin_normalize"}
            continue

        # Layer 3: 與 stock_map 雙向 substring,唯一候選
        sub_hits = find_substring_matches(base, smap)
        if len(sub_hits) == 1:
            n = sub_hits[0]
            auto[u] = {"code": smap[n]["code"], "name": n, "source": "substring_unique"}
        elif len(sub_hits) > 1:
            candidates[u] = [
                {"name": n, "code": smap[n]["code"], "market": smap[n]["market"]}
                for n in sub_hits[:8]
            ]
        else:
            unmatched.append(u)

    print(f"unknown 總數: {len(unknown)}")
    print(f"  既有 aliases: {len(existing_aliases)}")
    print(f"  自動推導: {len(auto)}")
    print(f"  多重候選: {len(candidates)}")
    print(f"  完全找不到: {len(unmatched)}")

    if auto:
        print("\n=== 自動推導樣本(前 10) ===")
        for k, v in list(auto.items())[:10]:
            print(f"  {k!r} → {v['code']} {v['name']!r} ({v['source']})")
    if candidates:
        print("\n=== 多重候選(請手工編輯 stock_aliases.json) ===")
        for k, cands in candidates.items():
            names = [c["name"] for c in cands]
            print(f"  {k!r}: {names}")
    if unmatched:
        print(f"\n=== 完全找不到({len(unmatched)} 個,興櫃/已下市/特殊命名) ===")
        print("  " + ", ".join(repr(u) for u in unmatched[:30]) + (" ..." if len(unmatched)>30 else ""))

    if args.write:
        out = dict(existing_aliases)
        out.update(auto)
        # 多重候選用 _candidates_<name> 留 stub,給用戶看
        for k, cands in candidates.items():
            out.setdefault(f"_candidates_{k}", {"candidates": cands})
        # 找不到的留註記
        if unmatched:
            out.setdefault("_unmatched", unmatched)
        ALIASES.write_text(
            json.dumps(out, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n[ok] 已寫入 {ALIASES.relative_to(BASE)}")
        print("接著重跑:python scripts/symbol_resolve.py")
    else:
        print("\n[dry-run] 未寫檔。加 --write 寫入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
