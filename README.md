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

### 既知の制約

- PDFのレイアウトは金庫ごとに異なり、また同一金庫でも年度・半期/通期で
  フォーマットが変わることがあるため、本スクリプトはベストエフォート実装。
  抽出できなかった項目は `null` になる（例: 栃木信用金庫は半期報告のPDFが
  「有価証券の時価情報」形式で、`残存期間別残高`形式ではないため
  `securities_portfolio` が取得できていない）。
- 同一ページに複数の表（例: 保証・信用の集計表と業種別貸出金表）が
  含まれる場合、「合計」「小計」「その他」など共通ラベルの行が
  意図しない表からも抽出されることがある（`raw_line` を見て確認すること）。
- 全254金庫に展開する場合は、金庫ごとにディスクロージャーPDFのURLを
  特定する工程（サイト構造が金庫ごとに異なる）が別途必要。
