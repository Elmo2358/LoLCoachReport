"""AI が出力した lol-counter ブロックを対策JSONにマージし、対策MDを再生成する。

GUI の「チャンピオン対策メンテナンス」欄に貼り付けられたテキストから
fenced コードブロック（```lol-counter または ```json）を抽出し、
champion_meta_mapping.json の該当チャンピオン/ロールの手動フィールドだけを
部分マージ（上書き）する。fetched_meta は一切触らない（echo chamber 防止）。
"""
import json
import os
import re
import shutil
from dataclasses import dataclass, field

import paths
import generate_md


class CounterMergeError(Exception):
    """ユーザー向けエラー（ブロック未検出/JSONパース失敗/未知のチャンピオン等）。"""


# lol-counter / json のフェンスブロック（DOTALLで複数行、非貪欲）
_BLOCK_RE = re.compile(
    r"```(?:lol-counter|json)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
# フェンス無しのフォールバック（裸の配列）。非貪欲にしないと長文で壊滅的バックトラック。
_BARE_ARRAY_RE = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)

# AI が書きそうなロール表記の揺れを正規化
_ROLE_ALIASES = {
    "MIDDLE": "Mid", "MID": "Mid", "TOP": "Top",
    "JUNGLE": "Jungle", "JG": "Jungle", "JUNG": "Jungle",
    "BOT": "ADC", "BOTTOM": "ADC", "ADC": "ADC",
    "SUPPORT": "Support", "SUP": "Support", "UTILITY": "Support",
}

# 手動管理フィールドのみ。fetched_meta は絶対に触らない。
MANUAL_FIELDS = (
    "threat", "core_builds", "tactics",
    "summoner_spells", "keystone", "power_spikes",
)


@dataclass
class MergeResult:
    updated: list = field(default_factory=list)            # [(internal_name, role), ...]
    skipped_role: list = field(default_factory=list)       # [(name, role, reason), ...]
    unknown_champions: list = field(default_factory=list)  # [input_name, ...]
    md_written: list = field(default_factory=list)         # generate_md._run_full の戻り値


# ---------------------------------------------------------------- 名前解決

def _build_name_resolver(dd):
    """DDragon から {ja表示名: 内部名}, {内部名小文字: 内部名} を構築。

    Jade_Ahri 等のダミーエントリ（内部名に _ 等が入る）は init_meta_json と同じ
    [A-Za-z]+ フィルタで除外する。
    """
    by_display_ja = {}
    by_internal = {}
    for key, internal in (dd.champion_keys or {}).items():
        if not re.fullmatch(r"[A-Za-z]+", internal):
            continue  # Jade_Ahri 等のダミーを除外
        by_internal[internal.lower()] = internal
        ja = (dd.champions or {}).get(int(key))
        if ja:
            by_display_ja[ja] = internal
        detail = (dd.champion_detail or {}).get(internal) or {}
        if detail.get("name"):
            by_display_ja.setdefault(detail["name"], internal)
    return by_display_ja, by_internal


def _norm_key(s):
    """アポストロフィ/スペース/ドットを除去して小文字化（Kai'Sa→kaisa, Lee Sin→leesin）。"""
    return s.replace("'", "").replace(" ", "").replace(".", "").lower()


def _resolve_name(champ_in, by_display_ja, by_internal):
    if not isinstance(champ_in, str):
        return None
    name = champ_in.strip()
    if not name:
        return None
    # 1. 日本語表示名の完全一致
    if name in by_display_ja:
        return by_display_ja[name]
    # 2. 内部名の大小文字無視
    lower = name.lower()
    if lower in by_internal:
        return by_internal[lower]
    # 3. アポストロフィ/スペース/ドット除去で再照合
    stripped = _norm_key(name)
    for internal in by_internal.values():
        if _norm_key(internal) == stripped:
            return internal
    return None


def _normalize_role(r):
    r = (r or "").strip()
    return _ROLE_ALIASES.get(r.upper(), r)


# ------------------------------------------------------------- エントリ正規化

def _normalize_str_list(v, field_name):
    if v is None:
        return None
    if isinstance(v, str):
        return [v.strip()] if v.strip() else None
    if isinstance(v, list):
        out = [str(x).strip() for x in v if str(x).strip()]
        return out or None
    raise CounterMergeError(f"{field_name} は文字列または文字列の配列である必要があります "
                            f"(got {type(v).__name__})")


def _normalize_tactics(v):
    if v is None:
        return None
    if isinstance(v, str):
        return [v.strip()] if v.strip() else None
    if isinstance(v, list):
        out = [str(x).strip() for x in v if str(x).strip()]
        return out or None
    raise CounterMergeError(f"tactics は文字列または文字列の配列である必要があります "
                            f"(got {type(v).__name__})")


def _normalize_core_builds(v):
    if v is None:
        return None
    if not isinstance(v, list):
        raise CounterMergeError("core_builds は配列の配列である必要があります "
                                f"(got {type(v).__name__})")
    out = []
    for variant in v:
        if isinstance(variant, list):
            items = [str(x).strip() for x in variant if str(x).strip()]
            out.append(items)
        elif isinstance(variant, str) and variant.strip():
            out.append([variant.strip()])
    return out or None


