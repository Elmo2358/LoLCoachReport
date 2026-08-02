"""ユーザー設定（.env または環境変数で指定）。

自分の Riot ID を設定すると、CLI の引数なし実行や GUI の自動取得で自分の直近試合を
取得し、「あなたのプレイ」を強調できる。未設定時は match_id か --player の明示指定が必要。

core.py / gui.py がモジュール定数 (MY_GAME_NAME など) を参照するため、
インポート時に .env を読み込み、定数として公開する（未設定なら空文字列）。
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _env(key, default=""):
    return os.getenv(key, default).strip()


# --- モジュール定数（core.py / gui.py が参照） ---
MY_GAME_NAME = _env("MY_GAME_NAME")
MY_TAG_LINE = _env("MY_TAG_LINE")
MY_FULL_ID = f"{MY_GAME_NAME}#{MY_TAG_LINE}" if (MY_GAME_NAME and MY_TAG_LINE) else ""
MY_REGION = _env("MY_REGION") or "asia"
MY_SERVER = _env("MY_SERVER") or "JP"


def get_my_account():
    """自分のアカウント情報を返す。戻り値: (game_name, tag_line, full_id, region, server)。"""
    return MY_GAME_NAME, MY_TAG_LINE, MY_FULL_ID, MY_REGION, MY_SERVER
