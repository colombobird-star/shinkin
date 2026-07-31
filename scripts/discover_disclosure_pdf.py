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
import time
import urllib.error
import urllib.request
from html import unescape
from urllib.parse import quote, urljoin, urlparse, urlsplit, urlunsplit

HIGH_KEYWORDS = [
    "計数編", "計数資料編", "keisu", "資料編", "業務のご報告", "gyomu", "業績報告",
    "財務データ", "損益の状況", "業種別",
]
# "開示項目"は「開示項目一覧」という目次ページにもマッチしてしまうため、
# 単体のHIGH_KEYWORDSからは除外し、実データを指すことが多い組み合わせのみ拾う。
HIGH_KEYWORDS_PHRASES = ["開示項目（財務", "開示項目(財務"]
BUNDLE_KEYWORDS = ["一括ダウンロード", "一括", "全ページ", "全頁", "_all"]
INDEX_PAGE_KEYWORDS = ["一覧", "目次", "index"]
MID_KEYWORDS = ["ディスクロージャー", "disclo", "report"]
EXCLUDE_KEYWORDS = [
    "個人情報", "プライバシー", "規程", "約款", "定款", "採用", "sdgs", "csr", "iban",
    "正誤表", "訂正", "お詫び", "景況", "マーケットレポート",
]

LINK_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
YEAR_RE = re.compile(r"20[12]\d")
# 一部のサイトは西暦ではなく「令和年+月」を4桁でコード化したパス
# (例: 2603 = 令和8年3月、2509 = 令和7年9月)を使うため、西暦が
# 見つからない場合のみ弱いフォールバックの新しさ指標として使う。
PERIOD_CODE_RE = re.compile(r"\b(2[3-6]\d{2})\b")

RETRYABLE_HTTP_CODES = {403, 429, 500, 502, 503, 504}


def safe_url(url):
    """URLのpath/queryに未エンコードの非ASCII文字(日本語ファイル名等)が
    含まれている場合、urllibがリクエストライン組み立て時にasciiエンコードで
    失敗するため、パーセントエンコードして安全な形に変換する。"""
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%")
    query = quote(parts.query, safe="=&%")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


META_CHARSET_RE = re.compile(rb'charset=["\']?\s*([\w-]+)', re.IGNORECASE)


