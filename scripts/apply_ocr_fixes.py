"""
對歷史 data/all_data_merged.json 套用 data/ocr_corrections.json 的錯字對照,
把 record 中的 OCR 錯字直接改寫為正字。

dry-run(預設)        印出會改的 record 數與每個對照的命中次數,不寫檔
--write              真正改寫 merged 並重建年份檔
--dry-write tmp.json 寫到指定路徑(不動 merged),供 diff 審視

注意:
  - 這是 destructive 操作(改寫 single source of truth)。建議先跑 dry-run 看影響面
  - 寫入後,daily-fetch.md Step 6a 的 ocr_corrections 仍會繼續阻擋未來新錯字
  - dashboard 的 alias-aware 搜尋(SYMBOL_DISPLAY 反查)即使不跑這個 script 也能讓
    使用者搜「正字」找到「錯字 record」,所以這個 batch fix 是「錦上添花」不是必要
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BASE      = Path(__file__).resolve().parent.parent
MERGED    = BASE / "data" / "all_data_merged.json"
FIXES     = BASE / "data" / "ocr_corrections.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真正改寫 merged + 重建年份檔")
    ap.add_argument("--dry-write", help="寫到指定路徑(不動 merged)")
    args = ap.parse_args()

    fixes: dict[str, str] = json.loads(FIXES.read_text(encoding="utf-8"))["ocr_to_correct"]
    merged: list[dict] = json.loads(MERGED.read_text(encoding="utf-8"))

    hits: Counter[str] = Counter()
    records_changed = 0

    for r in merged:
        changed = False
        for fld in ("bull", "bear", "top5_margin_reduce_inst_buy"):
            raw = r.get(fld, "")
            if not raw:
                continue
            new_parts = []
            for sym in raw.split(","):
                s = sym.strip()
                if s in fixes:
                    hits[s] += 1
                    new_parts.append(fixes[s])
                    changed = True
                elif s:
                    new_parts.append(s)
            if changed:
                r[fld] = ",".join(new_parts)
        if changed:
            records_changed += 1

    print(f"= OCR 對照 {len(fixes)} 條,merged {len(merged)} 筆")
    print(f"= 影響 record 數: {records_changed}")
    print(f"= 各對照命中:")
    for k, c in hits.most_common():
        print(f"    {c:5d}  {k!r} → {fixes[k]!r}")

    if args.dry_write:
        Path(args.dry_write).write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[dry-write] 寫入 {args.dry_write}")
        return 0

    if not args.write:
        print("\n[dry-run] 未寫檔。--write 真正寫入 merged + 重建年份檔。")
        return 0

    # 真正寫
    MERGED.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[ok] 寫入 {MERGED}")

    # 重建年份檔(透過 pipeline.regen_years)
    sys.path.insert(0, str(BASE))
    from pipeline import regen_years  # type: ignore[import-not-found]
    regen_years()
    return 0


if __name__ == "__main__":
    sys.exit(main())
