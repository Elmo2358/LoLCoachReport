"""Riot match-v5 / account-v1 API クライアント。

記事では RiotWatcher を使っていたが、RiotWatcher の HTTP 呼び出しはタイムアウトを
持たずネットワーク次第で無限待ちになるため、タイムアウトを明示できる requests の
直呼びにしている（ハング防止）。
"""
import os
from urllib.parse import quote

import requests

# match ID プレフィックス -> match-v5 の大陸別ルーティング値
REGION_MAP = {
    "KR": "asia", "JP1": "asia",
    "NA1": "americas", "BR1": "americas", "LA1": "americas",
    "LA2": "americas", "OC1": "americas",
    "EUW1": "europe", "EUN1": "europe", "TR1": "europe", "RU": "europe",
    "SEA": "sea",
}
# 表示用サーバーラベル
SERVER_LABEL = {
    "KR": "KR", "JP1": "JP", "NA1": "NA", "BR1": "BR",
    "LA1": "LA1", "LA2": "LA2", "OC1": "OC", "EUW1": "EUW",
    "EUN1": "EUNE", "TR1": "TR", "RU": "RU", "SEA": "SEA",
}
# 表示サーバー -> match ID のプラットフォームプレフィックス（数値のみIDの自動補完用）
SERVER_TO_PLATFORM = {
    "JP": "JP1", "KR": "KR", "NA": "NA1", "BR": "BR1", "EUW": "EUW1",
    "EUNE": "EUN1", "TR": "TR1", "RU": "RU", "OC": "OC1",
    "LA": "LA1", "LA1": "LA1", "LA2": "LA2", "SEA": "SEA",
}

# (接続, 読み取り) 秒。これを超えるとタイムアウトで確実にエラーを返す。
TIMEOUT = (10, 30)
RIOT_BASE = "https://{region}.api.riotgames.com"


class RiotApiError(Exception):
    """Riot API 呼び出し失敗。ステータス（数値 or 文字列）に応じた日本語メッセージ。"""

    _MESSAGES = {
        401: ("APIキーが無効または期限切れの可能性があります（HTTP 401）。"
              "Riot Developer Portal (https://developer.riotgames.com/) でキーを再生成し、"
              "GUI の APIキー欄（または .env）を更新してください。"),
        403: ("APIキーが無効または期限切れです（HTTP 403）。"
              "Riot Developer Portal (https://developer.riotgames.com/) でキーを再生成してください。"),
        404: ("試合が見つかりません（HTTP 404）。試合IDの間違い、リージョン違い、"
              "または対応していないゲームモードの可能性があります。"),
        429: "APIレート制限に達しました（HTTP 429）。数十秒待ってから再実行してください。",
        "TIMEOUT": "API呼び出しがタイムアウトしました（ネットワークが遅い/不通）。接続を確認して再試行してください。",
        "NETWORK": "APIへの接続に失敗しました。インターネット接続を確認してください。",
    }

    def __init__(self, status_code, match_id="", detail=""):
        self.status_code = status_code
        msg = self._MESSAGES.get(
            status_code,
            f"Riot APIエラーが発生しました（HTTP {status_code}）。",
        )
        full = f"[{match_id}] {msg}" if match_id else msg
        if detail:
            full += f"\n  詳細: {detail}"
        super().__init__(full)


class RiotClient:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("RIOT_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "RIOT_API_KEY が設定されていません。GUI の APIキー欄に入力するか .env で設定してください。"
            )
        self.session = requests.Session()
        self.session.headers.update({"X-Riot-Token": self.api_key})

    @staticmethod
    def parse_region(match_id):
        """match ID (例 'JP1_595633237') -> (大陸ルーティング, サーバーラベル)。"""
        prefix = match_id.split("_", 1)[0]
        region = REGION_MAP.get(prefix)
        if not region:
            known = ", ".join(sorted(REGION_MAP))
            raise ValueError(
                f"未知のリージョンプレフィックスです: {prefix!r} "
                f"(match_id={match_id})。対応プレフィックス: {known}"
            )
        server = SERVER_LABEL.get(prefix, prefix)
        return region, server

    def _get(self, region, path):
        url = RIOT_BASE.format(region=region) + path
        try:
            resp = self.session.get(url, timeout=TIMEOUT)
        except requests.Timeout:
            raise RiotApiError("TIMEOUT", detail=url)
        except requests.ConnectionError:
            raise RiotApiError("NETWORK", detail=url)
        except requests.RequestException as e:
            raise RiotApiError("NETWORK", detail=str(e)[:160])
        if resp.status_code != 200:
            raise RiotApiError(resp.status_code, detail=(resp.text or "")[:200].replace("\n", " "))
        return resp.json()

    def get_match(self, match_id):
        """1 試合の完全データとサーバーラベルを返す。失敗時は RiotApiError。"""
        region, server = self.parse_region(match_id)
        data = self._get(region, f"/lol/match/v5/matches/{match_id}")
        return data, server

    def get_puuid(self, game_name, tag_line, region):
        """Riot ID (gameName#tagLine) -> puuid。見つからない場合は ValueError。"""
        path = f"/riot/account/v1/accounts/by-riot-id/{quote(game_name)}/{quote(tag_line)}"
        try:
            data = self._get(region, path)
        except RiotApiError as e:
            if e.status_code == 404:
                raise ValueError(
                    f"アカウント {game_name}#{tag_line} が見つかりません"
                    f"（リージョン {region} が違う可能性があります）。"
                ) from e
            raise
        return data.get("puuid")

    def get_latest_match_id(self, puuid, region, count=1):
        """puuid の直近 match ID 一覧を返す（新しい順）。"""
        data = self._get(region, f"/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count={count}")
        return data