def decode_html(raw: bytes, header_charset: str | None) -> str:
    """HTTPヘッダにcharsetが無い、または実際の文字コードと食い違う
    ページ(特に古いshift_jis系の金庫サイト)が多いため、
    HTML内のmeta charset宣言も見て複数候補をフォールバックする。"""
    candidates = []
    if header_charset:
        candidates.append(header_charset)
    m = META_CHARSET_RE.search(raw[:2048])
    if m:
        candidates.append(m.group(1).decode("ascii", errors="ignore"))
    candidates += ["utf-8", "cp932", "euc-jp"]

    seen = set()
    for enc in candidates:
        enc_norm = enc.lower().replace("shift-jis", "shift_jis").replace("sjis", "shift_jis").replace("x-sjis", "shift_jis")
        if enc_norm in seen:
            continue
        seen.add(enc_norm)
        try:
            return raw.decode(enc_norm)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch(url, timeout=20, retries=3):
    last_err = None
    for attempt in range(retries):
        if attempt:
            time.sleep(2 * attempt)
        req = urllib.request.Request(safe_url(url), headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return decode_html(raw, resp.headers.get_content_charset())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in RETRYABLE_HTTP_CODES:
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
    raise last_err


def extract_links(html, base_url):
    links = []
    for m in LINK_RE.finditer(html):
        href, text = m.group(1), m.group(2)
        text = unescape(TAG_RE.sub("", text)).strip()
        abs_url = urljoin(base_url, href)
        links.append((abs_url, text))
    return links


# 「P13〜23」「P22-42」のようなページ範囲表記は、ディスクロージャー誌を
# 分割した章立てファイルによく見られるパターン。
PAGE_RANGE_RE = re.compile(r"\bp\.?\s*\d+\s*[~\-―～]\s*\d+", re.IGNORECASE)


def score_link(url, text):
    hay = f"{url} {text}".lower()
    if any(k.lower() in hay for k in EXCLUDE_KEYWORDS):
        return -100
    score = 0
    if any(k.lower() in hay for k in HIGH_KEYWORDS) or any(p.lower() in hay for p in HIGH_KEYWORDS_PHRASES):
        score += 5
    if any(k.lower() in hay for k in MID_KEYWORDS) or PAGE_RANGE_RE.search(hay):
        score += 2
    # 「開示項目一覧」等の目次ページはHIGH_KEYWORDSの単純一致では弾けないので減点する。
    if any(k.lower() in hay for k in INDEX_PAGE_KEYWORDS):
        score -= 4
    is_bundle_link = any(k.lower() in hay for k in BUNDLE_KEYWORDS) or hay.endswith("all.pdf")
    # URLのクエリ文字列(キャッシュバスター等)やファイル名中の日付は
    # 無関係な文書(規程・お知らせ等)にも付いていることが多く、それだけで
    # ディスクロージャー資料と誤認しないよう、キーワード一致がある場合か
    # バンドルファイルの場合に限って新しさボーナスを加点する。
    if score > 0 or is_bundle_link:
        years = [int(y) for y in YEAR_RE.findall(hay)]
        if years:
            score += (max(years) - 2020)  # 新しい年度ほど加点
        else:
            periods = [int(p) for p in PERIOD_CODE_RE.findall(hay)]
            if periods:
                score += (max(periods) - 2300) * 0.01  # 弱いフォールバックの新しさ指標
    if is_bundle_link:
        score += 1
    return score


def is_bundle(url, text):
    hay = f"{url} {text}".lower()
    return any(k.lower() in hay for k in BUNDLE_KEYWORDS) or hay.endswith("all.pdf")


def pick_pdf_candidates(links, max_candidates=6):
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
    tied = [s for s in scored if s["score"] == top_score]

    # 同点内に「一括ダウンロード」等のバンドルファイルがあれば、
    # 個別章立てPDF(小分けファイル)より優先する。
    bundles = [s for s in tied if is_bundle(s["url"], s["text"])]
    if not bundles:
        # トップと僅差(年度コードが数値年と認識できず加点されない等)で
        # バンドルファイルが埋もれている場合を救済する。同時に複数期間の
        # バンドルが見つかった場合は、その中で最もスコアの高い(=最新の)
        # ものだけに絞る。
        near_top_bundles = [
            s for s in scored
            if is_bundle(s["url"], s["text"]) and s["score"] >= top_score - 6
        ]
        if near_top_bundles:
            bundle_top = max(s["score"] for s in near_top_bundles)
            bundles = [s for s in near_top_bundles if s["score"] == bundle_top]
    best = bundles if bundles else tied

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


# この閾値以上のスコアは「資料編」「財務データ」等の明確なキーワード一致を
# 意味し、それだけで探索を打ち切ってよいとみなす。閾値未満(バンドル一致の
# みなど)の弱い候補だけで即座に確定すると、記念誌PDFのような無関係な
# バンドルファイルに飛びついて、本来もっと良いページ(サブページにリンク
# された本当のディスクロージャー誌)を見逃すことがある(大分みらい信用金庫
# で発生)。弱い候補は保持しつつ、探索は続行する。
STRONG_MATCH_THRESHOLD = 5


def discover(disclosure_url, max_depth=2):
    """returns (list of candidate pdf urls, debug info dict)"""
    debug = {"visited": []}
    visited_pages = set()
    to_visit = [disclosure_url]
    base_domain = urlparse(disclosure_url).netloc
    best_candidates = []
    best_score = -1

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
            page_top_score = max((c["score"] for c in good_candidates), default=-1)
            debug["visited"].append({
                "url": page_url,
                "pdf_candidates_found": len(candidates),
                "good_candidates_found": len(good_candidates),
            })
            if page_top_score > best_score:
                best_score = page_top_score
                best_candidates = good_candidates
            if best_score >= STRONG_MATCH_THRESHOLD:
                return best_candidates, debug
            next_round.extend(find_followup_pages(links, base_domain))
        to_visit = next_round
        if not to_visit:
            break
    return best_candidates, debug


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
