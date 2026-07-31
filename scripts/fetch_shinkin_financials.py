#!/usr/bin/env python3
"""
金融庁「中小・地域金融機関情報」から信用金庫の財務データを取得するスクリプト。

データソース:
  https://www.fsa.go.jp/policy/chusho/shihyou.html
  (信用金庫・信用組合の主要勘定一覧 Excel)

取得できる項目:
  種別, 都道府県, 金融機関コード, 金融機関名, 法人番号, 本店所在地, 店舗数,
  ディスクロージャー(Web) URL, 預金積金(百万円), 貸出金(百万円),
  自己資本比率(%), 不良債権比率(%),
  中小企業等向け貸出残高(百万円)[前年度/当年度],
  中小企業等向け貸出先件数[前年度/当年度]

使い方:
  python3 fetch_shinkin_financials.py
  python3 fetch_shinkin_financials.py --url https://www.fsa.go.jp/policy/chusho/shihyou/zenkoku/2025-2.xlsx --out data/shinkin_2025.csv

金融庁のファイルURLは年度によって変わる(例: 2025-2.xlsx)。
最新のURLは https://www.fsa.go.jp/policy/chusho/shihyou.html の
「信用金庫・信用組合」欄のリンクから確認できる。
"""
import argparse
import csv
import json
import sys
import urllib.request
from pathlib import Path

import openpyxl

DEFAULT_URL = "https://www.fsa.go.jp/policy/chusho/shihyou/zenkoku/2025-2.xlsx"

FIELDNAMES = [
    "type",
    "prefecture",
    "institution_code",
    "institution_name",
    "corporate_number",
    "head_office_location",
    "branch_count",
    "disclosure_url",
    "deposits_million_yen",
    "loans_million_yen",
    "capital_adequacy_ratio_pct",
    "npl_ratio_pct",
    "sme_loans_prev_million_yen",
    "sme_loans_latest_million_yen",
    "sme_loan_count_prev",
    "sme_loan_count_latest",
]


def download(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    return dest


def parse_workbook(path: Path, only_shinkin: bool = True):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(min_row=3, values_only=True)  # skip 2 header rows

    records = []
    for row in rows:
        if row[0] is None and row[3] is None:
            continue
        record = dict(zip(FIELDNAMES, row))
        if only_shinkin and record.get("type") != "信用金庫":
            continue
        records.append(record)
    return records


def write_csv(records, path: Path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records)


def write_json(records, path: Path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help="取得元Excelファイルのurl")
    parser.add_argument("--input", help="既にダウンロード済みのExcelファイルを使う場合のパス（--urlより優先）")
    parser.add_argument("--out", default="data/shinkin_financials.csv", help="出力ファイルパス(.csvまたは.json)")
    parser.add_argument("--all-institutions", action="store_true", help="信用金庫だけでなく信用組合も含める")
    parser.add_argument("--keep-raw", help="ダウンロードした生のExcelファイルを保存するパス")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.input:
        xlsx_path = Path(args.input)
    else:
        xlsx_path = Path(args.keep_raw) if args.keep_raw else Path(out_path.parent / "_tmp_shinkin.xlsx")
        print(f"Downloading {args.url} ...", file=sys.stderr)
        download(args.url, xlsx_path)

    records = parse_workbook(xlsx_path, only_shinkin=not args.all_institutions)
    print(f"Parsed {len(records)} institutions.", file=sys.stderr)

    if out_path.suffix.lower() == ".json":
        write_json(records, out_path)
    else:
        write_csv(records, out_path)
    print(f"Wrote {out_path}", file=sys.stderr)

    if not args.input and not args.keep_raw:
        xlsx_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
