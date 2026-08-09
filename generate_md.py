"""チャンピオン対策 Markdown をロール別に自動生成する。

DDragon の基礎データ（スキル・CD・ステータス）と手動管理のメタデータ
(champion_meta_mapping.json) を結合し、LLM（NotebookLM 等）に読み込ませるための
高品質な Markdown をロール別5ファイルに出力する。

フィドルスティックス Mid 視点。スキル説明文（ja_JP）から W(ドレイン:チャネリング)
を中断させるCC（W中断CC）と、Wは止まらないが位置取りを制限するCC（移動CC）を
自動検出してタグ付けする。スキル名・CC・CD・射程など数値はすべて DDragon 由来で、
ハルシネーションを防ぐため手書きしない。

使い方:
    python generate_md.py              # 全件生成（初回・メタJSON編集後・強制更新）
    python generate_md.py --diff       # 差分更新：前回パッチと比較し変更ロールのみ再生成
    python generate_md.py --version 16.15 [--locale ja_JP]
"""
import argparse
import datetime
import json
import os
import re

import ddragon
import paths

ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]
ROLE_JA = {
    "Top": "トップ", "Jungle": "ジャングル", "Mid": "ミッド",
    "ADC": "ボトム(ADC)", "Support": "サポート",
}

# ---- CC キーワード辞書 -------------------------------------------------
# (キーワード, 正規化ラベル)。表記揺れを同一ラベルに集約する。
# break: フィドルのW(チャネリング)を中断させるCC / move: 位置取りを制限するCC（Wは止まらない）
_BREAK_KW = [
    ("スタン", "スタン"), ("気絶", "スタン"),
    ("ノックアップ", "ノックアップ"), ("空中に打ち上げ", "ノックアップ"),
    ("打ち上げ", "ノックアップ"), ("宙に浮か", "ノックアップ"),
    ("サプレッション", "サプレッション"), ("制圧", "サプレッション"),
    ("沈黙", "沈黙"), ("サイレンス", "沈黙"),
    ("恐怖", "恐怖"), ("テラー", "恐怖"),
    ("チャーム", "チャーム"), ("魅了", "チャーム"),
    ("挑発", "挑発"),
    ("ノックバック", "ノックバック"), ("吹き飛ばし", "ノックバック"),
    ("小動物", "ポリモーフ"), ("ポリモーフ", "ポリモーフ"),
]
_MOVE_KW = [
    ("スネア", "スネア"), ("拘束", "スネア"), ("ルート", "スネア"), ("バインド", "スネア"),
    # DDragon の ja_JP は「スロー」ではなく古仮名表記「スロウ」を使うことが多い
    ("スロウ", "スロー"), ("スロー", "スロー"),
    ("移動速度低下", "スロー"), ("移動速度を低下", "スロー"),
]
_KW_TABLE = [(kw, "break", label) for kw, label in _BREAK_KW] + \
            [(kw, "move", label) for kw, label in _MOVE_KW]

# CCキーワードがこれらに近接する場合、「適用」ではなく「解除/無効化/耐性」と解釈し、
# 誤検知（例: 「スタンを解除する」「恐怖を受けない」）を防ぐ。
_NEGATION_HINTS = (
    "受けない", "無効", "解除", "軽減", "耐性", "免疫", "無視", "防ぐ", "回避", "影響を受け",
)

_TAG_RE = re.compile(r"<[^>]+>")
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")


# ----------------------------------------------------------------------------
# テキスト処理・CC検出
# ----------------------------------------------------------------------------
def _clean(text):
    """DDragon のタグとプレースホルダを除去し、空白を正規化する。"""
    if not text:
        return ""
    text = _PLACEHOLDER_RE.sub("", _TAG_RE.sub("", text))
    return re.sub(r"\s+", " ", text).strip()


def _is_negated(text, idx, kw_len):
    """CCキーワードの前後6文字に否定/除去/無効の文脈があれば True。"""
    left = text[max(0, idx - 6):idx]
    right = text[idx + kw_len:idx + kw_len + 6]
    return any(neg in (left + right) for neg in _NEGATION_HINTS)


def _detect_cc(*texts):
    """テキスト群からCCラベルを検出し、(break_set, move_set) を返す。

    description + tooltip（機構テキスト）のみを対象。フレーバー(loRE)や
    スキル名・パッシブ名は対象外なので、名前に偶然CC語が含まれる誤検知を避ける。
    """
    combined = " ".join(_clean(t) for t in texts)
    break_set, move_set = set(), set()
    for kw, cat, label in _KW_TABLE:
        start = 0
        while True:
            idx = combined.find(kw, start)
            if idx == -1:
                break
            if not _is_negated(combined, idx, len(kw)):
                # 非否定の1ヒットで presence 確定（否定出現のみなら検出しない）
                (break_set if cat == "break" else move_set).add(label)
                break
            start = idx + len(kw)
    return break_set, move_set


