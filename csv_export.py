"""IR（統計中間表現）-> CSV 文字列。参加者1行ずつ、主要統計を列挙。"""
import csv
import io


def _yn(b):
    return "勝" if b else "敗"


def _f(v, nd=2):
    if v is None:
        return ""
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return ""


COLUMNS = [
    "チーム", "ロール", "チャンピオン", "サモナー名", "勝敗",
    "K", "D", "A", "KDA比", "キル関与率", "チーム与DMG比",
    "CS", "CS/min", "ゴールド", "ゴールド/min", "レベル",
    "与ダメージ", "DMG/min", "被ダメージ", "回復", "視界スコア",
    "ワード設置", "コントロールワード", "デス", "ソロキル", "マルチキル",
    "タワーK", "ドラゴンK", "バロンK", "初タワー", "ファーストブラッド",
    "キーストーン", "ルーン(主)", "ルーン(補助)", "サモナースペル", "アイテム",
]


def _row(p):
    team = "Blue" if p["team_id"] == 100 else "Red"
    return [
        team, p["role"], p["champion"], p["name"], _yn(p["win"]),
        p["kills"], p["deaths"], p["assists"], _f(p["kda_ratio"]),
        _f(p["kp"] * 100 if p["kp"] is not None else None, 1) + "%",
        _f(p["team_damage_pct"] * 100 if p["team_damage_pct"] is not None else None, 1) + "%",
        p["cs"], _f(p["cs_per_min"], 1),
        p["gold"], _f(p["gold_per_min"], 1), p["level"],
        p["dmg"], _f(p["dmg_per_min"], 1), p["dmg_taken"], p["heal"], p["vision_score"],
        p["wards_placed"], p["control_wards"], p["deaths"],
        p["solo_kills"] if p["solo_kills"] is not None else "", p["multikill_label"],
        p["turret_kills"], p["dragon_kills"], p["baron_kills"],
        "◯" if p["first_tower"] else "-", "◯" if p["first_blood"] else "-",
        p["keystone"] or "", p["primary_tree"] or "", p["secondary_tree"] or "",
        "/".join(p["spells"]) if p["spells"] else "",
        ", ".join(p["items"]) if p["items"] else "",
    ]


def to_csv(ir):
    """IR を CSV 文字列に変換（ヘッダ＋参加者行）。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for p in ir["participants"]:
        writer.writerow(_row(p))
    return buf.getvalue()
