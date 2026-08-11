"""IR -> Gemini 分析・コーチング用 Markdown レポート生成。

そのまま Gemini（等のLLM）へ貼り付けられる構造化テキストを出力する。
"""

from stats import _sec_to_mmss as _mmss

ROLE_FULL = {
    "TOP": "トップ", "JUNGLE": "ジャングル", "MIDDLE": "ミッド",
    "BOTTOM": "ボット(ADC)", "UTILITY": "サポート",
}

PING_LABELS = {
    "onMyWayPings": "OMW", "assistMePings": "アシスト要求",
    "commandPings": "コマンド", "dangerPings": "危険",
    "enemyMissingPings": "敵MISS", "enemyVisionPings": "敵視界",
    "getBackPings": "下がれ", "holdPings": "ホールド",
    "needVisionPings": "視界要求", "pushPings": "プッシュ",
    "retreatPings": "撤退", "visionClearedPings": "視界解除",
    "allInPings": "オールイン", "basicPings": "基本",
}

TEAM_LABEL = {100: "ブルー側", 200: "レッド側"}


def _t(n):
    """整数を3桁区切り文字列に。"""
    try:
        return f"{int(round(n)):,}"
    except (TypeError, ValueError):
        return "-"


def _pm(x, nd=1):
    """分間値を '/min' 付きで。"""
    if x is None:
        return "-"
    try:
        return f"{float(x):.{nd}f}/分"
    except (TypeError, ValueError):
        return "-"


def _pct(x, nd=1):
    if x is None:
        return "-"
    try:
        return f"{float(x) * 100:.{nd}f}%"
    except (TypeError, ValueError):
        return "-"


def _f(x, nd=1):
    if x is None:
        return "-"
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def _yn(b):
    return "◯" if b else "-"


def _team_side(team_id):
    return TEAM_LABEL.get(team_id, f"チーム{team_id}")


def _result_str(p):
    return "勝利" if p["win"] else "敗北"


def _ping_summary(pings):
    if not pings:
        return "0"
    top = sorted(pings.items(), key=lambda kv: kv[1], reverse=True)[:4]
    parts = [f"{PING_LABELS.get(k, k)} {v}" for k, v in top if v]
    return ", ".join(parts) if parts else "0"


