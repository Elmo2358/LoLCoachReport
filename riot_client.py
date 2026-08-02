"""Riot match-v5 API ラッパ。

記事 (Zenn / moudousiyou) と同様に RiotWatcher を使用し、match ID から
1 試合の完全なデータを取得する。単一リクエストで完結するため、記事の
レート制限待機関数は不要（RiotWatcher が 429 リトライを内包）。
"""
import os

from riotwatcher import LolWatcher, RiotWatcher
from requests.exceptions import HTTPError

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


class RiotApiError(Exception):
    """Riot API 呼び出し失敗。HTTP ステータスに応じた日本語メッセージを保持する。"""

    _MESSAGES = {
        401: ("APIキーが無効または期限切れの可能性があります（HTTP 401）。"
              "Riot Developer Portal (https://developer.riotgames.com/) でキーを再生成し、"
              ".env の RIOT_API_KEY を更新してください。"),
        403: ("APIキーが無効または期限切れです（HTTP 403）。"
              "Riot Developer Portal (https://developer.riotgames.com/) でキーを再生成し、"
              ".env の RIOT_API_KEY を更新してください。"),
        404: ("試合が見つかりません（HTTP 404）。試合IDの間違い、リージョン違い、"
              "または対応していないゲームモードの可能性があります。"),
        429: "APIレート制限に達しました（HTTP 429）。数十秒待ってから再実行してください。",
    }

    def __init__(self, status_code, match_id, detail=""):
        self.status_code = status_code
        msg = self._MESSAGES.get(
            status_code,
            f"Riot APIエラーが発生しました（HTTP {status_code}）。",
        )
        full = f"[{match_id}] {msg}"
        if detail:
            full += f"\n  詳細: {detail}"
        super().__init__(full)


class RiotClient:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("RIOT_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "RIOT_API_KEY が設定されていません。.env に記述するか環境変数で設定してください。"
            )
        self.lol = LolWatcher(self.api_key)
        self.riot = RiotWatcher(self.api_key)

    @staticmethod
    def parse_region(match_id):
        """match ID (例 'KR_8323484082') -> (大陸ルーティング, サーバーラベル)。"""
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

    def get_match(self, match_id):
        """1 試合の完全データとサーバーラベルを返す。失敗時は RiotApiError。"""
        region, server = self.parse_region(match_id)
        try:
            data = self.lol.match.by_id(region, match_id)
        except HTTPError as e:
            resp = getattr(e, "response", None)
            code = getattr(resp, "status_code", None) if resp is not None else None
            detail = ""
            if resp is not None:
                detail = getattr(resp, "text", "") or ""
                detail = detail[:200].replace("\n", " ")
            raise RiotApiError(code, match_id, detail) from e
        return data, server

    def get_puuid(self, game_name, tag_line, region):
        """Riot ID (gameName#tagLine) -> puuid。見つからない場合は ValueError。"""
        try:
            data = self.riot.account.by_riot_id(region, game_name, tag_line)
        except HTTPError as e:
            resp = getattr(e, "response", None)
            code = getattr(resp, "status_code", None) if resp is not None else None
            if code == 404:
                raise ValueError(
                    f"アカウント {game_name}#{tag_line} が見つかりません"
                    f"（リージョン {region} が違う可能性があります）。"
                ) from e
            raise RiotApiError(code, f"{game_name}#{tag_line}") from e
        return data.get("puuid")

    def get_latest_match_id(self, puuid, region, count=1):
        """puuid の直近 match ID 一覧を返す（新しい順）。"""
        try:
            return self.lol.match.matchlist_by_puuid(region, puuid, count=count)
        except HTTPError as e:
            resp = getattr(e, "response", None)
            code = getattr(resp, "status_code", None) if resp is not None else None
            raise RiotApiError(code, str(puuid)[:12]) from e
