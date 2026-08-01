#!/usr/bin/env python3
"""
信用金庫の個別ディスクロージャー資料(PDF)から、
- 貸出金の業種別内訳
- 有価証券の種類別・残存期間別残高
- 損益の状況（収益構造）
を抽出するスクリプト。

各信用金庫のPDFはレイアウト・ページ数が大きく異なるが、開示項目自体は
信用金庫法施行規則等に基づく共通様式（日本標準産業分類の大分類ベースの
業種区分など）に従っているため、キーワード検索でページを特定したうえで
行単位のラベル・数値マッチングで抽出する方式を取っている。

使い方:
  python3 extract_disclosure_pdf.py --pdf /path/to/disclosure.pdf --out result.json
  python3 extract_disclosure_pdf.py --url https://.../disclosure.pdf --out result.json

注意:
  信用金庫ごとにPDFのレイアウトが異なるため、本スクリプトは
  「よくある形式」に対する抽出を行うベストエフォート実装であり、
  金庫によっては一部の項目が取得できない場合がある。
  取得できなかった項目は raw_pages に該当ページのテキストを残すので、
  必要に応じて個別に確認・調整すること。
"""
import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import pdfplumber

try:
    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image
    import io as _io
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

RETRYABLE_HTTP_CODES = {403, 429, 500, 502, 503, 504}
# この文字数未満しかテキストが取れなかったページは、スキャン画像や
# アウトラインフォント化されたページの可能性が高いとみなしOCRを試す。
OCR_FALLBACK_THRESHOLD = 30

INDUSTRY_CATEGORIES = [
    "製造業",
    "農業、林業",
    "漁業",
    "鉱業、採石業、砂利採取業",
    "建設業",
    "電気・ガス・熱供給・水道業",
    "情報通信業",
    "運輸業、郵便業",
    "卸売業、小売業",
    "金融業、保険業",
    "不動産業",
    "うち不動産賃貸業",
    "物品賃貸業",
    "学術研究、専門・技術サービス業",
    "宿泊業",
    "飲食業",
    "生活関連サービス業、娯楽業",
    "教育、学習支援業",
    "医療、福祉",
    "その他のサービス",
    "小計",
    "地方公共団体",
    "国・地方公共団体等",
    "個人",
    "その他",
    "合計",
]

SECURITY_CATEGORIES = [
    "国債",
    "地方債",
    "短期社債",
    "社債",
    "株式",
    "外国証券",
    "その他の証券",
    "合計",
]

INCOME_STATEMENT_ITEMS = [
    "経常収益",
    "資金運用収益",
    "貸出金利息",
    "有価証券利息配当金",
    "役務取引等収益",
    "その他業務収益",
    "その他経常収益",
    "経常費用",
    "資金調達費用",
    "預金利息",
    "役務取引等費用",
    "その他業務費用",
    "経費",
    "人件費",
    "物件費",
    "経常利益",
    "当期純利益",
    "業務純益",
    "コア業務純益",
]

NUMBER_RE = re.compile(r"△?[\d,]+(?:\.\d+)?%?|―|ー|-")


def strip_spaces(s: str) -> str:
    return re.sub(r"\s+", "", s)


def to_number(token: str):
    token = token.strip()
    if token in ("―", "ー", "-", ""):
        return None
    negative = token.startswith("△")
    token = token.lstrip("△").replace(",", "")
    is_pct = token.endswith("%")
    token = token.rstrip("%")
    try:
        value = float(token) if "." in token else int(token)
    except ValueError:
        return None
    if negative:
        value = -value
    return value