def _player_block(p, focal=False, header=True):
    star = "★ " if focal else ""
    role_full = ROLE_FULL.get(p["role"], p["role"])
    lines = []
    if header:
        lines.append(f"#### {star}[{p['role_label']}] {p['champion']} — {p['name']} — {_result_str(p)}")
    lines.append(f"- **KDA** {p['kills']}/{p['deaths']}/{p['assists']} (比 {_f(p['kda_ratio'], 2)}) | "
                 f"キル関与率 {_pct(p['kp'])} | チーム与ダメージ比 {_pct(p['team_damage_pct'])}")
    lines.append(f"- **CS** {_t(p['cs'])} ({_pm(p['cs_per_min'])}) | "
                 f"ゴールド {_t(p['gold'])} ({_pm(p['gold_per_min'])}) | レベル {p['level']}")
    lines.append(f"- **与ダメージ** {_t(p['dmg'])} ({_pm(p['dmg_per_min'])}) "
                 f"[物理 {_t(p['dmg_physical'])} / 魔法 {_t(p['dmg_magic'])} / 真 {_t(p['dmg_true'])}] | "
                 f"被ダメージ {_t(p['dmg_taken'])} | 被DMG軽減 {_t(p['dmg_mitigated'])}")
    lines.append(f"- **回復** {_t(p['heal'])} (味方 {_t(p['heal_teammates'])}) | "
                 f"味方シールド {_t(p['shield_teammates'])} | "
                 f"対象DMG {_t(p['dmg_to_objectives'])} / 対タワー {_t(p['dmg_to_turrets'])}")
    lines.append(f"- **視界** スコア {p['vision_score']} ({_pm(p['vision_per_min'])}) | "
                 f"ワード設置 {p['wards_placed']} / 破壊 {p['wards_killed']} / "
                 f"コントロールワード {p['control_wards']}")
    if p.get("is_fiddlesticks"):
        # 身代わり人形の設置数は Riot API（match-v5 / timeline）から取得できない。
        # 代わりに Effigy の貢献を示す proxy 指標（ward戦果・視界スコア）を併記する。
        wt = _t(p.get("ward_takedowns"))
        lines.append(f"- **身代わり人形** 設置数はAPI非公開（ward戦果 {wt} / "
                     f"視界スコア {p['vision_score']} がEffigy貢献の目安）")
    # レーニング指標
    lane_bits = [f"ソロキル {p['solo_kills']}" if p['solo_kills'] is not None else "",
                 f"前半10分CS {_f(p['cs_first10'], 0)}" if p['cs_first10'] is not None else "",
                 f"ジャングル前10分 {_f(p['jungle_cs_before10'], 0)}" if p['jungle_cs_before10'] is not None else "",
                 f"最大CS優位 {_f(p['max_cs_lead'])}" if p['max_cs_lead'] is not None else "",
                 f"最大Lv優位 {_f(p['max_level_lead'])}" if p['max_level_lead'] is not None else "",
                 f"視界優位 {_f(p['vision_advantage'])}" if p['vision_advantage'] is not None else "",
                 f"スキルショット命中 {p['skillshots_hit']}" if p['skillshots_hit'] is not None else "",
                 f"スキルショット回避 {p['skillshots_dodged']}" if p['skillshots_dodged'] is not None else ""]
    lane_bits = [b for b in lane_bits if b]
    lines.append(f"- **レーニング** マルチキル {p['multikill_label']} | " + " | ".join(lane_bits))
    # 目標
    obj_bits = [f"初タワー {_yn(p['first_tower'])}" + (f" ({_mmss(p['first_turret_time'])})" if p.get('first_tower') and p.get('first_turret_time') else ""),
                f"ファーストブラッド {_yn(p['first_blood'])}",
                f"タワーK {p['turret_kills']}",
                f"抑制K {p['inhib_kills']}",
                f"バロン {p['baron_kills']}",
                f"ドラゴン {p['dragon_kills']}",
                f"賞金首 {_t(p['bounty_gold'])}" if p.get('bounty_gold') else "",
                f"15分前エース {p['aces_before15']}" if p.get('aces_before15') else "",
                f"パーフェクトゲーム ◯" if p.get('perfect_game') else ""]
    obj_bits = [b for b in obj_bits if b]
    lines.append("- **目標** " + " | ".join(obj_bits))
    lines.append(f"- **時間** 死亡時間 {p['time_dead']}秒 | 他者CC付与 {p['cc_others']}秒 | プレイ時間 {p['time_played']}秒")
    lines.append(f"- **アイテム** {', '.join(p['items']) if p['items'] else '-'}"
                 + (f" | トリンケット: {p['trinket']}" if p['trinket'] else ""))
    lines.append(f"- **サモナースペル** {' / '.join(p['spells']) if p['spells'] else '-'}")
    rune = p['keystone'] or "-"
    if p['primary_tree']:
        rune += f" [{p['primary_tree']}]"
    if p['primary_runes']:
        rune += " +" + "・".join(p['primary_runes'])
    sub = p['secondary_tree'] or "-"
    if p['secondary_runes']:
        sub += " (" + "・".join(p['secondary_runes']) + ")"
    shards = [s for s in (p['shards_offense'], p['shards_flex'], p['shards_defense']) if s]
    lines.append(f"- **ルーン** {rune} / 補助: {sub}"
                 + (f" | 小片: {' / '.join(shards)}" if shards else ""))
    lines.append(f"- **ピング** 計 {p['pings_total']} ({_ping_summary(p['pings'])})")
    lines.append("")
    return "\n".join(lines)


def _find_opponent(ir, focal):
    """同じロールの敵チームプレイヤーを返す（対面）。"""
    role = focal["role"]
    for p in ir["participants"]:
        if p["team_id"] != focal["team_id"] and p["role"] == role:
            return p
    return None


