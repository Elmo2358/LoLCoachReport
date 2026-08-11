"""Data Dragon による ID -> 名称解決（チャンピオン / アイテム / サモンスペル / ルーン）。

コーチング出力を人間と Gemini の両方で読みやすくするため、数値 ID を
ローカライズ名称（既定 ja_JP）に変換する。取得結果は data/ddragon_cache に
バージョン・ロケール別でキャッシュし、同一パッチの再実行を高速化する。
"""
import json
import os

import requests

import paths

DDRAGON_BASE = "https://ddragon.leagueoflegends.com"

# ルーンの小碎片（stat shard）の ID -> 日本語名（dagon には含まれないため固定）
SHARD_NAMES = {
    5001: "体力", 5002: "物理防御", 5003: "魔法防御", 5005: "攻撃速度",
    5007: "スキルヘイスト", 5008: "適応性", 5010: "移動速度", 5011: "頑健",
    5013: "即座回復",
}


class DDragon:
    def __init__(self, game_version, locale="ja_JP", cache_dir=None, load_full=False):
        self.locale = locale
        self.cache_dir = cache_dir or paths.ddragon_cache_dir()
        os.makedirs(self.cache_dir, exist_ok=True)
        # 名称マップ（取得失敗時は空でフォールバック）
        self.champions = {}      # championId(int) -> name
        self.items = {}          # itemId(int) -> name
        self.spells = {}         # spellId(int) -> name
        self.runes = {}          # perkId(int) -> name
        self.trees = {}          # styleId(int) -> tree name
        # スキル詳細（load_full=True のとき取得。generate_md 等で使用）
        self.champion_detail = {}  # 内部名 -> 詳細dict（championFull.json 由来）
        self.champion_keys = {}    # championId(int) -> 内部名
        self.version = None
        try:
            self.version = self._resolve_version(game_version)
            self._load_all()
            if load_full:
                self._load_champion_full()
        except Exception as e:  # 通信失敗等でもID表示で続行
            print(f"[警告] Data Dragon の取得に失敗しました（IDで表示します）: {e}")

    # ---- バージョン解決 ----
    def _resolve_version(self, game_version):
        versions = self._fetch_json("/api/versions.json", cache_key=None)
        # game_version 未指定・"latest" のときは最新パッチ（generate_md 等で使用）
        if not game_version or str(game_version).strip().lower() == "latest":
            return versions[0] if versions else "latest"
        # game_version は "16.15.801.3452" のような形式。先頭2要素でパッチ一致を探す。
        gv = game_version.strip()
        if gv in versions:
            return gv
        prefix = ".".join(gv.split(".")[:2])  # 例 "16.15"
        for v in versions:  # versions.json は新しい順
            if v.startswith(prefix + ".") or v == prefix:
                return v
        return versions[0] if versions else gv  # フォールバック: 最新

    def _cache_path(self, name):
        return os.path.join(self.cache_dir, f"{self.version}_{self.locale}_{name}.json")

    def _fetch_json(self, path, cache_key):
        """cache_key が None のときはキャッシュしない（versions.json は最新が必要）。"""
        if cache_key is not None:
            cp = self._cache_path(cache_key)
            if os.path.exists(cp):
                with open(cp, encoding="utf-8") as f:
                    return json.load(f)
        url = f"{DDRAGON_BASE}{path}"
        data = requests.get(url, timeout=20).json()
        if cache_key is not None:
            cp = self._cache_path(cache_key)
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        return data

    def _load_all(self):
        v = self.version
        locale = self.locale
        # チャンピオン
        cj = self._fetch_json(f"/cdn/{v}/data/{locale}/champion.json", "champion")
        for info in cj.get("data", {}).values():
            try:
                self.champions[int(info["key"])] = info["name"]
            except (KeyError, ValueError):
                continue
        # アイテム
        ij = self._fetch_json(f"/cdn/{v}/data/{locale}/item.json", "item")
        for iid, info in ij.get("data", {}).items():
            try:
                self.items[int(iid)] = info["name"]
            except (ValueError, KeyError):
                continue
        # サモナースペル
        sj = self._fetch_json(f"/cdn/{v}/data/{locale}/summoner.json", "summoner")
        for info in sj.get("data", {}).values():
            try:
                self.spells[int(info["key"])] = info["name"]
            except (KeyError, ValueError):
                continue
        # ルーン（ツリー構造を平坦化）
        rj = self._fetch_json(f"/cdn/{v}/data/{locale}/runesReforged.json", "runes")
        for tree in rj:
            try:
                self.trees[int(tree["id"])] = tree["name"]
            except (KeyError, ValueError):
                pass
            for slot in tree.get("slots", []):
                for rune in slot.get("runes", []):
                    try:
                        self.runes[int(rune["id"])] = rune["name"]
                    except (KeyError, ValueError):
                        continue

    # ---- スキル詳細（championFull.json） ----
    def _load_champion_full(self):
        """全チャンピオンのスキル詳細を取得・保持する（generate_md 等で使用）。

        名称解決（champion.json）は既に完了している前提。ここでの失敗は
        スキル詳細が無効になるだけで済むよう、独立して例外を握る。
        """
        try:
            v = self.version
            locale = self.locale
            cj = self._fetch_json(
                f"/cdn/{v}/data/{locale}/championFull.json", "championFull")
            for internal_name, info in cj.get("data", {}).items():
                try:
                    self._parse_champion_full(internal_name, info)
                except (KeyError, ValueError, TypeError):
                    continue
        except Exception as e:
            print(f"[警告] championFull.json の取得に失敗しました（スキル詳細は無効）: {e}")

    def _parse_champion_full(self, internal_name, info):
        key = info.get("key")
        if key is None:
            return
        try:
            self.champion_keys[int(key)] = internal_name
        except ValueError:
            return

        spells = []
        for idx, sp in enumerate(info.get("spells", [])):
            spells.append({
                # "key" は Q/W/E/R。欠損時のみ順序から補完
                "slot": sp.get("key") or ("QWER"[idx] if idx < 4 else str(idx)),
                "name": sp.get("name", ""),
                # CC検出は description + tooltip を対象（フレーバーテキストは含まない）
                "description": sp.get("description", ""),
                "tooltip": sp.get("tooltip", ""),
                "cooldownBurn": sp.get("cooldownBurn", ""),
                "costBurn": sp.get("costBurn", ""),
                "rangeBurn": sp.get("rangeBurn", ""),
                "maxrank": sp.get("maxrank"),
                "effectBurn": sp.get("effectBurn", []),
                "leveltip": sp.get("leveltip"),
            })

        passive = info.get("passive") or {}
        self.champion_detail[internal_name] = {
            "id": internal_name,
            "key": key,
            "name": info.get("name", internal_name),
            "title": info.get("title", ""),
            "partype": info.get("partype", ""),
            "tags": info.get("tags", []),
            "info": info.get("info", {}),
            "stats": info.get("stats", {}),
            "passive": {
                "name": passive.get("name", ""),
                "description": passive.get("description", ""),
            },
            "spells": spells,
            "allytips": info.get("allytips", []),
            "enemytips": info.get("enemytips", []),
        }

    # ---- スキル詳細の参照 ----
    def detail(self, internal_name):
        """内部名 -> チャンピオン詳細dict。未収録は None。"""
        return self.champion_detail.get(internal_name)

    # ---- 名称参照（常に文字列を返す） ----
    def champion(self, champion_id, fallback=""):
        return self.champions.get(int(champion_id)) or fallback or f"Champ#{champion_id}"

    def item(self, item_id):
        if not item_id:
            return None
        return self.items.get(int(item_id)) or f"アイテム#{item_id}"

    def spell(self, spell_id):
        if not spell_id:
            return None
        return self.spells.get(int(spell_id)) or f"スペル#{spell_id}"

    def rune(self, perk_id):
        if not perk_id:
            return None
        return self.runes.get(int(perk_id)) or f"ルーン#{perk_id}"

    def tree(self, style_id):
        if not style_id:
            return None
        return self.trees.get(int(style_id)) or f"ツリー#{style_id}"

    @staticmethod
    def shard(perk_id):
        return SHARD_NAMES.get(int(perk_id)) if perk_id else None
