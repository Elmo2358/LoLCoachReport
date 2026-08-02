"""match-v5 応答 -> 統計中間表現(IR) の抽出。

report.py が消費しやすい平坦な dict に変換する。欠損フィールドは安全な
デフォルトで補完し、古い試合や特殊モードの差異を吸収する。
"""
from datetime import datetime, timedelta, timezone

# queueId -> 日本語モード名（主要なもの）
MODE_NAMES = {
    400: "ノーマル(ドラフト)",
    420: "ランク(ソロ/デュオ)",
    430: "ノーマル(ブラインド)",
    440: "ランク(フレックス)",
    450: "ARAM",
    460: "ノーマル(ブラインド)",
    470: "ランク(フレックス)",
    490: "ノーマル(ドラフト)",
    700: "CLASH",
    720: "CLASH",
    740: "CLASH",
    770: "AI戦",
    830: "AI戦(入門)",
    840: "AI戦(初級)",
    850: "AI戦(中級)",
    900: "URF",
    920: "URF",
    1020: "ワンフォーオール",
    1700: "アリーナ(Arena)",
    1900: "URF",
    2000: "チュートリアル",
}

MAP_NAMES = {
    1: "古の裂け目",
    11: "サモナーズリフト",
    12: "ハウリングアビス",
    21: "アリーナ",
    22: "アリーナ",
    30: "TFT",
}

# チーム位置の表示順
ROLE_ORDER = ["TOP", "JUNGLE", "MIDDLE", "MID", "BOTTOM", "UTILITY"]
ROLE_LABEL = {
    "TOP": "TOP", "JUNGLE": "JNG", "MIDDLE": "MID", "MID": "MID",
    "BOTTOM": "BOT", "UTILITY": "SUP",
}

MULTIKILL_LABEL = {2: "ダブル", 3: "トリプル", 4: "クアドラ", 5: "ペンタ"}