def _comparison_section(me, opp):
    """対面との一対比較表（コーチングで最も重要）。"""
    if not opp:
        return "（同じロールの敵が特定できないため対面比較は省略）\n"

    def delta(a, b, fmt="{:+.0f}"):
        try:
            return fmt.format(float(a) - float(b))
        except (TypeError, ValueError):
            return "-"

    rows = [
        ("KDA", f"{me['kills']}/{me['deaths']}/{me['assists']}",
         f"{opp['kills']}/{opp['deaths']}/{opp['assists']}", "-"),
        ("KDA比", _f(me["kda_ratio"], 2), _f(opp["kda_ratio"], 2),
         delta(me["kda_ratio"], opp["kda_ratio"], "{:+.2f}")),
        ("CS", _t(me["cs"]), _t(opp["cs"]), delta(me["cs"], opp["cs"])),
        ("ゴールド", _t(me["gold"]), _t(opp["gold"]), delta(me["gold"], opp["gold"])),
        ("与ダメージ", _t(me["dmg"]), _t(opp["dmg"]), delta(me["dmg"], opp["dmg"])),
        ("レベル", me["level"], opp["level"], delta(me["level"], opp["level"])),
        ("視界スコア", me["vision_score"], opp["vision_score"],
         delta(me["vision_score"], opp["vision_score"])),
        ("ワード設置", me["wards_placed"], opp["wards_placed"],
         delta(me["wards_placed"], opp["wards_placed"])),
        ("デス", me["deaths"], opp["deaths"], delta(me["deaths"], opp["deaths"])),
        ("前半10分CS", _f(me["cs_first10"], 0), _f(opp["cs_first10"], 0),
         delta(me["cs_first10"], opp["cs_first10"])),
    ]
    lines = [
        "### 🆚 対面との比較（同じロールの敵）",
        f"**あなた：{me['champion']}** vs **対面：{opp['champion']}（{opp['name']}）**  "
        "（差 = あなた − 対面。デスは少ないほど良い）",
        "",
        "| 指標 | あなた | 対面(敵) | 差 |",
        "|---|---:|---:|---:|",
    ]
    for name, a, b, d in rows:
        lines.append(f"| {name} | {a} | {b} | {d} |")
    lines.append("")
    return "\n".join(lines)


def _team_section(team_id, ir, focal_id):
    meta = ir["meta"]
    team = ir["teams"].get(team_id, {})
    side = _team_side(team_id)
    win_str = "（勝利）" if team.get("win") else "（敗北）"
    lines = []
    lines.append(f"## {side}（チーム{team_id}）{win_str}")
    obj = (f"キル {int(team.get('kills', 0))} | タワー {int(team.get('tower', 0))} | "
           f"ドラゴン {int(team.get('dragon', 0))} | バロン {int(team.get('baron', 0))} | "
           f"抑制 {int(team.get('inhibitor', 0))} | リフトヘラルド {int(team.get('riftHerald', 0))} | "
           f"ヴォイドグラブ {int(team.get('horde', 0))} | アタカーン {int(team.get('atakhan', 0))}")
    lines.append(f"**チーム目標** {obj}")
    lines.append("")
    members = [p for p in ir["participants"] if p["team_id"] == team_id]
    for p in members:
        if p is focal_id:
            # 詳細は上部「👑 あなたのプレイ」にあるため、チーム欄ではコンパクトに
            lines.append(f"- ★ **[{p['role_label']}] {p['champion']}（{p['name']}）** "
                         f"— あなた。KDA {p['kills']}/{p['deaths']}/{p['assists']} | "
                         f"CS {_t(p['cs'])} | 与ダメージ {_t(p['dmg'])} | "
                         f"視界 {p['vision_score']}（↑ 詳細は 👑 あなたのプレイ）")
        else:
            lines.append(_player_block(p, focal=False))
    lines.append("")
    return "\n".join(lines)


