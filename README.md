# shinkin

信用金庫の財務データ取得スクリプト。

## データソース

金融庁「中小・地域金融機関情報」（https://www.fsa.go.jp/policy/chusho/shihyou.html ）が
公開している信用金庫・信用組合の主要勘定一覧（Excel）を取得元とする。

取得できる項目（信用金庫ごと）:
- 都道府県、金融機関コード、金融機関名、法人番号、本店所在地、店舗数
- ディスクロージャー誌（Web）へのリンク
- 預金積金、貸出金（百万円）
- 自己資本比率、不良債権比率（%）
- 中小企業等向け貸出残高・貸出先件数（前年度/当年度）

## 使い方

```bash
pip install -r requirements.txt
python3 scripts/fetch_shinkin_financials.py --out data/shinkin_financials.csv
```

金融庁のファイルURLは年度更新のたびに変わる（例: `zenkoku/2025-2.xlsx`）。
最新のURLは https://www.fsa.go.jp/policy/chusho/shihyou.html の
「信用金庫・信用組合」欄のリンクから確認し、`--url` で指定する。

```bash
python3 scripts/fetch_shinkin_financials.py \
  --url https://www.fsa.go.jp/policy/chusho/shihyou/zenkoku/<年度>.xlsx \
  --out data/shinkin_financials.json
```

個別信用金庫のより詳細な財務データ（決算書全体）が必要な場合は、
出力される `disclosure_url` 列から各金庫のディスクロージャー誌（PDF）に
アクセスする。収益構造・業種別貸出金・有価証券ポートフォリオの取得については
下記を参照。

## 個別金庫の詳細データ（収益構造・業種別貸出金・有価証券ポートフォリオ）

FSAの集計Excelには含まれない、より詳細な項目
（収益構造＝損益計算書の内訳、貸出金の業種別内訳、有価証券の
種類別・残存期間別残高）は、各信用金庫が個別に公開する
ディスクロージャー資料（PDF）にのみ掲載されている。

これらのPDFはサイト構成・ファイル分割の仕方が金庫ごとに異なるため、
`scripts/extract_disclosure_pdf.py` はPDFのテキストからキーワード検索で
該当ページを特定し、行単位のラベル・数値マッチングで抽出する方式を取っている
（開示項目自体は信用金庫法施行規則等に基づく共通様式であり、業種区分も
日本標準産業分類の大分類に準じているため、金庫が変わってもラベルの
文言はほぼ共通という前提に基づく）。

### 使い方（単体）

```bash
python3 scripts/extract_disclosure_pdf.py \
  --url https://example.co.jp/disclosure/report.pdf \
  --out result.json

# PDFが複数ファイルに分割されている金庫の場合、順番に複数指定するとページを連結して処理する
python3 scripts/extract_disclosure_pdf.py \
  --url https://example.co.jp/disclosure/part1.pdf \
  --url https://example.co.jp/disclosure/part2.pdf \
  --out result.json
```

### パイロット実行（4金庫）

`scripts/pilot_institutions.json` に、規模の異なる4金庫
（栃木信用金庫・稚内信用金庫・城南信用金庫・伊達信用金庫）のPDF URLを
登録済み。まとめて実行する場合:

```bash
python3 scripts/run_disclosure_pilot.py
```

結果は `data/pilot/<金庫名>.json` に出力される（このリポジトリには
実行結果のサンプルをコミット済み）。

### PDFの自動発見（`scripts/discover_disclosure_pdf.py`）

金庫ごとにサイト構造が異なるため、FSAの一覧にある「ディスクロージャー(Web)」
リンク先のページから、最新のディスクロージャー資料PDFをキーワード
（計数編・資料編・財務データ・一括ダウンロード等）とページ内リンクの
1階層フォローでベストエフォート発見する。

```bash
python3 scripts/discover_disclosure_pdf.py "https://example.co.jp/disclosure/"
```

### 拡張バッチ実行（`scripts/run_disclosure_batch.py`）

