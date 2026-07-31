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
アクセスする。
