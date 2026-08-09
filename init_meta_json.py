"""全チャンピオンを champion_meta_mapping.json に登録し、ロールを割り当てる。

既存の手動エントリ（Aurora/Yasuo 等、tactics/builds を持つキュレーション済みロール）
は保持したうえで、全チャンピオンの roles を OVERRIDES > JUNGLERS > tags ヒューリスティック
で（再）割り当てする。再実行可能（冪等）。

注意:
- DDragon の championFull.json には "Jade_*" の内部用ダミーエントリが含まれるため、
  アルファベットのみのキー（正規チャンピオン約170体）だけを対象とする。
- OVERRIDES は確定版（ユーザー指定）。タグだけでは判別困難なチャンピオンはここで確定させる。
- キュレーション済み（tactics/core_builds 有り）でないエントリには "_autogen": true を付け、
  未整備分が grep で見つけられるようにする（generate_md はこのキーを無視する）。
"""
import json
import os
import re
from collections import Counter

import ddragon
import paths

# 主要ジャングラー（OVERRIDES に無いもの。tags だけでは Top/Mid に分類されるため手動指定）
JUNGLERS = {
    "Warwick", "Nunu", "Amumu", "Rammus", "Sejuani", "Zac",
    "Skarner", "Nocturne", "Udyr", "Shyvana", "Vi", "RekSai",
    "JarvanIV", "XinZhao", "MasterYi", "Kayn", "Diana", "Karthus", "Lillia",
    "Belveth", "Briar", "Ivern",
}

# 確定版ロール（ユーザー指定）。内部名は DDragon 表記に補正済み。
OVERRIDES = {
    # --- Mid / APC / 暗殺者系 ---
    "Azir": ["Mid"],
    "Annie": ["Mid"],
    "Anivia": ["Mid"],
    "Ahri": ["Mid"],
    "Akali": ["Mid"],
    "Aurora": ["Mid", "Top"],
    "Kassadin": ["Mid"],
    "Katarina": ["Mid"],
    "Leblanc": ["Mid"],           # [修正] DDragon内部名 Leblanc
    "Lissandra": ["Mid"],
    "Malzahar": ["Mid"],
    "Orianna": ["Mid"],
    "Syndra": ["Mid", "ADC"],   # APC対応
    "Taliyah": ["Jungle", "Mid"],
    "Talon": ["Mid", "Jungle"],
    "Viktor": ["Mid", "ADC"],   # APC対応
    "Vex": ["Mid"],
    "Veigar": ["Mid", "ADC"],   # APC対応
    "Xerath": ["Mid", "Support"],
    "Yasuo": ["Mid", "Top", "ADC"],  # ADC対応に修正
    "Yone": ["Mid", "Top"],
    "Zoe": ["Mid"],
    "Zed": ["Mid", "Jungle"],   # ロール微調整

    # --- ADC (ボトム) 系 ---
    "Ashe": ["ADC"],
    "Akshan": ["Mid", "ADC"],
    "Caitlyn": ["ADC"],
    "Corki": ["Mid", "ADC"],
    "Ezreal": ["ADC"],
    "Jhin": ["ADC"],
    "Jinx": ["ADC"],
    "Kaisa": ["ADC"],           # [修正] DDragon内部名 Kaisa
    "Kalista": ["ADC"],
    "KogMaw": ["ADC"],
    "Lucian": ["ADC"],
    "MissFortune": ["ADC"],
    "Samira": ["ADC"],
    "Sivir": ["ADC"],
    "Tristana": ["ADC", "Mid"],
    "Twitch": ["ADC", "Jungle"],
    "Varus": ["ADC"],
    "Vayne": ["ADC", "Top"],
    "Xayah": ["ADC"],
    "Zeri": ["ADC"],

    # --- Top / Jungle ファイター・タンク系 ---
    "Aatrox": ["Top", "Jungle"],
    "Camille": ["Top", "Support"],   # ロール微調整
    "Darius": ["Top", "Jungle"],
    "Gwen": ["Top", "Jungle"],
    "Gangplank": ["Top", "Mid"],
    "Malphite": ["Top", "Jungle"],
    "Nasus": ["Top", "Jungle"],
    "Rumble": ["Top"],
    "Sion": ["Top", "Mid"],
    "Chogath": ["Top", "Mid"],       # [修正] DDragon内部名 Chogath
    "Fiora": ["Top"],
    "Gnar": ["Top"],
    "Illaoi": ["Top"],
    "Jax": ["Top", "Jungle"],
    "Jayce": ["Top", "Mid"],
    "Kayle": ["Top"],
    "Kennen": ["Top"],
    "KSante": ["Top"],               # [修正] K'Sante -> KSante
    "Mordekaiser": ["Top"],
    "Olaf": ["Top", "Jungle"],
    "Ornn": ["Top"],
    "Poppy": ["Top", "Jungle", "Support"],
    "Renekton": ["Top"],
    "Riven": ["Top"],
    "Singed": ["Top"],
    "Tryndamere": ["Top"],
    "Urgot": ["Top"],
    "Volibear": ["Top", "Jungle"],
    "Yorick": ["Top"],

    # 主要な Jungle 専用/兼任（OVERRIDES で明示）
    "Viego": ["Jungle"],
    "Fiddlesticks": ["Jungle", "Mid"],
    "LeeSin": ["Jungle"],
    "Graves": ["Jungle"],
    "Khazix": ["Jungle"],
    "Rengar": ["Jungle", "Top"],
    "Nidalee": ["Jungle"],
    "Evelynn": ["Jungle"],
    "Elise": ["Jungle"],
    "Hecarim": ["Jungle"],
    "Kindred": ["Jungle"],
    "Shaco": ["Jungle", "Support"],

    # --- Support 専用 / 兼任 ---
    "Karma": ["Support"],
    "Morgana": ["Support", "Jungle", "Mid"],
    "Rell": ["Support"],
    "Leona": ["Support"],
    "Braum": ["Support"],
    "Janna": ["Support"],
    "Lulu": ["Support"],
    "Milio": ["Support"],
    "Nami": ["Support"],
    "Nautilus": ["Support"],
    "Rakan": ["Support"],
    "Renata": ["Support"],           # [修正] Renata Glasc -> Renata（内部名にGlascは付かない）
    "Seraphine": ["Support", "Mid"],
    "Sona": ["Support"],
    "Soraka": ["Support"],
    "Taric": ["Support"],
    "Thresh": ["Support"],
    "Yuumi": ["Support"],
    "Zyra": ["Support", "Jungle"],
    "Alistar": ["Support"],
    "Blitzcrank": ["Support"],
}


