"""elmo2358 個人用 LoL コーチングレポート CLI。

GUI 版は gui.py（PyInstaller で exe 化を想定）。CLI はこちら。

使用例:
  python main.py                 # 自分の直近試合を自動取得 → あなた中心にレポート
  python main.py --coach --clip  # コーチングプロンプト付きでクリップボードへ
  python main.py JP1_595633237   # 試合ID指定（あなたが含まれていれば自動で強調）
  python main.py --player Akari  # 自分以外を一時的に分析対象にしたい場合
  python main.py --demo          # API不要のオフライン検証（同梱サンプル）
"""
import argparse
import json
import os
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

import paths
import core
import csv_export
from riot_client import RiotClient


def _save(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _to_clipboard(text):
    try:
        import pyperclip
        pyperclip.copy(text)
        print("[クリップボードにコピーしました]")
    except ImportError:
        print("[ヒント] クリップボードコピーには `pip install pyperclip` が必要です。")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="LoL 試合IDから Gemini 分析・コーチング用レポート(Markdown)を生成します。")
    ap.add_argument("match_id", nargs="?", help="試合ID（省略時は自分 elmo2358 の直近試合を自動取得）")
    ap.add_argument("--player", help="分析対象プレイヤー（既定は自分。チャンプ名/サモナー名の部分一致で上書き）")
    ap.add_argument("--coach", action="store_true", help="先頭にコーチング指示プロンプトを付与")
    ap.add_argument("--lang", choices=["ja", "en"], default="ja", help="名称のロケール（既定: ja）")
    ap.add_argument("--demo", action="store_true", help="APIを使わず同梱サンプルで出力（オフライン検証用）")
    ap.add_argument("--clip", action="store_true", help="レポートをクリップボードへコピー")
    ap.add_argument("--csv", action="store_true", help="参加者統計をCSVで data/reports/ に保存")
    args = ap.parse_args()

    if load_dotenv is not None:
        load_dotenv()

    try:
        if args.demo:
            sp = paths.sample_path()
            if not os.path.exists(sp):
                print(f"エラー: サンプルが見つかりません: {sp}")
                sys.exit(1)
            with open(sp, encoding="utf-8") as f:
                match = json.load(f)
            match_id = match["metadata"]["matchId"]
            _, server = RiotClient.parse_region(match_id)
            result = core.process_match_data(match, server, lang=args.lang,
                                             coach=args.coach, player=args.player)
        else:
            api_key = os.getenv("RIOT_API_KEY")
            result = core.process(api_key, match_id=args.match_id, auto_me=True,
                                  lang=args.lang, coach=args.coach, player=args.player)
    except core.ProcessError as e:
        print(str(e))
        sys.exit(1)

    md = result["markdown"]
    mid = result["match_id"] or "match"

    # 保存（Markdown / 生JSON / CSV）
    md_path = os.path.join(paths.reports_dir(), f"{mid}.md")
    _save(md_path, md)
    print(f"[保存] レポート(Markdown): {md_path}")
    if not args.demo:
        raw_path = os.path.join(paths.matches_dir(), f"{mid}.json")
        _save(raw_path, json.dumps(result["raw"], ensure_ascii=False, indent=2))
        print(f"[保存] 生データ(JSON): {raw_path}")
    if args.csv:
        csv_path = os.path.join(paths.reports_dir(), f"{mid}.csv")
        # Excel で日本語が化けないよう UTF-8 BOM
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(csv_export.to_csv(result["ir"]))
        print(f"[保存] 統計(CSV): {csv_path}")

    print("\n" + "=" * 70)
    print(md)
    print("=" * 70 + "\n")

    if args.clip:
        _to_clipboard(md)


if __name__ == "__main__":
    main()