def _analyze_champion(detail):
    """チャンピオン詳細 -> (champ_break, champ_move, skill_cc)。
    skill_cc: CCを持つ slot のみ -> (break_set, move_set)。"""
    champ_b, champ_m = set(), set()
    skill_cc = {}
    pb, pm = _detect_cc(detail["passive"]["description"])
    if pb or pm:
        skill_cc["P"] = (pb, pm)
    champ_b |= pb
    champ_m |= pm
    for sp in detail["spells"]:
        b, m = _detect_cc(sp["description"], sp["tooltip"])
        if b or m:
            skill_cc[sp["slot"]] = (b, m)
        champ_b |= b
        champ_m |= m
    return champ_b, champ_m, skill_cc


# ----------------------------------------------------------------------------
# レンダリング
# ----------------------------------------------------------------------------
def _stars(n):
    n = max(1, min(5, int(n)))
    return "★" * n + "☆" * (5 - n) + f" ({n}/5)"


def _cc_summary(champ_b, champ_m, skill_cc):
    """チャンピオン冒頭の W中断CC / 移動CC サマリ行。"""
    def per_slot(getter):
        items = []
        for slot in ["P", "Q", "W", "E", "R"]:
            if slot in skill_cc and getter(skill_cc[slot]):
                items.append(f"{slot}=" + "+".join(sorted(getter(skill_cc[slot]))))
        return ", ".join(items) if items else "あり"

    line = (f"**W中断CC:** 【有】（{per_slot(lambda c: c[0])}）" if champ_b
            else "**W中断CC:** 【無】（ドレインは彼/彼女からは中断されにくい）")
    if champ_m:
        line += f" ｜ **移動CC:** 【有】（{per_slot(lambda c: c[1])}）"
    else:
        line += " ｜ **移動CC:** 【無】"
    return line


def _skill_line(slot, name, desc, cd, rng, b, m):
    tags = []
    if b:
        tags.append(f"**【W中断: {'+'.join(sorted(b))}】**")
    if m:
        tags.append(f"**【移動: {'+'.join(sorted(m))}】**")
    meta = []
    if cd and cd != "0":
        meta.append(f"CD {cd}")
    if rng and rng not in ("", "1"):
        meta.append(f"射程 {rng}")
    meta_s = f" `[{'] ['.join(meta)}]`" if meta else ""
    tag_s = (" " + " ".join(tags)) if tags else ""
    return f"- **{slot} {name}:** {desc}{tag_s}{meta_s}"


def _render_builds(builds, valid_items):
    if not builds:
        return "（未登録）"
    parts = []
    for i, variant in enumerate(builds):
        items = " → ".join(
            it if (not valid_items or it in valid_items) else f"⚠️{it}(未確認)"
            for it in variant
        )
        mark = "①②③④⑤"[i] if i < 5 else f"({i + 1})"
        parts.append(f"{mark} {items}")
    return " / ".join(parts)


# fetched_meta の tier_source（U.GG内部キー）-> 表示ラベル
_TIER_SOURCE_LABEL = {
    "platinum_plus": "Platinum+", "emerald_plus": "Emerald+",
    "diamond_plus": "Diamond+", "diamond_two_plus": "Diamond 2+",
    "master_plus": "Master+", "all": "All", "challenger": "Challenger",
}


def _render_fetched_meta(fm):
    """fetched_meta（U.GG 客観統計）-> 1行のMD文字列。無ければ None。

    手動メタ（ビルド/対策/脅威度）とは明確に区別される「外部データ」として出力する。
    LLM が主観(手動)と客観(外部)を混同しないよう、見出しで出典・パッチ・tierを明示。
    """
    if not fm:
        return None
    source = fm.get("source", "U.GG")
    patch = fm.get("patch") or "?"
    tier_src = _TIER_SOURCE_LABEL.get(fm.get("tier_source") or "",
                                      fm.get("tier_source") or "?")
    parts = []
    if fm.get("tier"):
        parts.append(f"Tier: **{fm['tier']}**")
    if fm.get("win_rate") is not None:
        parts.append(f"勝率: **{fm['win_rate']}%**")
    if fm.get("pick_rate") is not None:
        parts.append(f"Pick率: **{fm['pick_rate']}%**")
    if fm.get("ban_rate") is not None:
        parts.append(f"Ban率: **{fm['ban_rate']}%**")
    matches = fm.get("matches")
    suffix = f" (対象試合数: {matches:,})" if isinstance(matches, int) else ""
    return (f"- **【パッチ統計メタ ({source} Patch {patch} {tier_src})】:** "
            + " ｜ ".join(parts) + suffix)


