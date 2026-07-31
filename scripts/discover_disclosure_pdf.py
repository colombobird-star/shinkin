#!/usr/bin/env python3
"""
金融庁の一覧が指すディスクロージャー(Web)ページから、
最新のディスクロージャー資料PDFのURLをベストエフォートで自動発見する。

各金庫のサイト構造は統一されていないため、以下のヒューリスティックで
候補を探す:
  1. ページ上のPDFリンクを収集し、リンクテキスト/ファイル名に含まれる
     キーワード(計数編・資料編・業務のご報告・ディスクロージャー等)と
     年度表記からスコアリングする。
  2. PDFリンクがほとんど見つからない場合、「資料編」「計数」等の
     キーワードを含むページ内リンクを1階層だけ辿って再探索する
     (稚内信用金庫のような、詳細ページに実PDFがある構成に対応)。
  3. 分割PDF(例: "xx_all.pdf" と "xx-2_all.pdf")は両方候補として返す。

100%の精度は保証できない。取得できなかった場合は候補なしとして
呼び出し側でスキップ・手動確認する前提。
"""
import argparse
import json
import re
import sys
import urllib.request
from html import unescape
from urllib.parse import urljoin, urlparse

HIGH_KEYWORDS = ["計数編", "計数資料編", "keisu", "資料編", "業務のご報告", "gyomu", "業績報告"]
MID_KEYWORDS = ["ディスクロージャー", "disclo", "report"]
EXCLUDE_KEYWORDS = ["個人情報", "プライバシー", "規程", "約款", "定款", "採用", "sdgs", "csr", "iban"]

LINK_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
YEAR_RE = re.compile(r"20[12]\d")


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def extract_links(html, base_url):
    links = []
    for m in LINK_RE.finditer(html):
        href, text = m.group(1), m.group(2)
        text = unescape(TAG_RE.sub("", text)).strip()
        abs_url = urljoin(base_url, href)
        links.append((abs_url, text))
    return links


def score_link(url, text):
    hay = f"{url} {text}".lower()
    if any(k.lower() in hay for k in EXCLUDE_KEYWORDS):
        return -100
    score = 0
    if any(k.lower() in hay for k in HIGH_KEYWORDS):
        score += 5
    if any(k.lower() in hay for k in MID_KEYWORDS):
        score += 2
    years = [int(y) for y in YEAR_RE.findall(hay)]
    if years:
        score += (max(years) - 2020)  # 新しい年度ほど加点
    if "_all" in hay or hay.endswith("all.pdf"):
        score += 1
    return score


def pick_pdf_candidates(links, max_candidates=3):
    pdf_links = [(u, t) for u, t in links if u.lower().split("?")[0].endswith(".pdf")]
    if not pdf_links:
        return []
    scored = sorted(
        ({"url": u, "text": t, "score": score_link(u, t)} for u, t in pdf_links),
        key=lambda x: x["score"],
        reverse=True,
    )
    scored = [s for s in scored if s["score"] > -100]
    if not scored:
        return []
    top_score = scored[0]["score"]
    best = [s for s in scored if s["score"] == top_score]
    # 重複URL除去、順序維持
    seen = set()
    result = []
    for s in best:
        if s["url"] not in seen:
            seen.add(s["url"])
            result.append(s)
        if len(result) >= max_candidates:
            break
    return result


FOLLOWUP_KEYWORDS = HIGH_KEYWORDS + MID_KEYWORDS + [
    "経営内容", "情報開示", "財務", "業績", "決算", "info",
]


def find_followup_pages(links, base_domain, max_links=5):
    scored = []
    for u, t in links:
        if urlparse(u).netloc != base_domain:
            continue
        if u.lower().split("?")[0].endswith((".pdf", ".jpg", ".png", ".css", ".js")):
            continue
        hay = f"{u} {t}".lower()
        if any(k in EXCLUDE_KEYWORDS for k in hay.split()):
            continue
        kw_hit = any(k.lower() in hay for k in FOLLOWUP_KEYWORDS)
        year_hit = bool(YEAR_RE.search(hay))
        if kw_hit or year_hit:
            years = [int(y) for y in YEAR_RE.findall(hay)]
            s = (2 if kw_hit else 0) + (max(years) - 2020 if years else 0)
            scored.append((s, u))
    scored.sort(key=lambda x: x[0], reverse=True)
    seen = set()
    out = []
    for _, u in scored:
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= max_links:
            break
    return out


def discover(disclosure_url, max_depth=2):
    """returns (list of candidate pdf urls, debug info dict)"""
    debug = {"visited": []}
    visited_pages = set()
    to_visit = [disclosure_url]
    base_domain = urlparse(disclosure_url).netloc
    fallback_candidates = []

    for depth in range(max_depth):
        next_round = []
        for page_url in to_visit:
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)
            try:
                html = fetch(page_url)
            except Exception as e:  # noqa: BLE001
                debug["visited"].append({"url": page_url, "error": str(e)})
                continue
            links = extract_links(html, page_url)
            candidates = pick_pdf_candidates(links)
            good_candidates = [c for c in candidates if c["score"] > 0]
            debug["visited"].append({
                "url": page_url,
                "pdf_candidates_found": len(candidates),
                "good_candidates_found": len(good_candidates),
            })
            if good_candidates:
                return good_candidates, debug
            if candidates and not fallback_candidates:
                fallback_candidates = candidates
            next_round.extend(find_followup_pages(links, base_domain))
        to_visit = next_round
        if not to_visit:
            break
    return fallback_candidates, debug


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="金融機関のディスクロージャー(Web)ページURL")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    candidates, debug = discover(args.url)
    if args.debug:
        print(json.dumps(debug, ensure_ascii=False, indent=2), file=sys.stderr)
    print(json.dumps(candidates, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