def _num(d, key, default=0):
    v = d.get(key, default) if isinstance(d, dict) else default
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _sec_to_mmss(seconds):
    if seconds is None:
        return None
    seconds = int(round(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _sec_to_jp(seconds):
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    return f"{seconds // 60}分{seconds % 60:02d}秒"


def _ts_to_jst(ms):
    if not ms:
        return None
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone(timedelta(hours=9)))
        return dt.strftime("%Y-%m-%d %H:%M (JST)")
    except (TypeError, ValueError, OSError):
        return None


def _player_name(p):
    name = (p.get("riotIdGameName") or "").strip()
    tag = (p.get("riotIdTagline") or "").strip()
    if name:
        return f"{name}#{tag}" if tag else name
    return p.get("summonerName") or p.get("championName") or "Unknown"


def _extract_team_objectives(team):
    obj = team.get("objectives", {})
    champ = obj.get("champion", {})
    w = team.get("win")
    win = (w is True) or (isinstance(w, str) and w.lower() == "win")
    return {
        "win": win,
        "kills": _num(champ, "kills"),
        "tower": _num(obj.get("tower", {}), "kills"),
        "inhibitor": _num(obj.get("inhibitor", {}), "kills"),
        "dragon": _num(obj.get("dragon", {}), "kills"),
        "baron": _num(obj.get("baron", {}), "kills"),
        "riftHerald": _num(obj.get("riftHerald", {}), "kills"),
        "horde": _num(obj.get("horde", {}), "kills"),          # ヴォイドグラブ
        "atakhan": _num(obj.get("atakhan", {}), "kills"),
    }


def _extract_participant(p, dd, duration_min, team_kills):
    ch = p.get("challenges", {}) or {}
    role = (p.get("teamPosition") or p.get("individualPosition") or "").upper()
    if role == "MID":
        role = "MIDDLE"

    kills = _num(p, "kills")
    deaths = _num(p, "deaths")
    assists = _num(p, "assists")
    cs = _num(p, "totalMinionsKilled") + _num(p, "neutralMinionsKilled")

    kda_ratio = (kills + assists) / max(deaths, 1)
    kp = (kills + assists) / team_kills if team_kills else ch.get("killParticipation")

    largest_mk = int(_num(p, "largestMultiKill"))

    # アイテム
    item_ids = [p.get(f"item{i}") for i in range(6)]
    items = [dd.item(i) for i in item_ids if i]
    trinket = dd.item(p.get("item6"))

    # サモナースペル
    spells = [dd.spell(p.get("summoner1Id")), dd.spell(p.get("summoner2Id"))]
    spells = [s for s in spells if s]

    # ルーン
    perks = p.get("perks", {}) or {}
    styles = perks.get("styles", [])
    primary = styles[0] if styles else {}
    secondary = styles[1] if len(styles) > 1 else {}
    primary_sels = primary.get("selections", [])
    keystone = dd.rune(primary_sels[0].get("perk")) if primary_sels else None
    primary_tree = dd.tree(primary.get("style"))
    primary_runes = [dd.rune(s.get("perk")) for s in primary_sels[1:]]
    secondary_tree = dd.tree(secondary.get("style"))
    secondary_runes = [dd.rune(s.get("perk")) for s in secondary.get("selections", [])]
    shards = perks.get("statPerks", {}) or {}

    # ピング
    ping_keys = ["onMyWayPings", "assistMePings", "commandPings", "dangerPings",
                 "enemyMissingPings", "enemyVisionPings", "getBackPings",
                 "holdPings", "needVisionPings", "pushPings", "retreatPings",
                 "visionClearedPings", "allInPings", "basicPings"]
    pings = {k: int(_num(p, k)) for k in ping_keys if p.get(k)}
    pings_total = sum(pings.values())

    champ_id = p.get("championId")
    return {
        "name": _player_name(p),
        "champion": dd.champion(champ_id, fallback=p.get("championName", "")),
        "champion_en": p.get("championName", ""),
        "role": role,
        "role_label": ROLE_LABEL.get(role, role[:3]),
        "team_id": p.get("teamId"),
        "win": bool(p.get("win")),
        "level": int(_num(p, "champLevel")),
        # KDA
        "kills": int(kills), "deaths": int(deaths), "assists": int(assists),
        "kda_ratio": kda_ratio,
        "kp": kp,
        "double": int(_num(p, "doubleKills")), "triple": int(_num(p, "tripleKills")),
        "quadra": int(_num(p, "quadraKills")), "penta": int(_num(p, "pentaKills")),
        "multikill_label": MULTIKILL_LABEL.get(largest_mk, "なし"),
        "killing_sprees": int(_num(p, "killingSprees")),
        # 経済 / CS
        "cs": int(cs),
        "cs_per_min": cs / duration_min if duration_min else None,
        "gold": int(_num(p, "goldEarned")),
        "gold_per_min": ch.get("goldPerMinute") or (_num(p, "goldEarned") / duration_min if duration_min else None),
        # ダメージ
        "dmg": int(_num(p, "totalDamageDealtToChampions")),
        "dmg_per_min": ch.get("damagePerMinute") or (_num(p, "totalDamageDealtToChampions") / duration_min if duration_min else None),
        "dmg_magic": int(_num(p, "magicDamageDealtToChampions")),
        "dmg_physical": int(_num(p, "physicalDamageDealtToChampions")),
        "dmg_true": int(_num(p, "trueDamageDealtToChampions")),
        "dmg_taken": int(_num(p, "totalDamageTaken")),
        "dmg_mitigated": int(_num(p, "damageSelfMitigated")),
        "heal": int(_num(p, "totalHeal")),
        "heal_teammates": int(_num(p, "totalHealsOnTeammates")),
        "shield_teammates": int(_num(p, "totalDamageShieldedOnTeammates")),
        "dmg_to_objectives": int(_num(p, "damageDealtToObjectives")),
        "dmg_to_turrets": int(_num(p, "damageDealtToTurrets")),
        # 視界
        "vision_score": int(_num(p, "visionScore")),
        "vision_per_min": ch.get("visionScorePerMinute") or (_num(p, "visionScore") / duration_min if duration_min else None),
        "wards_placed": int(_num(p, "wardsPlaced")),
        "wards_killed": int(_num(p, "wardsKilled")),
        "control_wards": int(ch.get("controlWardsPlaced", _num(p, "detectorWardsPlaced"))),
        # 目標
        "turret_kills": int(_num(p, "turretKills")),
        "inhib_kills": int(_num(p, "inhibitorKills")),
        "baron_kills": int(_num(p, "baronKills")),
        "dragon_kills": int(_num(p, "dragonKills")),
        "first_blood": bool(p.get("firstBloodKill")),
        "first_tower": bool(p.get("firstTowerKill")),
        # 時間
        "time_dead": int(_num(p, "totalTimeSpentDead")),
        "cc_others": int(_num(p, "timeCCingOthers")),
        "time_played": int(_num(p, "timePlayed")),
        # ピング
        "pings_total": pings_total,
        "pings": pings,
        # アイテム / スペル / ルーン
        "items": items,
        "trinket": trinket,
        "spells": spells,
        "keystone": keystone,
        "primary_tree": primary_tree,
        "primary_runes": [r for r in primary_runes if r],
        "secondary_tree": secondary_tree,
        "secondary_runes": [r for r in secondary_runes if r],
        "shards_offense": dd.shard(shards.get("offense")),
        "shards_flex": dd.shard(shards.get("flex")),
        "shards_defense": dd.shard(shards.get("defense")),
        # challenges（コーチング向け事前計算メトリクス）
        "solo_kills": int(ch.get("soloKills", 0)) if ch.get("soloKills") is not None else None,
        "team_damage_pct": ch.get("teamDamagePercentage"),
        "first_turret_time": ch.get("firstTurretKilledTime"),
        "max_cs_lead": ch.get("maxCsAdvantageOnLaneOpponent"),
        "max_level_lead": ch.get("maxLevelLeadLaneOpponent"),
        "cs_first10": ch.get("laneMinionsFirst10Minutes"),
        "jungle_cs_before10": ch.get("jungleCsBefore10Minutes"),
        "skillshots_hit": ch.get("skillshotsHit"),
        "skillshots_dodged": ch.get("skillshotsDodged"),
        "vision_advantage": ch.get("visionScoreAdvantageLaneOpponent"),
        "aces_before15": ch.get("acesBefore15Minutes"),
        "perfect_game": ch.get("perfectGame"),
        "bounty_gold": ch.get("bountyGold"),
    }


def extract(match, server, dd):
    """match-v5 応答を IR dict に変換する。"""
    info = match.get("info", {})
    meta_data = match.get("metadata", {})
    duration_sec = _num(info, "gameDuration")
    duration_min = duration_sec / 60 if duration_sec else 0
    queue_id = info.get("queueId")
    map_id = info.get("mapId")

    teams_raw = {t["teamId"]: t for t in info.get("teams", [])}
    teams = {tid: _extract_team_objectives(t) for tid, t in teams_raw.items()}
    winner_team = next((tid for tid, t in teams.items() if t["win"]), None)

    participants = []
    for p in info.get("participants", []):
        team_kills = teams.get(p.get("teamId"), {}).get("kills", 0)
        participants.append(_extract_participant(p, dd, duration_min, team_kills))

    # チーム別・ロール順に並び替え
    def role_rank(p):
        r = p["role"]
        return ROLE_ORDER.index(r) if r in ROLE_ORDER else len(ROLE_ORDER)
    participants.sort(key=lambda p: (p["team_id"], role_rank(p), p["name"]))

    # 降参検出（participant フラグから）
    raw_parts = info.get("participants", [])
    surrendered = any(p.get("gameEndedInSurrender") for p in raw_parts)
    early_surrender = any(p.get("gameEndedInEarlySurrender") for p in raw_parts)

    return {
        "meta": {
            "match_id": meta_data.get("matchId"),
            "server": server,
            "queue_id": queue_id,
            "mode": MODE_NAMES.get(queue_id, f"キュータイプID {queue_id}"),
            "map_id": map_id,
            "map_name": MAP_NAMES.get(map_id, f"マップID {map_id}"),
            "version": info.get("gameVersion"),
            "duration_sec": int(duration_sec),
            "duration_str": _sec_to_jp(duration_sec),
            "start_jst": _ts_to_jst(info.get("gameStartTimestamp")),
            "end_jst": _ts_to_jst(info.get("gameEndTimestamp")),
            "winner_team": winner_team,
            "surrendered": surrendered,
            "early_surrender": early_surrender,
        },
        "teams": teams,
        "participants": participants,
    }