def _render_champion(name, info, role, dd, valid_items):
    """1チャンピオンのMDブロック。-> (block_text, warning or None)。"""
    detail = dd.detail(name)
    if not detail:
        return None, "DDragon に未収録（内部名の綴りを確認してください）"

    ri = info["meta_info"].get(role) or {}
    champ_b, champ_m, skill_cc = _analyze_champion(detail)

    roles_s = " / ".join(info.get("roles", [role]))
    threat = ri.get("threat")
    threat_s = f" ｜ **脅威度({role}):** {_stars(threat)}" if threat is not None else ""

    L = []
    L.append(f"### {detail['name']}（{name}）")
    L.append("")
    L.append(f"**ロール:** {roles_s}{threat_s}")
    L.append(_cc_summary(champ_b, champ_m, skill_cc))

    # W中断CCがある場合のみ、フィドル向けの注意書きを自動挿入
    if champ_b:
        break_slots = [s for s in ["Q", "W", "E", "R", "P"]
                       if s in skill_cc and skill_cc[s][0]]
        names = {sp["slot"]: sp["name"] for sp in detail["spells"]}
        names["P"] = detail["passive"]["name"]
        detail_s = ", ".join(f"{s}({names.get(s, '')})" for s in break_slots)
        L.append(f"⚠️ **フィドル注意:** 敵の {detail_s} はドレイン(W)を中断する。"
                 "W詠唱はこれらを避けるか、敵CCのクールダウン中に。")
    L.append("")

    # スキル（数値はすべて DDragon 由来）
    L.append(f"#### スキル（{dd.version} / ja_JP）")
    L.append(_skill_line("P", detail["passive"]["name"],
                         _clean(detail["passive"]["description"]),
                         "", "", *skill_cc.get("P", (set(), set()))))
    for sp in detail["spells"]:
        desc = _clean(sp["description"]) or _clean(sp["tooltip"])
        L.append(_skill_line(sp["slot"], sp["name"], desc,
                             sp["cooldownBurn"], sp["rangeBurn"],
                             *skill_cc.get(sp["slot"], (set(), set()))))
    L.append("")

    # メタ・対策
    L.append(f"#### メタ・対策（{role}）")
    L.append(f"- **コアビルド:** {_render_builds(ri.get("core_builds"), valid_items)}")
    extra = []
    if ri.get("summoner_spells"):
        extra.append("サモナースペル: " + " / ".join(ri["summoner_spells"]))
    if ri.get("keystone"):
        extra.append("キーストーン: " + ri["keystone"])
    if extra:
        L.append("- " + " ｜ ".join(extra))
    if ri.get("power_spikes"):
        L.append("- **パワースパイク:** " + ", ".join(ri["power_spikes"]))
    fm_line = _render_fetched_meta(ri.get("fetched_meta"))
    if fm_line:
        L.append(fm_line)
    if threat is not None:
        L.append(f"- **脅威度:** {_stars(threat)}")
    tac = ri.get("tactics")
    L.append(f"- **対策（フィドル視点）:** {tac}" if tac
             else "- **対策（フィドル視点）:** （未登録）")
    L.append("")
    L.append("---")
    return "\n".join(L), None


def _render_file(role, dd, champs, valid_items, today):
    """1ロール分のMDファイル全文。-> (text, warnings)。"""
    L = []
    L.append(f"# {ROLE_JA[role]}レーン チャンピオン対策（フィドルスティックス視点）")
    L.append("")
    L.append(f"> **パッチ:** {dd.version} ｜ **生成日:** {today} ｜ "
             "データ元: Riot Data Dragon + 手動メタ + U.GG統計(fetched_meta)")
    L.append(">")
    L.append("> **凡例（LLM向け）:**")
    L.append("> - **W中断CC【有】/【無】:** 敵スキル説明文から、フィドルの W(ドレイン:チャネリング) を中断させるCC")
    L.append(">   （スタン/ノックアップ/沈黙/恐怖/チャーム/挑発/サプレッション/ノックバック/ポリモーフ）を自動検出。")
    L.append("> - **移動CC【有】/【無】:** スネア/スロー等、Wは止まらないが位置取りを制限するCC。")
    L.append("> - **脅威度:** ★1（楽）〜 ★5（非常に厳しい）。フィドル視点。")
    L.append("> - `[CD]` `[射程]` は DDragon の実数値。メタ情報（ビルド・対策）は手動管理。")
    L.append("> - **【パッチ統計メタ（外部データ）】:** U.GG（外部統計サイト）から自動取得した客観データ。")
    L.append(">   手動メタ（ビルド・対策・脅威度）とは独立。LLMのエコーチェンバー（出力循環参照）防止用。")
    L.append("")
    L.append("---")
    L.append("")

    warnings = []
    for name, info in champs:
        block, warn = _render_champion(name, info, role, dd, valid_items)
        if warn:
            warnings.append(f"{role}/{name}: {warn}")
            if block is None:
                continue
        L.append(block)
        L.append("")
    return "\n".join(L), warnings