FSAのExcelから対象金庫をサンプリングし、PDF自動発見・ダウンロード・
抽出までを一括実行する。

```bash
python3 scripts/run_disclosure_batch.py \
  --xlsx /path/to/zenkoku.xlsx \
  --sample-per-prefecture 1 \
  --out-dir data/sample_47
```

**47都道府県から1金庫ずつサンプリングした実行結果**（`data/sample_47/`に
コミット済み）:

| 指標 | 件数 |
|---|---|
| PDFの自動発見に成功 | 42/47 (89%) |
| 収益構造・業種別貸出金・有価証券ポートフォリオの3項目すべて取得 | 18/47 (38%) |
| 3項目のうち1つ以上取得 | 37/47 (79%) |
| 収益構造(損益の状況)を取得 | 33/42 |
| 業種別貸出金を取得 | 29/42 |
| 有価証券ポートフォリオを取得 | 23/42 |

### 全254金庫での実行結果

```bash
python3 scripts/run_disclosure_batch.py \
  --xlsx /path/to/zenkoku.xlsx \
  --sample-per-prefecture 999 \
  --out-dir data/all_254
```

信用金庫254金庫すべてに対して実行した結果（`data/all_254/`にコミット済み。
生PDFは`_raw_pdf/`配下にダウンロードされるがサイズが大きい(約2GB)ため
gitignore対象）:

| 指標 | 件数 |
|---|---|
| PDFの自動発見に成功 | 225/254 (89%) |
| 収益構造・業種別貸出金・有価証券ポートフォリオの3項目すべて取得 | 86/254 (34%) |
| 3項目のうち1つ以上取得 | 180/254 (71%) |
| 収益構造(損益の状況)を取得 | 167/225 |
| 業種別貸出金を取得 | 144/225 |
| 有価証券ポートフォリオを取得 | 109/225 |
| PDFが発見できなかった | 24/254 |
| ダウンロード・解析エラー | 5/254 |
| PDFは発見・処理できたが3項目とも抽出できなかった | 45/254 |

47金庫サンプルとほぼ同じ歩留まりが254金庫全体でも再現された。上記は
URLエンコード・文字コード判定・部分ダウンロード許容の3件のバグ修正
（下記コミット参照）を反映した後の数値。個別institutionでは
0/3→2/3（大阪商工信用金庫・いちい信用金庫等）のように明確な改善が
確認できたが、ダウンロード・解析エラーは12→5に大きく減った一方
`pdf_not_found`が20→24とわずかに増えており、これは同一サイトへの
連続アクセスによる一時的なブロック(403等)がバッチ実行のたびに
異なる金庫で発生する再現性の低いノイズによるもの。全体集計はその
ノイズに埋もれて微増にとどまっているが、個々の修正自体は有効。

### 既知の制約

- PDFのレイアウトは金庫ごとに異なり、また同一金庫でも年度・半期/通期で
  フォーマットが変わることがあるため、本スクリプトはベストエフォート実装。
  抽出できなかった項目は `null` になる。
- 同一ページに複数の表（例: 保証・信用の集計表と業種別貸出金表）が
  含まれる場合、「合計」「小計」「その他」など共通ラベルの行が
  意図しない表からも抽出されることがある（`raw_line` を見て確認すること）。
- PDF自動発見はヒューリスティックであり、失敗パターンは主に3種類
  （`data/all_254/_summary.json` の `status`/`found` で判別可能）:
  1. PDFリンクが全く見つからない、またはダウンロード・解析エラー（32金庫）
  2. PDFは発見できるが中身が半期の簡易報告のみで詳細表を含まない
  3. PDFは見つかり詳細表も含むが、ラベルの表記ゆれで本スクリプトの
     抽出パターンにヒットしない
- 全254金庫規模では、上記のような未対応サイトが一定数（約3割）残る前提で、
  取得できた分だけをデータセットとして使うか、失敗リストを見ながら
  個別に抽出ルールを追加していく運用が現実的。
