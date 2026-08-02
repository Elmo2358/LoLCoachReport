# LoL コーチングレポート（Gemini 分析・コーチング用 CLI）

League of Legends の**試合ID（または自分の Riot ID）を渡すと、全参加者の統計を Gemini にそのまま貼れる Markdown レポート**に整形する Python CLI です。レポートを Gemini に貼るだけで、レーニング・マクロ・チームファイト・ビジョン・ビルドの観点からコーチング回答が得られます。

任意のプレイヤーを「分析対象」として強調でき、対面との一対比較（CS / ゴールド / DMG / KDA / 視界 / デス）も出力します。

参考: [Zenn / moudousiyou の記事](https://zenn.dev/moudousiyou/articles/763a19f923eb46)（RiotWatcher で試合データ収集）の知見を、単一試合のコーチング用途に最適化しました。

## できること
- 自分の直近試合を自動取得（`.env` に Riot ID を設定しておけば引数なしで実行可能）
- 試合ID指定も可能（分析対象プレイヤーが含まれていれば自動で強調）
- **👑 分析対象プレイヤーのプレイ** セクションで全統計を最強調 ＋ **🆚 対面との比較表**（同じロールの敵との CS/ゴールド/DMG/KDA/視界/デス の一対比較）
- Data Dragon で**チャンピオン / アイテム / サモナースペル / ルーンを日本語名**に解決
- `--coach` で**コーチング指示プロンプト**を先頭に付与 → Gemini に貼るだけでコーチング回答

## 必要環境
- Python 3.10+
- Riot Games の Personal API Key（[Riot Developer Portal](https://developer.riotgames.com/) で発行、**24時間で期限切れ**）

## セットアップ
```bash
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```
`.env` を開き、以下を設定します:
- `RIOT_API_KEY=` にポータルで発行した自分の API キー
- `MY_GAME_NAME=` / `MY_TAG_LINE=` に自分の Riot ID（任意。設定すると引数なしで自分の直近試合を自動取得）

## 使い方
```bash
# .env に自分の Riot ID を設定済みなら、引数なしで自分の直近試合を自動取得
python main.py

# コーチングプロンプト付きでクリップボードへ（Geminiに貼るだけ）
python main.py --coach --clip

# 試合ID指定（分析対象プレイヤーが含まれていれば自動で強調）
python main.py JP1_595633237

# 分析対象プレイヤーを明示指定（チャンプ名/サモナー名の部分一致）
python main.py JP1_595633237 --player Fiora --coach

# 英語名で出力
python main.py --lang en

# APIキー不要のオフライン検証（同梱サンプル試合を使用）
python main.py --demo
```

## GUI / exe 版（ダブルクリックで起動）

CLI を使わずに **ダブルクリックで起動するデスクトップアプリ** も使えます。試合IDを入力 → ボタン1つで結果を保存。

### exe をビルド（初回のみ）

```bat
build.bat
```

`dist\LoLCoachReport.exe` と `dist\.env` が生成されます。`dist\.env` に自分の Riot ID（`MY_GAME_NAME` / `MY_TAG_LINE`）を書いておくと、GUI の「自動取得」と強調表示が有効になります。

### 操作手順

1. `dist\LoLCoachReport.exe` をダブルクリック
2. **APIキー** 欄に [Riot Developer Portal](https://developer.riotgames.com/) で発行したキーを貼る（期限切れ時はここを更新）
3. **試合ID** を入力（空欄＋「自動取得」チェックで自分の直近試合を取得）
4. **出力形式** を選択（複数選択可）:
   - **クリップボードにコピー** … Markdown レポートをコピー（Gemini にそのまま貼る）
   - **Markdown(.md)** / **JSON(.json)** / **CSV(.csv)** … 保存先ダイアログで保存
5. **取得して出力** をクリック → 結果がプレビュー表示され、選択形式で保存

> APIキーは GUI 内に入力すれば保持されます（`%LOCALAPPDATA%\lolalytics\settings.json`）。`.env` は自分の Riot ID 用。
> 初回起動で Windows にブロックされた場合は「詳細情報」→「実行」で通してください（未署名のため）。

### 個人設定（.env）
| 変数 | 説明 |
|---|---|
| `RIOT_API_KEY` | Riot API キー（**必須**） |
| `MY_GAME_NAME` | 自分の Riot ID の gameName（例: `elmo2358`） |
| `MY_TAG_LINE` | 自分の Riot ID の tagLine（例: `2358`） |
| `MY_REGION` | 大陸ルーティング（JP/KR=`asia`, NA/BR/LA/OC=`americas`, EU/TR/RU=`europe`） |
| `MY_SERVER` | 表示用サーバーラベル（`JP`/`NA`/`EUW`/`KR` など） |

`MY_GAME_NAME` / `MY_TAG_LINE` を設定すると、引数なし実行で自分の直近試合を自動取得し、「あなたのプレイ」を強調します。未設定の場合は `match_id` か `--player` で明示的に指定してください。

### オプション
| オプション | 説明 |
|---|---|
| `match_id` | 試合ID（省略時は `.env` の自分の直近試合を自動取得。未設定なら `--player` や match_id を指定） |
| `--player` | 分析対象プレイヤー（既定は `.env` の自分。チャンプ名/サモナー名の部分一致で上書き） |
| `--coach` | 先頭にコーチング指示プロンプトを付与 |
| `--lang` | 名称ロケール `ja`(既定) / `en` |
| `--demo` | APIを使わず同梱サンプルで出力 |
| `--clip` | レポートをクリップボードへコピー |

## 出力
- `data/reports/{試合ID}.md` … Gemini 貼り付け用レポート（標準出力にも表示）
- `data/matches/{試合ID}.json` … 生のAPI応答（実API実行時のみ）
- `data/ddragon_cache/` … Data Dragon のキャッシュ（2回目以降の高速化）

> いずれも `.gitignore` 対象のため公開リポジトリには含まれません。

## リージョン自動判定
試合ID のプレフィックスから match-v5 の大陸別ルーティングを自動選択します（入力時にリージョン指定は不要）。
`KR/JP1→asia` / `NA1/BR1/LA1/LA2/OC1→americas` / `EUW1/EUN1/TR1/RU→europe` / `SEA→sea`

## Gemini への渡し方
1. `python main.py JP1_xxx --player チャンプ名 --coach --clip` を実行
2. Gemini のチャット欄にそのまま貼り付け（Ctrl+V）
3. レーニング・マクロ・チームファイト・ビジョン・ビルドの観点からコーチング回答が返ってきます

## トラブルシューティング
| エラー | 原因と対応 |
|---|---|
| HTTP 401 / 403 | APIキーが無効または**期限切れ**。ポータルで再生成し `.env` を更新 |
| HTTP 404 | 試合IDの間違い・リージョン違い・対応外モード |
| HTTP 429 | レート制限。数秒待って再実行（1試合1リクエストなので通常は発生しません） |
| Data Dragon 取得失敗 | 警告が出ますがID表示で処理は継続します |

> Personal API Key は「20リクエスト/秒・100リクエスト/2分」。本ツールは1試合につき Riot API 1〜2リクエスト＋Data Dragon 数リクエスト（キャッシュ済みなら0）なので、制限にかかることはありません。

## ライセンス
[MIT License](LICENSE) のもとで公開します。

## 構成
```
main.py         CLI エントリ（取得→名前解決→統計→Markdown→保存/コピー）
gui.py          GUI（tkinter）。PyInstaller で exe 化するエントリ
core.py         CLI/GUI 共通の取得・処理ロジック
paths.py        exe/開発のデータ保存先を解決（%LOCALAPPDATA% / ./data）
csv_export.py   統計中間表現 → CSV
riot_client.py  RiotWatcher ラッパ（リージョン自動判定・エラー処理）
ddragon.py      Data Dragon による ID→名称解決（キャッシュ・日本語化）
stats.py        match-v5 応答 → 統計中間表現（分間値・challenges 等）
report.py       Gemini 向け Markdown 生成（コーチプロンプト・対面比較含む）
config.py        .env 経由のユーザー設定（自分の Riot ID・リージョン）
build.bat       PyInstaller で exe をビルド
```
