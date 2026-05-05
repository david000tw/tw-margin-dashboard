"""
Fetch daily close prices for all stocks that appear in data/all_data_merged.json
(bull / bear / top5_margin_reduce_inst_buy), so the dashboard can compute
post-event returns and correlations.

Output (compact format the dashboard reads directly):
  data/stock_map.json       { "台積電": {"code": "2330", "market": "TW"}, ... }
  data/stock_prices.json    {
                              "dates":  ["2021-01-04", "2021-01-05", ...],
                              "prices": {
                                "2330.TW": {"start": 0,   "csv": "600.0,602.5,..."},
                                "2454.TW": {"start": 120, "csv": "880.0,,885.0,..."}
                              }
                            }
  data/stock_fetch_log.json { "ok": [...], "unknown_names": [...], "no_price": [...] }

Usage (from repo root):
  python scripts/fetch_prices.py

Requirements:
  pip install yfinance requests pandas
"""

import json
import re
import sys
import time
from pathlib import Path

BASE  = Path(__file__).resolve().parent.parent
DATA  = BASE / "data"
MERGED = DATA / "all_data_merged.json"
MAP_FILE     = DATA / "stock_map.json"
PRICES_FILE  = DATA / "stock_prices.json"
FETCH_LOG    = DATA / "stock_fetch_log.json"

START_DATE = "2021-01-01"
CODE_RE = re.compile(r"^(\d{4,6})([A-Z]*)$")


def log(msg):
    print(msg, flush=True)


# ── 建立股名 → 股號對照 ─────────────────────────────────────

def build_stock_map():
    """從 TWSE + TPEx OpenAPI 抓上市/上櫃公司清單,建立 {簡稱: {code, market}}。"""
    import requests
    mapping = {}

    # TWSE 上市
    log("下載 TWSE 上市公司清單...")
    r = requests.get(
        "https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=30
    )
    r.raise_for_status()
    for item in r.json():
        code = (item.get("公司代號") or "").strip()
        name = (item.get("公司簡稱") or "").strip()
        if code and name:
            mapping[name] = {"code": code, "market": "TW"}
    log(f"  TWSE 上市 {sum(1 for v in mapping.values() if v['market']=='TW')} 筆")

    # TPEx 上櫃
    log("下載 TPEx 上櫃公司清單...")
    r = requests.get(
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", timeout=30
    )
    r.raise_for_status()
    otc_added = 0
    for item in r.json():
        code = (item.get("SecuritiesCompanyCode") or "").strip()
        name = (item.get("CompanyAbbreviation") or "").strip()
        if code and name and name not in mapping:  # 上市優先
            mapping[name] = {"code": code, "market": "TWO"}
            otc_added += 1
    log(f"  TPEx 上櫃 {otc_added} 筆 (未與上市重名者)")
    log(f"合計 mapping 筆數: {len(mapping)}")

    MAP_FILE.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"寫入 {MAP_FILE}")
    return mapping


# ── 從 merged 萃取所有股票符號 ─────────────────────────────

def extract_symbols(merged):
    syms = set()
    for r in merged:
        for field in ("bull", "bear", "top5_margin_reduce_inst_buy"):
            for s in (r.get(field) or "").split(","):
                s = s.strip()
                if s:
                    syms.add(s)
    return syms


def normalize_symbols(syms, smap):
    """返回 {original_symbol: 'code.TW' or 'code.TWO'} 和 unknown 清單。
       股號優先猜 .TW(上市),若 yfinance 抓不到會在下一階段記錄。"""
    result = {}
    unknown = []
    # 建反向對照:code -> market（從 mapping 值反推)
    code_to_market = {}
    for name, info in smap.items():
        code_to_market[info["code"]] = info["market"]

    for s in syms:
        m = CODE_RE.fullmatch(s)
        if m:
            code = m.group(1)
            market = code_to_market.get(code, "TW")  # 預設上市
            result[s] = f"{code}.{market}"
        else:
            # 股名查對照
            name = s.rstrip("*")  # 去警示符號
            if name in smap:
                info = smap[name]
                result[s] = f"{info['code']}.{info['market']}"
            else:
                unknown.append(s)
    return result, unknown


# ── yfinance 抓收盤價 ──────────────────────────────────────

def download_prices(tickers, start=START_DATE):
    import yfinance as yf
    prices = {}
    no_price = []
    tickers = sorted(set(tickers))
    batch_size = 30
    total = len(tickers)

    for i in range(0, total, batch_size):
        batch = tickers[i : i + batch_size]
        log(f"  [{i+1}-{i+len(batch)}/{total}] downloading batch...")
        try:
            df = yf.download(
                " ".join(batch),
                start=start,
                progress=False,
                group_by="ticker",
                threads=True,
                auto_adjust=True,
            )
            for t in batch:
                try:
                    if len(batch) == 1:
                        close = df["Close"]
                    else:
                        close = df[t]["Close"]
                    close = close.dropna()
                    if not close.empty:
                        prices[t] = {
                            d.strftime("%Y-%m-%d"): round(float(v), 2)
                            for d, v in close.items()
                        }
                    else:
                        no_price.append(t)
                except (KeyError, AttributeError):
                    # KeyError: ticker 不在 multi-ticker DataFrame
                    # AttributeError: 該 ticker 對應的不是 Series 而是 ndarray(yfinance 偶發)
                    no_price.append(t)
            time.sleep(0.5)
        except Exception as e:
            log(f"  batch error: {e}")
            for t in batch:
                no_price.append(t)
    return prices, no_price