# ----------------------------------------------------------------------------
# データ読み込み・共通ヘルパ
# ----------------------------------------------------------------------------
def _load_meta():
    path = paths.meta_mapping_path()
    if not os.path.exists(path):
        raise SystemExit(
            f"[error] メタファイルが存在しません: {path}\n"
            "  champion_meta_mapping.json を用意してください。")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _champs_for_role(meta, role):
    """meta から指定ロールに出力するチャンピオンを脅威度降順で取得。"""
    champs = [(n, i) for n, i in meta.items()
              if not n.startswith("_") and role in (i.get("roles") or [])]
    champs.sort(key=lambda ni: (ni[1]["meta_info"].get(role, {}) or {}).get("threat", 3),
                reverse=True)
    return champs


def _write_file(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _print_summary(header, written, skipped=None, warns=None, upload_hint=False):
    """written: [(role, n, path), ...]"""
    print(header)
    for role, n, path in written:
        print(f"  [更新] {role}: {n} 体 -> {os.path.basename(path)}")
    if skipped:
        print("  [変更なし] " + ", ".join(skipped))
    if warns:
        print("\n⚠️ 確認事項（手動アップロード時に確認）:")
        for w in warns:
            print(f"  - {w}")
    if upload_hint and written:
        print("\n→ NotebookLM 再アップロード推奨: " +
              ", ".join(os.path.basename(p) for _, _, p in written))


# ----------------------------------------------------------------------------
# 差分更新（Step 4）
# ----------------------------------------------------------------------------
def _state_path():
    return os.path.join(paths.champion_md_dir(), ".state.json")


def _read_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(version, locale):
    _write_file(_state_path(), json.dumps({
        "last_version": version,
        "last_locale": locale,
        "last_generated": datetime.date.today().isoformat(),
    }, ensure_ascii=False, indent=2))


def _full_cache_path(version, locale):
    return os.path.join(paths.ddragon_cache_dir(), f"{version}_{locale}_championFull.json")


def _load_cached_full(version, locale):
    """キャッシュから指定バージョンの championFull.json を読む（無ければ None）。"""
    p = _full_cache_path(version, locale)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# MD生成に使うフィールドのみ比較する（フォーマット用の差分ノイズを含めない）
_SPELL_FIELDS = ("name", "description", "tooltip", "cooldownBurn",
                 "costBurn", "rangeBurn", "maxrank")


def _spells_changed(o, n):
    os_, ns_ = o.get("spells", []), n.get("spells", [])
    if len(os_) != len(ns_):
        return True
    for a, b in zip(os_, ns_):
        if any(a.get(f) != b.get(f) for f in _SPELL_FIELDS):
            return True
        if (a.get("effectBurn") or []) != (b.get("effectBurn") or []):
            return True
    return False


def _change_reasons(o, n):
    """前回/最新のチャンピオン info を比較し、変更理由のリストを返す（空＝変更なし）。"""
    reasons = []
    if o.get("stats") != n.get("stats"):
        reasons.append("ステータス")
    op, npi = o.get("passive") or {}, n.get("passive") or {}
    if (op.get("name"), op.get("description")) != (npi.get("name"), npi.get("description")):
        reasons.append("パッシブ")
    if _spells_changed(o, n):
        reasons.append("スキル(CD/説明/数値)")
    if (o.get("name"), o.get("title"), o.get("partype"),
        o.get("tags"), o.get("info")) != \
       (n.get("name"), n.get("title"), n.get("partype"),
        n.get("tags"), n.get("info")):
        reasons.append("基本情報")
    return reasons


def compute_changed_champions(old_data, new_data):
    """championFull.data 同士を比較し、変更チャンピオンを分類して返す。
    -> {"added": set, "removed": set, "modified": {name: [reasons]}}"""
    old_keys = set(old_data or {})
    new_keys = set(new_data or {})
    modified = {}
    for k in old_keys & new_keys:
        reasons = _change_reasons(old_data[k], new_data[k])
        if reasons:
            modified[k] = reasons
    return {"added": new_keys - old_keys,
            "removed": old_keys - new_keys,
            "modified": modified}


def _run_full(dd, meta, valid_items, today):
    """全ロールファイルを生成。"""
    out_dir = paths.champion_md_dir()
    written, all_warns = [], []
    for role in ROLES:
        champs = _champs_for_role(meta, role)
        if not champs:
            continue
        text, warns = _render_file(role, dd, champs, valid_items, today)
        path = os.path.join(out_dir, f"{role}_Champions.md")
        _write_file(path, text)
        written.append((role, len(champs), path))
        all_warns.extend(warns)
    _print_summary(f"全件生成（パッチ {dd.version}）:", written, warns=all_warns)
    return written


def _run_diff(dd, meta, valid_items, today):
    """差分更新。前回パッチと比較し、変更のあったロールファイルのみ再生成。
    フォールバックが必要な場合は None を返す（呼び出し元が全件生成へ）。"""
    state = _read_state()
    prev = state.get("last_version")
    cur = dd.version
    if not prev:
        print("[diff] 前回パッチの記録がないため全件生成します。")
        return None
    if prev == cur:
        print(f"[diff] 前回と同一パッチ({cur})のため全件生成します。")
        return None
    old_full = _load_cached_full(prev, dd.locale)
    if old_full is None:
        print(f"[diff] 前回パッチ({prev})のキャッシュがないため全件生成します。")
        return None
    new_full = _load_cached_full(cur, dd.locale)
    if new_full is None:
        print(f"[diff] 現行パッチ({cur})のキャッシュ取得に失敗したため全件生成します。")
        return None

    changes = compute_changed_champions(old_full.get("data"), new_full.get("data"))
    meta_names = {n for n in meta if not n.startswith("_")}
    changed_all = changes["added"] | changes["removed"] | set(changes["modified"])
    changed_in_meta = changed_all & meta_names

    # 変更チャンピオンを含むロールのみ再生成
    affected_roles = set()
    for name in changed_in_meta:
        affected_roles.update(meta[name].get("roles") or [])

    out_dir = paths.champion_md_dir()
    written, all_warns = [], []
    for role in ROLES:
        if role not in affected_roles:
            continue
        champs = _champs_for_role(meta, role)
        if not champs:
            continue
        text, warns = _render_file(role, dd, champs, valid_items, today)
        path = os.path.join(out_dir, f"{role}_Champions.md")
        _write_file(path, text)
        written.append((role, len(champs), path))
        all_warns.extend(warns)
    skipped = [r for r in ROLES if r not in affected_roles]

    # レポート（要件C: 手動アップロード時の確認漏れ防止）
    print(f"[差分更新] {prev} -> {cur}")
    if changes["added"]:
        print(f"  新規チャンピオン: {', '.join(sorted(changes['added']))}")
    if changes["removed"]:
        print(f"  削除: {', '.join(sorted(changes['removed']))}")
    if changes["modified"]:
        print("  変更チャンピオン（★=メタ登録＝出力対象）:")
        for name in sorted(changes["modified"]):
            mark = "★" if name in meta_names else "・"
            print(f"    {mark} {name}: {', '.join(changes['modified'][name])}")
    if changed_in_meta:
        print(f"  メタ内の変更: {', '.join(sorted(changed_in_meta))}")
    else:
        print("  メタ登録チャンピオンに変更はありません（ファイル更新なし）。")
    _print_summary("  ファイル更新:", written, skipped=skipped,
                   warns=all_warns, upload_hint=True)
    return written


# ----------------------------------------------------------------------------
# エントリポイント
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="チャンピオン対策Markdownをロール別に生成")
    ap.add_argument("--version", default="latest", help="DDragonパッチ（既定: latest）")
    ap.add_argument("--locale", default="ja_JP")
    ap.add_argument("--diff", action="store_true",
                    help="差分更新モード: 前回パッチと比較し変更のあったロールファイルのみ再生成")
    args = ap.parse_args()

    dd = ddragon.DDragon(args.version, locale=args.locale, load_full=True)
    if not dd.champion_detail:
        print("[error] championFull.json が取得できませんでした。終了します。")
        return 1

    meta = _load_meta()
    valid_items = set(dd.items.values())  # ビルドのアイテム名検証用
    today = datetime.date.today().isoformat()

    if args.diff:
        did = _run_diff(dd, meta, valid_items, today)
        if did is None:
            _run_full(dd, meta, valid_items, today)
    else:
        _run_full(dd, meta, valid_items, today)

    _write_state(dd.version, dd.locale)  # 次回diffの基準を更新
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
