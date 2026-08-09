"""全チャンピオンを champion_meta_mapping.json に初期登録する。

既存の手動エントリ（Aurora/Yasuo 等）は保持し、未登録の正規チャンピオンのみ
テンプレート（roles=自動推定, threat=3, core_builds=[], tactics=""）を追記する。

注意:
- DDragon の championFull.json には "Jade_*" の内部用ダミーエントリが含まれるため、
  アルファベットのみのキー（正規チャンピオン約169体）だけを対象とする。
- roles は DDragon の tags からの暫定推定（主要ジャングラのみ手動指定）。
  **不正確なので、生成後に各チャンピオンの roles / tactics を手動で修正すること。**
  自動追加エントリには "_autogen": true を付与してある（generate_md は無視する）。
"""
import json
import os
import re
from collections import Counter

import ddragon
import paths

# 主要ジャングラー（tags だけでは Top/Mid に分類されてしまうため手動指定）
JUNGLERS = {
    "Warwick", "Nunu", "Fiddlesticks", "Amumu", "Rammus", "Sejuani", "Zac",
    "Skarner", "Nocturne", "Shaco", "Udyr", "Shyvana", "Vi", "Hecarim",
    "RekSai", "Graves", "Nidalee", "Elise", "Kindred", "LeeSin", "JarvanIV",
    "XinZhao", "MasterYi", "Kayn", "Khazix", "Diana", "Karthus", "Lillia",
    "Belveth", "Briar", "Viego", "Ivern", "Rengar",
}

# tags ヒューリスティックで誤る既知チャンピオンの補正（内部名 -> roles）
OVERRIDES = {
    "Quinn": ["Top"],          # Marksman だが Top 主軸
    "Corki": ["Mid"],          # Marksman だが Mid 主軸
    "Vayne": ["ADC", "Top"],   # ADC/Top フレックス
}


def guess_roles(name, tags):
    """tags から主要ロールを推定（暫定・要手動修正）。"""
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


def main():
    dd = ddragon.DDragon("latest", load_full=True)
    if not dd.champion_detail:
        print("[error] championFull が取得できません。")
        return 1

    # 正規チャンピオンのみ（"Jade_*" 等のダミーを除外）
    real = {k: v for k, v in dd.champion_detail.items()
            if re.fullmatch(r"[A-Za-z]+", k)}
    print(f"正規チャンピオン: {len(real)} 体（全 {len(dd.champion_detail)} キー中）")

    path = paths.meta_mapping_path()
    meta = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)

    existing = {k for k in meta if not k.startswith("_")}
    added = []
    for name, detail in sorted(real.items()):
        if name in existing:
            continue  # 既存の手動エントリは上書きしない
        roles = guess_roles(name, detail.get("tags", []))
        meta[name] = {
            "_autogen": True,
            "roles": roles,
            "meta_info": {
                r: {"threat": 3, "core_builds": [], "tactics": ""}
                for r in roles
            },
        }
        added.append((name, roles))

    # OVERRIDES/JUNGLERS の綴り誤り（存在しない内部名）を検出
    real_names = set(real)
    typo_warns = []
    for src, names in (("OVERRIDES", set(OVERRIDES)), ("JUNGLERS", JUNGLERS)):
        for n in sorted(names - real_names):
            typo_warns.append(f"{src}: {n} は存在しません（綴り確認）")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"追加: {len(added)} 体 / 既存保持: {len(existing)} 体")
    print(f"メタファイル: {path}")
    if typo_warns:
        print("⚠️ 綴り確認:")
        for w in typo_warns:
            print(f"  - {w}")

    rc = Counter()
    for _, roles in added:
        for r in roles:
            rc[r] += 1
    print("追加組のロール分布:", dict(sorted(rc.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