# ── 壓縮成 dashboard 讀的格式 ───────────────────────────────

def to_compact(prices: dict) -> dict:
    """
    Input :  { ticker: { "YYYY-MM-DD": close, ... } }
    Output:  { dates:[...], prices:{ ticker:{start:int, csv:"p1,p2,..."} } }

    csv 中缺值用空字串("")表示,dashboard 的 readPrice() 會把空字串視為 null。
    每個 ticker 只儲 start..last 的連續區間,不存末尾的空格以節省空間。
    """
    all_dates = set()
    for tdata in prices.values():
        all_dates.update(tdata.keys())
    dates_sorted = sorted(all_dates)
    date_to_idx = {d: i for i, d in enumerate(dates_sorted)}

    out_prices = {}
    for ticker, tdata in prices.items():
        if not tdata:
            continue
        idxs = sorted(date_to_idx[d] for d in tdata)
        start = idxs[0]
        end = idxs[-1]
        cells = []
        for i in range(start, end + 1):
            d = dates_sorted[i]
            v = tdata.get(d)
            # download_prices 已經 round(.., 2);維持與既有格式一致(整數會帶 ".0")
            cells.append("" if v is None else str(v))
        out_prices[ticker] = {"start": start, "csv": ",".join(cells)}

    return {"dates": dates_sorted, "prices": out_prices}


# ── 主程式 ─────────────────────────────────────────────────

def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    log("=== Step 1: 建立/載入 stock_map ===")
    if MAP_FILE.exists() and "--refresh-map" not in sys.argv:
        smap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
        log(f"載入快取 {MAP_FILE}: {len(smap)} 筆 (用 --refresh-map 強制更新)")
    else:
        smap = build_stock_map()

    log("\n=== Step 2: 從 merged 萃取所有股票符號 ===")
    merged = json.loads(MERGED.read_text(encoding="utf-8"))
    syms = extract_symbols(merged)
    log(f"總 unique 符號: {len(syms)}")

    log("\n=== Step 3: 符號正規化 ===")
    ticker_map, unknown = normalize_symbols(syms, smap)
    log(f"可對應: {len(ticker_map)},  查無股名: {len(unknown)}")
    if unknown:
        log(f"  查無樣本 (前 10): {unknown[:10]}")

    log("\n=== Step 4: yfinance 批次下載 ===")
    unique_tickers = sorted(set(ticker_map.values()))
    log(f"唯一 ticker 數: {len(unique_tickers)}")
    prices, no_price = download_prices(unique_tickers)

    log(f"\n=== 結果 ===")
    log(f"  成功抓到價格的 ticker : {len(prices)}")
    log(f"  yfinance 無回應的 ticker: {len(no_price)}")

    log("\n=== Step 5: 壓縮並寫入 data/stock_prices.json ===")
    compact = to_compact(prices)
    PRICES_FILE.write_text(
        json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_mb = PRICES_FILE.stat().st_size / 1024 / 1024
    log(f"寫入 {PRICES_FILE}: {size_mb:.2f} MB ({len(compact['dates'])} dates × {len(compact['prices'])} tickers)")

    fetch_log = {
        "ok_tickers": sorted(prices.keys()),
        "unknown_names": sorted(unknown),
        "no_price_tickers": sorted(no_price),
        "symbol_to_ticker": ticker_map,
    }
    FETCH_LOG.write_text(
        json.dumps(fetch_log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"寫入 {FETCH_LOG}")

    # === Step 6: 自動 chain lookup_aliases + symbol_resolve ===
    # fetch_log 變更後 unknown_names 可能有新項目;讓 lookup_aliases 自動推導
    # 寫入 stock_aliases.json,再重建 symbol_index.json,以便 dashboard 立即看到 enrich
    log("\n=== Step 6: 自動更新 stock_aliases + symbol_index ===")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lookup_aliases import main as run_lookup  # type: ignore[import-not-found]
        from symbol_resolve import write_index  # type: ignore[import-not-found]
        # lookup_aliases --write
        old_argv = sys.argv
        sys.argv = ["lookup_aliases.py", "--write"]
        try:
            run_lookup()
        finally:
            sys.argv = old_argv
        write_index()
        log("[ok] stock_aliases.json + symbol_index.json 已更新")
    except Exception as e:
        log(f"[WARN] 自動 chain 失敗(不影響主流程): {e}")


if __name__ == "__main__":
    main()
