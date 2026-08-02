"""CLI と GUI の両方から使う、試合取得→統計抽出→レポート生成の共通ロジック。"""
import config
from riot_client import RiotClient, RiotApiError
from ddragon import DDragon
from stats import extract
from report import build_report

LOCALE_MAP = {"ja": "ja_JP", "en": "en_US"}


class ProcessError(Exception):
    """処理中のユーザー向けエラー。"""


def find_me(ir):
    """IR から自分（.env の MY_GAME_NAME#MY_TAG_LINE）を探す。"""
    _, _, full, _, _ = config.get_my_account()
    if not full:
        return None
    q = full.lower()
    for p in ir["participants"]:
        if (p.get("name") or "").lower() == q:
            return p
    return None


def find_focal(ir, query):
    """--player の部分一致で対象参加者を特定。チャンプ（日本語/英語）→名前の順。"""
    q = (query or "").strip().lower()
    if not q:
        return None
    participants = ir["participants"]
    for key_fn in (lambda p: p["champion"], lambda p: p["champion_en"], lambda p: p["name"]):
        matches = [p for p in participants if q in (key_fn(p) or "").lower()]
        if matches:
            return matches[0]
    return None


def resolve_focal(ir, player):
    """player 指定があればそれを、無ければ自分を返す。"""
    if player:
        return find_focal(ir, player)
    return find_me(ir)


def process_match_data(match, server, lang="ja", coach=False, player=None):
    """取得済みの試合データから統計抽出＋レポート生成（ネットワーク不要）。"""
    game_version = match.get("info", {}).get("gameVersion", "")
    dd = DDragon(game_version, locale=LOCALE_MAP.get(lang, "ja_JP"))
    ir = extract(match, server, dd)
    focal = resolve_focal(ir, player)
    markdown = build_report(ir, coach=coach, focal=focal)
    return {
        "ir": ir,
        "markdown": markdown,
        "raw": match,
        "match_id": ir["meta"]["match_id"],
        "server": server,
        "focal": focal,
        "focal_present": focal is not None,
    }


def process(api_key, match_id=None, auto_me=False, lang="ja", coach=False, player=None):
    """APIキーと試合ID（または自動取得）からレポートを生成。"""
    if not api_key:
        raise ProcessError("APIキーが設定されていません。GUIのAPIキー欄に入力してください。")
    try:
        client = RiotClient(api_key)
    except RuntimeError as e:
        raise ProcessError(str(e))

    if not match_id:
        if not auto_me:
            raise ProcessError("試合IDを入力するか「自分の直近試合を自動取得」を有効にしてください。")
        name, tag, _, region, _ = config.get_my_account()
        if not (name and tag):
            raise ProcessError("自分のアカウントが未設定です（.env の MY_GAME_NAME/MY_TAG_LINE）。")
        try:
            puuid = client.get_puuid(name, tag, region)
            ids = client.get_latest_match_id(puuid, region, count=1)
        except RiotApiError as e:
            raise ProcessError(str(e))
        except ValueError as e:
            raise ProcessError(str(e))
        if not ids:
            raise ProcessError("直近の試合が見つかりませんでした。")
        match_id = ids[0]

    try:
        match, server = client.get_match(match_id)
    except RiotApiError as e:
        raise ProcessError(str(e))
    except ValueError as e:  # リージョン判定失敗
        raise ProcessError(str(e))

    return process_match_data(match, server, lang=lang, coach=coach, player=player)
