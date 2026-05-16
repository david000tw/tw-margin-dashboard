"""
把 data/all_data_merged.json 全量(或增量) UPSERT 進 PG market.chip_ocr。

用法:
    python scripts/export_chip_ocr_to_pg.py            # 全量
    python scripts/export_chip_ocr_to_pg.py --since 2026-05-01

權威源:all_data_merged.json (法人日資料 OCR 結果)。
PG 端 market.chip_ocr 是單向派生 copy。改錯永遠改 merged.json。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg  # type: ignore[import-not-found]

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

sys.path.insert(0, str(BASE))

DEFAULT_DSN = "postgresql://twstock:twstock_dev_pw@localhost:5433/twstock"


def build_upsert_rows(
    merged: list[dict], since: str | None = None,
) -> list[tuple]:
    """把 merged record list 轉成 UPSERT 用的 tuple list。"""
    rows = []
    for r in merged:
        d = r.get("date", "")
        if since and d < since:
            continue
        rows.append((
            d,
            r.get("rate", 0),
            r.get("bull", "") or "",
            r.get("bear", "") or "",
            r.get("top5_margin_reduce_inst_buy", "") or "",
        ))
    return rows


UPSERT_SQL = """
INSERT INTO market.chip_ocr (date, rate, bull, bear, top5_margin_reduce_inst_buy, source, updated_at)
VALUES (%s, %s, %s, %s, %s, 'scantrader', NOW())
ON CONFLICT (date) DO UPDATE SET
    rate = EXCLUDED.rate,
    bull = EXCLUDED.bull,
    bear = EXCLUDED.bear,
    top5_margin_reduce_inst_buy = EXCLUDED.top5_margin_reduce_inst_buy,
    updated_at = NOW()
"""


def run_export(
    *, merged: list[dict], dsn: str, since: str | None = None,
) -> dict:
    """執行 UPSERT。回 stats dict。"""
    rows = build_upsert_rows(merged, since=since)
    if not rows:
        return {"upserted": 0}

    conn = psycopg.connect(dsn, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
        conn.commit()
    finally:
        conn.close()

    return {"upserted": len(rows)}


def main() -> int:
    from pipeline import load_json  # type: ignore[import-not-found]

    ap = argparse.ArgumentParser(description="Export merged.json chip OCR to PG")
    ap.add_argument("--since", help="只 export date >= since 的 record (YYYY-MM-DD)")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN", DEFAULT_DSN),
                     help="PG connection string")
    args = ap.parse_args()

    merged = load_json(DATA / "all_data_merged.json")
    print(f"讀進 {len(merged)} 筆 merged record")
    print(f"PG DSN: {args.dsn}")
    if args.since:
        print(f"增量模式 since={args.since}")

    try:
        stats = run_export(merged=merged, dsn=args.dsn, since=args.since)
        print(f"UPSERT 完成: {stats['upserted']} 筆")
        return 0
    except psycopg.OperationalError as e:
        print(f"[ERR] PG 連不上: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
