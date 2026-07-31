#!/usr/bin/env python3
"""
scripts/pilot_institutions.json に列挙した信用金庫について、
ディスクロージャー資料PDFをダウンロードし、収益構造・業種別貸出金・
有価証券ポートフォリオを抽出してdata/pilot/配下にJSON出力する。

使い方:
  python3 scripts/run_disclosure_pilot.py
  python3 scripts/run_disclosure_pilot.py --manifest scripts/pilot_institutions.json --out-dir data/pilot
"""
import argparse
import json
from pathlib import Path

from extract_disclosure_pdf import download, extract_all


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", default=str(Path(__file__).parent / "pilot_institutions.json"))
    parser.add_argument("--out-dir", default="data/pilot")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "_raw_pdf"
    raw_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for inst in manifest:
        name = inst["name"]
        print(f"=== {name} ===")
        try:
            local_paths = []
            for i, url in enumerate(inst["pdf_urls"]):
                dest = raw_dir / f"{name}_{i}.pdf"
                print(f"  downloading {url}")
                download(url, dest)
                local_paths.append(dest)
            result = extract_all(local_paths)
        except Exception as e:  # noqa: BLE001 - パイロットなので個別失敗を継続する
            print(f"  FAILED: {e}")
            summary.append({"name": name, "status": "error", "error": str(e)})
            continue

        result["institution_name"] = name
        result["source_urls"] = inst["pdf_urls"]
        out_path = out_dir / f"{name}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        found = {
            "industry_loans": bool(result["industry_loans"]),
            "securities_portfolio": bool(result["securities_portfolio"]),
            "income_statement": bool(result["income_statement"]),
        }
        print(f"  -> {out_path} ({found})")
        summary.append({"name": name, "status": "ok", "found": found})

    print("\n=== Summary ===")
    for s in summary:
        print(s)


if __name__ == "__main__":
    main()