def _normalize_entry(raw, resolver):
    """1エントリを正規化して (entry, None) or (None, unknown_input_name) を返す。"""
    if not isinstance(raw, dict):
        raise CounterMergeError(f"要素がオブジェクトではありません (got {type(raw).__name__})")
    champ_in = raw.get("champion")
    if not isinstance(champ_in, str) or not champ_in.strip():
        raise CounterMergeError("champion フィールドが空または未指定です")
    name = _resolve_name(champ_in, *resolver)
    if name is None:
        return None, champ_in.strip()

    role = _normalize_role(raw.get("role"))
    threat = raw.get("threat")
    if threat is not None:
        try:
            threat = int(threat)
            assert 1 <= threat <= 5
        except (TypeError, ValueError, AssertionError):
            raise CounterMergeError(
                f"{name}: threat は 1〜5 の整数である必要があります (got {threat!r})")

    entry = {
        "name": name,
        "role": role,
        "threat": threat,
        "tactics": _normalize_tactics(raw.get("tactics")),
        "core_builds": _normalize_core_builds(raw.get("core_builds")),
        "summoner_spells": _normalize_str_list(raw.get("summoner_spells"), "summoner_spells"),
        "keystone": raw.get("keystone") if isinstance(raw.get("keystone"), str) else None,
        "power_spikes": _normalize_str_list(raw.get("power_spikes"), "power_spikes"),
    }
    return entry, None


# ------------------------------------------------------------------ マージ

def _merge_entry(meta, entry):
    """meta を破壊的にマージ。戻り値: ("updated"|"skipped", role, reason|None)。"""
    name, role = entry["name"], entry["role"]
    champ = meta.get(name)
    if champ is None:
        return "skipped", role, f"{name} が対策JSONに存在しません"
    roles = champ.setdefault("roles", [])
    if role not in roles:
        # 試合で非標準ロールだった場合など、roles を拡張して対策を登録できるようにする
        roles.append(role)
    ri = champ.setdefault("meta_info", {}).setdefault(role, {})
    for field_name in MANUAL_FIELDS:
        val = entry.get(field_name)
        if val is not None:           # 提供されたら上書き、省略なら保持
            ri[field_name] = val
    return "updated", role, None


# ------------------------------------------------------------- ブロック抽出

def _extract_block(text):
    """fenced ブロックを抽出してJSON文字列を返す。無ければフォールバック、それでも無ければ raise。"""
    matches = _BLOCK_RE.findall(text)
    for m in matches:
        candidate = m.strip()
        if candidate:
            return candidate
    bare = _BARE_ARRAY_RE.search(text)
    if bare:
        return bare.group(0).strip()
    raise CounterMergeError(
        "対策データブロック（```lol-counter ... ```）が見つかりませんでした。")


# ------------------------------------------------------------ 書き込み

def _write_meta_atomic(path, meta):
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# ------------------------------------------------------------ 公開API

def apply_counter_block(text, *, dd, today_iso=None, regen_md=True, progress=None):
    """テキストから lol-counter ブロックを解析してJSONにマージし、対策MDを再生成する。

    progress: 進捗メッセージを受け取るコールバック（省略可）。どの段階で止まったか判別用。
    戻り値: MergeResult。全エントリが失敗した場合は書き込まず CounterMergeError。
    """
    def _p(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    _p("対策ブロックを抽出中...")
    block_json = _extract_block(text)
    _p("JSON をパース中...")
    try:
        parsed = json.loads(block_json)
    except json.JSONDecodeError as e:
        raise CounterMergeError(f"ブロック内のJSONの解析に失敗しました: {e}")

    if isinstance(parsed, list):
        entries_raw = parsed
    elif isinstance(parsed, dict):
        entries_raw = [parsed]
    else:
        entries_raw = []
    if not entries_raw:
        raise CounterMergeError("ブロック内に対策エントリがありません")

    _p("チャンピオン名を解決・エントリを正規化中...")
    resolver = _build_name_resolver(dd)
    entries, unknown = [], []
    for raw in entries_raw:
        norm, unk = _normalize_entry(raw, resolver)
        if norm:
            entries.append(norm)
        elif unk:
            unknown.append(unk)

    _p("対策JSON を読み込み中...")
    path = paths.meta_mapping_path()
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)

    _p("対策JSON にマージ中...")
    updated, skipped_role = [], []
    for entry in entries:
        status, role, reason = _merge_entry(meta, entry)
        if status == "updated":
            updated.append((entry["name"], role))
        else:
            skipped_role.append((entry["name"], role, reason))

    if not updated:
        raise CounterMergeError(
            "更新できるエントリがありませんでした（ロール不一致または未知のチャンピオン）。")

    _p("対策JSON を保存中...")
    _write_meta_atomic(path, meta)

    md_written = []
    if regen_md:
        _p("対策Markdown を再生成中...")
        valid_items = set((dd.items or {}).values())
        today_iso = today_iso or _today_iso()
        md_written = generate_md._run_full(dd, meta, valid_items, today_iso)

    return MergeResult(updated=updated, skipped_role=skipped_role,
                       unknown_champions=unknown, md_written=md_written)


def _today_iso():
    import datetime
    return datetime.date.today().isoformat()