def guess_roles(name, tags):
    """OVERRIDES > JUNGLERS > tags ヒューリスティック でロールを決定。"""
    if name in OVERRIDES:
        return OVERRIDES[name]
    if name in JUNGLERS:
        return ["Jungle"]
    t = set(tags or [])
    if "Support" in t:
        return ["Support"]
    if "Marksman" in t:
        return ["ADC"]
    if "Mage" in t or "Assassin" in t:
        return ["Mid"]
    if "Fighter" in t or "Tank" in t:
        return ["Top"]
    return ["Top"]


def _template():
    return {"threat": 3, "core_builds": [], "tactics": ""}


def main():
    dd = ddragon.DDragon("latest", load_full=True)
    if not dd.champion_detail:
        print("[error] championFull が取得できません。")
        return 1

    # 正規チャンピオンのみ（"Jade_*" 等のダミーを除外）
    real = {k: v for k, v in dd.champion_detail.items()
            if re.fullmatch(r"[A-Za-z]+", k)}
    print(f"正規チャンピオン: {len(real)} 体（全 {len(dd.champion_detail)} キー中）")

    # OVERRIDES/JUNGLERS の綴り誤り（存在しない内部名）を検出
    real_names = set(real)
    typo_warns = []
    for src, names in (("OVERRIDES", set(OVERRIDES)), ("JUNGLERS", JUNGLERS)):
        for n in sorted(names - real_names):
            typo_warns.append(f"{src}: {n} は存在しません（綴り確認）")
    if typo_warns:
        print("⚠️ 綴り確認（これらはフォールバックします）:")
        for w in typo_warns:
            print(f"  - {w}")

    path = paths.meta_mapping_path()
    meta = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)

    updated, curated_kept = 0, 0
    for name, detail in sorted(real.items()):
        roles = guess_roles(name, detail.get("tags", []))
        old_mi = (meta.get(name) or {}).get("meta_info") or {}
        # キュレーション済み（tactics or core_builds を持つ）ロールだけ保持
        keep = {r: mi for r, mi in old_mi.items()
                if mi.get("tactics") or mi.get("core_builds")}
        new_mi = {r: keep.get(r) or _template() for r in roles}
        curated = any(mi.get("tactics") or mi.get("core_builds") for mi in new_mi.values())
        entry = {"roles": roles, "meta_info": new_mi}
        if not curated:
            entry["_autogen"] = True   # 監査用: まだ手動未整備
        meta[name] = entry
        updated += 1
        if curated:
            curated_kept += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"ロール再割当て: {updated} 体（うちキュレーション保持 {curated_kept} 体）")
    print(f"メタファイル: {path}")

    # ロール分布（チャンピオン単位・ロール単位）
    rc = Counter()
    for name in real:
        for r in meta[name]["roles"]:
            rc[r] += 1
    print("ロール分布（チャンピオン×ロール）:", dict(sorted(rc.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
