"""
Fetch daily close prices for all stocks that appear in data/all_data_merged.json
(bull / bear / top5_margin_reduce_inst_buy), so the dashboard can compute
post-event returns and correlations.

Output:
  data/stock_map.json       { "台積電": {"code": "2330", "market": "TW"}, ... }
  data/stock_prices.json    { "2330.TW": {"2021-05-24": 600.0, ...}, ... }
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
                except (KeyError, Exception):
                    no_price.append(t)
            time.sleep(0.5)
        except Exception as e:
            log(f"  batch error: {e}")
            for t in batch:
                no_price.append(t)
    return prices, no_price


# ── 主程式 ─────────────────────────────────────────────────

def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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

    log("\n=== Step 5: 寫入 data/stock_prices.json ===")
    PRICES_FILE.write_text(
        json.dumps(prices, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_mb = PRICES_FILE.stat().st_size / 1024 / 1024
    log(f"寫入 {PRICES_FILE}: {size_mb:.2f} MB")

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


if __name__ == "__main__":
    main()
