#!/usr/bin/env python3
"""
FSAの一覧(信用金庫)から対象をサンプリングし、ディスクロージャーPDFを
自動発見・ダウンロードして、収益構造・業種別貸出金・有価証券ポートフォリオを
抽出するバッチ実行スクリプト。パイロット(4金庫)の次段階として、
30〜50金庫規模での自動発見の成功率を検証するためのもの。

使い方:
  python3 scripts/run_disclosure_batch.py --xlsx /path/to/zenkoku.xlsx --out-dir data/batch1
  python3 scripts/run_disclosure_batch.py --xlsx /path/to/zenkoku.xlsx --sample-per-prefecture 1 --out-dir data/batch1
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from discover_disclosure_pdf import discover  # noqa: E402
from extract_disclosure_pdf import download, extract_all  # noqa: E402
from fetch_shinkin_financials import parse_workbook  # noqa: E402


def sample_institutions(records, per_prefecture):
    by_pref = {}
    for r in records:
        by_pref.setdefault(r["prefecture"], []).append(r)
    sample = []
    for pref, items in by_pref.items():
        sample.extend(items[:per_prefecture])
    return sample


def run_one(record, raw_dir):
    name = record["institution_name"]
    disclosure_url = record["disclosure_url"]
    result = {
        "institution_name": name,
        "institution_code": record["institution_code"],
        "prefecture": record["prefecture"],
        "disclosure_page": disclosure_url,
    }
    if not disclosure_url:
        result["status"] = "no_disclosure_url"
        return result

    candidates, debug = discover(disclosure_url)
    if not candidates:
        result["status"] = "pdf_not_found"
        result["debug"] = debug
        return result

    pdf_urls = [c["url"] for c in candidates]
    result["pdf_urls"] = pdf_urls

    local_paths = []
    download_errors = []
    for i, url in enumerate(pdf_urls):
        dest = raw_dir / f"{name}_{i}.pdf"
        try:
            download(url, dest)
            local_paths.append(dest)
        except Exception as e:  # noqa: BLE001
            # 同点候補の中には壊れたリンク(404等)が混ざることがあるため、
            # 個別のダウンロード失敗では全体を諦めず、成功した分だけで進める。
            download_errors.append(f"{url}: {e}")
    if download_errors:
        result["download_errors"] = download_errors
    if not local_paths:
        result["status"] = "download_or_parse_error"
        result["error"] = "; ".join(download_errors) or "no candidate could be downloaded"
        return result

    try:
        extracted = extract_all(local_paths)
    except Exception as e:  # noqa: BLE001
        result["status"] = "download_or_parse_error"
        result["error"] = str(e)
        return result

    result["status"] = "ok"
    result["found"] = {
        "industry_loans": bool(extracted["industry_loans"]),
        "securities_portfolio": bool(extracted["securities_portfolio"]),
        "income_statement": bool(extracted["income_statement"]),
    }
    result["data"] = extracted
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", required=True, help="金融庁の信用金庫・信用組合一覧Excel(ローカルパス)")
    parser.add_argument("--sample-per-prefecture", type=int, default=1, help="都道府県ごとに何金庫サンプリングするか")
    parser.add_argument("--limit", type=int, help="サンプル数の上限(未指定なら都道府県数まま)")
    parser.add_argument("--out-dir", default="data/batch1")
    args = parser.parse_args()

    records = parse_workbook(Path(args.xlsx), only_shinkin=True)
    sample = sample_institutions(records, args.sample_per_prefecture)
    if args.limit:
        sample = sample[: args.limit]

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "_raw_pdf"
    raw_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, record in enumerate(sample):
        name = record["institution_name"]
        print(f"[{i+1}/{len(sample)}] {name} ...", file=sys.stderr)
        if i:
            time.sleep(1.5)  # 同一ホスティング基盤への連続アクセスによるレート制限を避ける
        r = run_one(record, raw_dir)
        print(f"  -> {r['status']} {r.get('found', '')}", file=sys.stderr)
        results.append(r)
        # 個別institutionの完全データはファイルに逃がし、summaryは軽量に保つ
        detail_path = out_dir / f"{name}.json"
        with open(detail_path, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)

    summary = [
        {
            "institution_name": r["institution_name"],
            "prefecture": r["prefecture"],
            "status": r["status"],
            "found": r.get("found"),
        }
        for r in results
    ]
    with open(out_dir / "_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    ok = sum(1 for s in summary if s["status"] == "ok")
    print(f"\n=== {ok}/{len(summary)} institutions processed with a discovered PDF ===", file=sys.stderr)
    for status in ("no_disclosure_url", "pdf_not_found", "download_or_parse_error"):
        n = sum(1 for s in summary if s["status"] == status)
        if n:
            print(f"  {status}: {n}", file=sys.stderr)
    if ok:
        for field in ("industry_loans", "securities_portfolio", "income_statement"):
            n = sum(1 for s in summary if s["status"] == "ok" and s["found"][field])
            print(f"  {field} extracted: {n}/{ok}", file=sys.stderr)


if __name__ == "__main__":
    main()