def safe_url(url):
    """URLのpath/queryに未エンコードの非ASCII文字(日本語ファイル名等)が
    含まれている場合、urllibがリクエストライン組み立て時にasciiエンコードで
    失敗するため、パーセントエンコードして安全な形に変換する。"""
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%")
    query = quote(parts.query, safe="=&%")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def download(url: str, dest: Path, retries: int = 3):
    last_err = None
    for attempt in range(retries):
        if attempt:
            time.sleep(2 * attempt)
        req = urllib.request.Request(safe_url(url), headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
                f.write(resp.read())
            return
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in RETRYABLE_HTTP_CODES:
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
    raise last_err


def _ocr_page(pdf_path: Path, page_number: int) -> str:
    """該当ページを画像化してOCRする(スキャンPDF・アウトラインフォント化
    されたページ用のフォールバック)。tesseract等が無い環境では黙って
    空文字列を返す。"""
    if not OCR_AVAILABLE:
        return ""
    try:
        doc = fitz.open(pdf_path)
        page = doc[page_number]
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
        img = Image.open(_io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(img, lang="jpn")
    except Exception:  # noqa: BLE001
        return ""


def load_pages(pdf_path: Path, use_ocr: bool = True):
    """一部の金庫のPDFは、ページのmediabox外(印刷・表示はされない領域)に
    縦書きサイドバー見出し等の「幽霊」文字オブジェクトが残っており、
    pdfplumberのextract_text()がこれを本文の行に混ぜてしまうことがある
    (奈良信用金庫等)。ページをmediabox内にcropしてから抽出することで
    これらの幽霊文字を除外する。

    抽出されたテキストが極端に短いページは、スキャン画像やアウトライン
    フォント化されたページの可能性が高いため、OCRで再試行する。"""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            cropped = page.crop((0, 0, page.width, page.height))
            text = cropped.extract_text() or ""
            if use_ocr and len(text.strip()) < OCR_FALLBACK_THRESHOLD:
                ocr_text = _ocr_page(pdf_path, i)
                if len(ocr_text.strip()) > len(text.strip()):
                    text = ocr_text
            pages.append(unicodedata.normalize("NFKC", text))
    return pages


def find_pages(pages, keywords):
    """一部の金庫は見出しを「経 常 収 益」のように1文字ずつ全角スペースで
    区切って組版しているため(昭和信用金庫等)、単純な部分文字列一致では
    該当ページを検出できない。ページテキストから空白を除いた版でも
    照合することで、そうした表記にも対応する。"""
    hits = []
    for i, text in enumerate(pages):
        compact = strip_spaces(text)
        if any(k in text or k in compact for k in keywords):
            hits.append(i)
    return hits


def _label_pattern(category: str) -> re.Pattern:
    # PDF抽出時にラベルの文字間に空白が挿入されることがあるため、
    # 各文字の間に任意の空白を許容する正規表現にする。
    chars = [re.escape(c) for c in category]
    return re.compile(r"^\s*" + r"\s*".join(chars))


def extract_label_number_lines(text, categories):
    """categories内のラベルで始まる行から、当該行の数値列を抽出する。"""
    results = []
    patterns = {cat: _label_pattern(cat) for cat in categories}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for cat in categories:
            m = patterns[cat].match(stripped)
            if m:
                rest = stripped[m.end():]
                nums = [to_number(n) for n in NUMBER_RE.findall(rest)]
                nums = [n for n in nums if n is not None]
                if nums:
                    results.append({"label": cat, "values": nums, "raw_line": stripped})
                break
    return results


def extract_industry_loans(pages):
    # 見出し(「貸出金業種別内訳」等)が図形化されている、あるいは表記ゆれで
    # 見つからないページがあるため(高知信用金庫等)、見出しキーワードに
    # 一致するページを優先しつつ、無ければ全ページを対象に業種区分の
    # 行パターン自体で判定する。
    idx = find_pages(pages, ["貸出金業種別", "貸出金の業種別", "業種別内訳"])
    ordered = idx + [i for i in range(len(pages)) if i not in idx]
    for i in ordered:
        rows = extract_label_number_lines(pages[i], INDUSTRY_CATEGORIES)
        if len(rows) >= 5:
            return {"source_page": i + 1, "rows": rows}
    return None


def extract_securities_portfolio(pages):
    idx = find_pages(pages, ["有価証券の種類別", "残存期間別の残高", "種類別の平均残高"])
    ordered = idx + [i for i in range(len(pages)) if i not in idx]
    out = {}
    for i in ordered:
        rows = extract_label_number_lines(pages[i], SECURITY_CATEGORIES)
        if len(rows) >= 3:
            out.setdefault("pages", []).append({"source_page": i + 1, "rows": rows})
            if len(out["pages"]) >= 4:
                break
    return out or None


def extract_income_statement(pages):
    idx = find_pages(pages, ["損益計算書", "損益の状況", "経常収益"])
    ordered = idx + [i for i in range(len(pages)) if i not in idx]
    for i in ordered:
        rows = extract_label_number_lines(pages[i], INCOME_STATEMENT_ITEMS)
        # 5年間の主要経営指標だけを載せる簡易版だと、経常収益・業務純益・
        # 経常利益・当期純利益の4項目しか無いことがある(高知信用金庫等)。
        if len(rows) >= 4:
            return {"source_page": i + 1, "rows": rows}
    return None


def extract_all_from_pages(pages):
    return {
        "industry_loans": extract_industry_loans(pages),
        "securities_portfolio": extract_securities_portfolio(pages),
        "income_statement": extract_income_statement(pages),
        "page_count": len(pages),
    }


def extract_all(pdf_paths):
    """1つ以上のPDF(分割されたディスクロージャー誌に対応)からまとめて抽出する。"""
    if isinstance(pdf_paths, (str, Path)):
        pdf_paths = [pdf_paths]
    pages = []
    for p in pdf_paths:
        pages.extend(load_pages(p))
    return extract_all_from_pages(pages)


def resolve_sources(pdfs, urls, keep_raw_dir):
    """--pdf/--urlの指定(複数可)をローカルパスのリストに解決する。"""
    paths = [Path(p) for p in (pdfs or [])]
    tmp_paths = []
    for i, url in enumerate(urls or []):
        if keep_raw_dir:
            dest = Path(keep_raw_dir) / f"source_{i}.pdf"
            dest.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest = Path(f"_tmp_disclosure_{i}.pdf")
            tmp_paths.append(dest)
        print(f"Downloading {url} ...", file=sys.stderr)
        download(url, dest)
        paths.append(dest)
    return paths, tmp_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", action="append", help="ローカルのPDFファイルパス(複数指定可、分割PDFはページ順に連結される)")
    parser.add_argument("--url", action="append", help="PDFのURL(複数指定可)")
    parser.add_argument("--out", required=True, help="出力JSONファイルパス")
    parser.add_argument("--keep-raw", help="ダウンロードしたPDFを保存するディレクトリ")
    args = parser.parse_args()

    if not args.pdf and not args.url:
        parser.error("either --pdf or --url is required")
        return

    paths, tmp_paths = resolve_sources(args.pdf, args.url, args.keep_raw)
    result = extract_all(paths)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Wrote {out_path}", file=sys.stderr)

    for p in tmp_paths:
        p.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