def _coach_prompt(focal, opponent=None):
    target = ""
    if focal:
        role_full = ROLE_FULL.get(focal["role"], focal["role"])
        target = (f"特に「分析対象プレイヤー：{focal['name']}（{focal['champion']} / {role_full}）」"
                  f"のプレイを主眼に置いて")
    else:
        target = "試合全体の流れと各プレイヤーの動きを踏まえつつ、特に目立ったプレイヤーを"
    matchup_clause = (f"対面「{opponent['champion']}（{opponent['name']}）」および"
                      if opponent else "")
    return f"""以下はLeague of Legendsの1試合のデータです。あなたは経験豊富なコーチとして、{target}分析し、日本語で具体的にコーチングしてください。

次の観点で順に分析し、最後に改善点と次へのアクションをまとめてください。
1. レーニング（CS取得・トレード・デスの有無・レーン戦績の優劣・前半10分の指標）
2. マクロ（ローテーション・オブジェクト関与・タワー/ドラゴン/バロン/ヴォイドグラブ/アタカーンへの寄り）
3. チームファイト（ダメージ効率・KDA・キル関与率・チーム与ダメージ比・ポジショニングの示唆）
4. ビジョン（ワード設置数・視界スコア・コントロールワードの活用・視界優位）
5. ビルド・ルーン（アイテム構成と順序・キーストーンとルーンの状況適合）
6. **改善点を3点** と、**次の試合ですぐに試すべき具体的アクション**

数値は根拠として引用し、抽象論ではなく「何を・どう・なぜ」が分かる改善提案にしてください。

さらに、{matchup_clause}敵チームの主要キャリー（与ダメージやキルが多い敵）を含め、フィドルスティックス視点で脅威の大きいチャンピオンを **3〜5体** 選び、以下の形式の対策データブロックを **必ず** 出力してください。本ブロックは別ツールが自動取り込みするため、コードフェンスとJSONスキーマを厳密に守ってください。

```lol-counter
[
  {{
    "champion": "<チャンピオン名（試合データの表記をそのまま）>",
    "role": "Mid | Top | Jungle | ADC | Support",
    "threat": <1〜5の整数。5が最も厳しい>,
    "tactics": ["対策を具体的に。1要素=1観点。必ず配列で"],
    "core_builds": [["アイテム1", "アイテム2"]],
    "summoner_spells": ["フラッシュ", "…"],
    "keystone": "キーストーン名（分かる場合のみ）",
    "power_spikes": ["最初のアイテム完成時", "…"]
  }}
]
```

注意:
- champion は上記試合データに登場する表記をそのまま使ってください。
- 対面（同じロールの敵）は必ず1体含めてください。
- champion / role / threat は必須、他は判明したもののみで構いません（省略可）。
- tactics は必ず **文字列の配列** として出力してください（単一文字列は不可）。core_builds は配列の配列、不明なら空配列 `[]`。
- fetched_meta 等の統計値は本ブロックに含めないでください（主観的な対策のみ）。

---

"""


def build_report(ir, coach=False, focal=None):
    meta = ir["meta"]
    out = []

    opp = _find_opponent(ir, focal) if focal else None
    if coach:
        out.append(_coach_prompt(focal, opp))

    # タイトル：自分（focal）がいれば最も強調
    if focal:
        role_full = ROLE_FULL.get(focal["role"], focal["role"])
        out.append(f"# 🎮 LoL コーチングレポート — {focal['name']}（{focal['champion']} / {role_full}）"
                   f"— {_result_str(focal)}")
    else:
        out.append("# LoL 試合データ（Gemini分析・コーチング用）")
    out.append("")

    # 試合概要
    out.append("## 試合概要")
    winner = _team_side(meta["winner_team"]) if meta["winner_team"] else "不明"
    result_suffix = ""
    if meta["surrendered"]:
        result_suffix = "（降参）" + ("・早期降参" if meta["early_surrender"] else "")
    out.append(f"- 試合ID: `{meta['match_id']}`")
    out.append(f"- サーバー: {meta['server']} | モード: {meta['mode']} | マップ: {meta['map_name']}")
    out.append(f"- ゲームバージョン: {meta['version']}")
    out.append(f"- 試合時間: {meta['duration_str']}（{meta['duration_sec']}秒）")
    out.append(f"- 開始: {meta['start_jst']} | 終了: {meta['end_jst']}")
    out.append(f"- 結果: **{winner} 勝利**{result_suffix}")
    out.append("")

    # 👑 あなたのプレイ（最も強調）＋対面比較
    if focal:
        role_full = ROLE_FULL.get(focal["role"], focal["role"])
        out.append(f"## 👑 あなたのプレイ — {focal['name']}（{focal['champion']} / {role_full}）"
                   f"— {_result_str(focal)}")
        out.append(_player_block(focal, header=False))
        out.append(_comparison_section(focal, opp))

    # チーム別（あなたは👑欄に詳細があるためチーム欄ではコンパクト）
    out.append(_team_section(100, ir, focal))
    out.append(_team_section(200, ir, focal))

    return "\n".join(out)
