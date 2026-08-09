"""U.GG のSSR-HTMLから客観的パッチ統計を取得し、champion_meta_mapping.json の
fetched_meta を更新する（Phase 2a: 勝率 / ピック率 / Ban率 / Tier）。

【目的】エコーチェンバー（LLM出力を手動メタに追記し再度LLMに読ませる循環参照）
を防ぐため、LLMの外部にある客観データを「真実」として注入する。本モジュール
だけが fetched_meta を書き、generate_md.py は読むだけ（LLMは産出に関与しない）。

【取得元】U.GG のロール別ティアリストページ（SSR-HTML）。React アプリが初期状態
として <script id="reactn-preloaded-state"> 内に champion_ranking データセットを
丸ごと埋め込んでおり、1ページ取得で全5ロール・全チャンピオンの統計が揃う。
stats2.u.gg の静的JSONは S3 ポリシーで 403 されるため使わない。

【フェイルソフト】U.GG取得・パース失敗時は fetched_meta を更新せず警告のみ。
既存の手動メタ(tactics/core_builds/threat)は一切上書きせず、既存パイプラインは
正常動作し続ける。JSON書き込みは tmp + os.replace で排他化し破損を防ぐ。

【tier について】U.GG はtierラベル(S+/S/A...)を生データでは持たず、各エントリの
tier.stdevs（勝率のピック率重み付け標準偏差スコア）からフロントエンドで算出する。
本モジュールは stdevs をそのまま tier_stdevs として保存（客観指標）するとともに、
文書化された独自ヒューリスティックで tier ラベルを導出する。U.GG公式のカットオフ
は非公開のため、tier ラベルは参考値。

使い方:
    python meta_loader.py              # 全ロール取得して fetched_meta を更新
    python meta_loader.py --role Mid    # 特定ロールのみ（--role は複数回指定可）
    python meta_loader.py --diff        # キャッシュがあれば再取得せずそれを使用
    python meta_loader.py --dry-run     # JSONを書き換えず結果だけ表示
"""
import argparse
import datetime
import json
import os
import re
import time

import requests

import ddragon
import paths

ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]
# 当アプリのロール -> U.GG tier-list の URL slug / データ内 role キー
ROLE_TO_SLUG = {
    "Top": "top-lane", "Jungle": "jungle", "Mid": "mid-lane",
    "ADC": "adc", "Support": "support",
}
ROLE_TO_UGGKEY = {
    "Top": "top", "Jungle": "jungle", "Mid": "mid",
    "ADC": "adc", "Support": "support",
}
UGG_ALL_URL = "https://u.gg/lol/tier-list"          # 全ロール統合ページ
UGG_ROLE_URL = "https://u.gg/lol/{slug}-tier-list"  # ロール別ページ（フォールバック用）

# 取得優先tier。承認既定は Platinum+ だが、U.GG がページに埋め込むのは
# emerald_plus が中心。platinum_plus が埋め込まれていればそれを優先し、
# 無ければ存在するものを採用して tier_source に記録する。
PREFER_TIERS = ("platinum_plus", "emerald_plus", "diamond_plus", "all")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HDR = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9",
}
FETCH_TIMEOUT = 25
# フォールバック時のロール別ページ間のポライト遅延（秒）
POLITE_DELAY = 1.5

# U.GG 埋め込みデータセットキー（ReactN が API レスポンスを URL 単位でキャッシュ）。
# キーは stats2.u.gg の champion_ranking URL。patch は "16_15" 形式（DDragon の
# major.minor と一致）。形式の詳細は _DATASET_KEY_RE を参照。
_DATASET_KEY_RE = re.compile(
    r'"https://stats2\.u\.gg/lol/[\d.]+/champion_ranking/'
    r"(\w+)/(\d+_\d+)/(\w+)/(\w+)/([\d.]+)\.json\"\s*:\s*"
)


# ----------------------------------------------------------------------------
# tier 導出
# ----------------------------------------------------------------------------
def _tier_from_stdevs(stdevs):
    """U.GG の tier.stdevs（ピック率重み付け標準偏差スコア）からtierラベルを導出。

    正負は「平均より強い/弱い」、絶対値は「偏差の大きさ」。カットオフは U.GG公式
    非公開のため文書化された独自マッピング（参考値）。None のときは None。
    """
    if stdevs is None:
        return None
    if stdevs >= 1.0:
        return "S+"
    if stdevs >= 0.5:
        return "S"
    if stdevs >= 0.0:
        return "A"
    if stdevs >= -0.5:
        return "B"
    if stdevs >= -1.0:
        return "C"
    return "D"


# ----------------------------------------------------------------------------
# HTML 取得・データセット抽出
# ----------------------------------------------------------------------------
def _bracket_match_json(text, start):
    """text[start] が '{' の想定。対応する '}' までの部分文字列を返す。

    文字列リテラル（'/'/"/`）とバックススラッシュエスケープを考慮し、ネストした
    {} の対応を正しく追う。終端がない場合は None。
    """
    depth = 0
    i = start
    instr = False
    q = ""
    n = len(text)
    while i < n:
        c = text[i]
        if instr:
            if c == "\\":
                i += 2
                continue
            if c == q:
                instr = False
        else:
            if c in ('"', "'", "`"):
                instr = True
                q = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def _fetch_html(url):
    """U.GG ページHTMLを取得。失敗時は例外を上位に伝播（呼び出し元でフェイルソフト）。"""
    r = requests.get(url, headers=HDR, timeout=FETCH_TIMEOUT)
    r.raise_for_status()
    return r.text


def _find_datasets(html):
    """HTML から埋め込み統計データセットを抽出する。

    -> [(params, payload_dict), ...]
       params = {region, patch, queue, tier, apiver}（patch は '16_15' 形式）
    """
    out = []
    for m in _DATASET_KEY_RE.finditer(html):
        params = {
            "region": m.group(1),
            "patch": m.group(2),
            "queue": m.group(3),
            "tier": m.group(4),
            "apiver": m.group(5),
        }
        brace = html.find("{", m.end())
        if brace == -1:
            continue
        obj = _bracket_match_json(html, brace)
        if not obj:
            continue
        try:
            payload = json.loads(obj)
        except json.JSONDecodeError:
            continue
        out.append((params, payload))
    return out


def _select_dataset(datasets):
    """PREFER_TIERS の順で優先データセットを選ぶ。無ければ最初のもの。"""
    for pref in PREFER_TIERS:
        for params, payload in datasets:
            if params["tier"] == pref:
                return params, payload
    return datasets[0] if datasets else (None, None)


def _extract_win_rates(payload):
    """payload -> {role_key: [entry, ...]}。構造: payload['data']['win_rates']。"""
    try:
        return payload["data"]["win_rates"]
    except (KeyError, TypeError):
        return {}


# ----------------------------------------------------------------------------
# 正規化
# ----------------------------------------------------------------------------
def _sanitize_pct(x):
    """pick/ban 率は U.GG 側で既にパーセント(0-100)表記。丸めて負値を0に抑止。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, v), 2)


def _normalize_role(win_rates, ugg_role_key, key2name):
    """1ロール分の win_rates を {internal_name: stats} に正規化する。

    -> (result, unresolved_count)
    """
    entries = win_rates.get(ugg_role_key) or []
    result = {}
    unresolved = 0
    for e in entries:
        cid = str(e.get("champion_id"))
        name = key2name.get(cid)
        if not name:
            unresolved += 1
            continue
        stdevs = (e.get("tier") or {}).get("stdevs")
        try:
            stdevs = round(float(stdevs), 3) if stdevs is not None else None
        except (TypeError, ValueError):
            stdevs = None
        try:
            wr = round(float(e.get("win_rate", 0.0)), 1)
        except (TypeError, ValueError):
            wr = None
        try:
            matches = int(e.get("matches", 0))
        except (TypeError, ValueError):
            matches = 0
        result[name] = {
            "tier": _tier_from_stdevs(stdevs),
            "win_rate": wr,
            "pick_rate": _sanitize_pct(e.get("pick_rate")),
            "ban_rate": _sanitize_pct(e.get("ban_rate")),
            "tier_stdevs": stdevs,
            "matches": matches,
        }
    return result, unresolved


# ----------------------------------------------------------------------------
# キャッシュ
# ----------------------------------------------------------------------------
def _cache_files():
    d = paths.ugg_cache_dir()
    return sorted(
        (os.path.join(d, f) for f in os.listdir(d)
         if f.startswith("ugg_") and f.endswith(".json")),
        key=os.path.getmtime, reverse=True)


def _newest_cache():
    files = _cache_files()
    if not files:
        return None
    try:
        with open(files[0], encoding="utf-8") as f:
            return json.load(f), files[0]
    except Exception:
        return None


def _write_cache(params, role_stats, fetched_at):
    fname = f"ugg_{params['patch']}_{params['tier']}.json"
    path = os.path.join(paths.ugg_cache_dir(), fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"params": params, "fetched_at": fetched_at,
                   "role_stats": role_stats}, f, ensure_ascii=False, indent=2)
    return path


# ----------------------------------------------------------------------------
# データ収集（キャッシュ優先 + フェイルソフト）
# ----------------------------------------------------------------------------
def _fetch_one(url, key2name):
    """1ページを取得し (params, role_stats_all_roles) を返す。失敗時は None。"""
    html = _fetch_html(url)
    datasets = _find_datasets(html)
    if not datasets:
        return None
    params, payload = _select_dataset(datasets)
    if payload is None:
        return None
    win_rates = _extract_win_rates(payload)
    all_stats = {}
    for role, ugg_key in ROLE_TO_UGGKEY.items():
        stats, _ = _normalize_role(win_rates, ugg_key, key2name)
        all_stats[role] = stats
    return params, all_stats


def gather(roles, use_cache, key2name):
    """U.GG 統計を収集し {role: {internal_name: stats}} と params を返す。

    戻り値: (role_stats, params, note)
      role_stats: 要求ロールのみ
      params: 取得元メタ（patch/tier/region/queue）。取得失敗時は ({}, None, ...)
      note: キャッシュ使用/フォールバック等の状況文字列
    フェイルソフト: 全取得失敗時は ({}, None, 警告) を返す（例外を出さない）。
    """
    fetched_at = datetime.date.today().isoformat()

    if use_cache:
        c = _newest_cache()
        if c:
            cache, cpath = c
            params = cache.get("params", {})
            rs = {r: (cache.get("role_stats", {}).get(r) or {}) for r in roles}
            note = (f"キャッシュ使用: {os.path.basename(cpath)} "
                    f"(patch={params.get('patch','?')} fetched={cache.get('fetched_at','?')})")
            return rs, params, note
        print("[diff] キャッシュがないため新規取得します。")

    # 1件目: 全ロール統合ページ（1リクエストで全ロール分が埋め込み）
    try:
        got = _fetch_one(UGG_ALL_URL, key2name)
    except Exception as e:
        got = None
        print(f"[警告] 全ロールページ取得失敗({type(e).__name__}): {str(e)[:120]}")

    if got:
        params, all_stats = got
        missing = [r for r in roles if not all_stats.get(r)]
    else:
        params, all_stats, missing = None, {}, list(roles)

    # フォールバック: 統合ページで取得できなかったロールを個別取得
    if missing:
        for role in missing:
            slug = ROLE_TO_SLUG[role]
            url = UGG_ROLE_URL.format(slug=slug)
            try:
                got2 = _fetch_one(url, key2name)
            except Exception as e:
                print(f"[警告] {role} ページ取得失敗({type(e).__name__}): {str(e)[:120]}")
                continue
            if not got2:
                continue
            p2, all_stats2 = got2
            if not params:
                params = p2  # 統合ページ失敗時はロール別のparamsを採用
            all_stats[role] = all_stats2[role]
            time.sleep(POLITE_DELAY)

    if not all_stats or not any(all_stats.values()):
        return {}, None, "[error] U.GG から統計を取得できませんでした（fetched_meta は更新しません）"

    # キャッシュ保存（全ロール分）
    cpath = _write_cache(params, all_stats, fetched_at) if params else None
    rs = {r: all_stats.get(r, {}) for r in roles}
    src = f"新規取得: {os.path.basename(cpath)}" if cpath else "新規取得"
    note = (f"{src} (patch={params.get('patch','?')} tier={params.get('tier','?')} "
            f"region={params.get('region','?')})")
    return rs, params, note


# ----------------------------------------------------------------------------
# fetched_meta 書き込み
# ----------------------------------------------------------------------------
def update_meta_json(role_stats, params, fetched_at, dry_run=False):
    """champion_meta_mapping.json の各 meta_info[role].fetched_meta を上書き更新する。

    手動メタ(tactics/core_builds/threat 等)は一切触れない。fetched_meta だけを
    （存在すれば同位置で）置き換える。書き込みは tmp + os.replace で排他化。
    -> 更新したチャンピオン×ロール件数
    """
    path = paths.meta_mapping_path()
    if not os.path.exists(path):
        print(f"[error] メタファイルが存在しません: {path}")
        return 0
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)

    patch = (params or {}).get("patch", "").replace("_", ".") or None
    fm_base = {
        "source": "U.GG",
        "patch": patch,
        "region": (params or {}).get("region"),
        "tier_source": (params or {}).get("tier"),
        "queue": (params or {}).get("queue"),
        "fetched_at": fetched_at,
    }
    fm_base = {k: v for k, v in fm_base.items() if v is not None}

    updated = 0
    skipped_noentry = 0
    skipped_role = 0
    for role, stats_by_name in role_stats.items():
        for name, st in stats_by_name.items():
            if name.startswith("_"):
                continue
            champ = meta.get(name)
            if not champ:
                skipped_noentry += 1
                continue
            # チャンピオンの担当ロール（roles 配列）のときだけ書き込む。
            # 担当外ロール（例: ヤスオの Jungle）の統計はノイズになるため書かない。
            if role not in (champ.get("roles") or []):
                skipped_role += 1
                continue
            ri = champ.setdefault("meta_info", {}).setdefault(role, {})
            ri["fetched_meta"] = {**fm_base, **st}
            updated += 1

    if dry_run:
        print(f"[dry-run] fetched_meta 更新予定: {updated} 件"
              f"（メタ未登録: {skipped_noentry} / 担当外ロール: {skipped_role} をスキップ）"
              " → 書き込みスキップ")
        return updated

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return updated


# ----------------------------------------------------------------------------
# エントリポイント
# ----------------------------------------------------------------------------
def main():
    """CLI: U.GG 統計を取得して champion_meta_mapping.json の fetched_meta を更新する。"""
    ap = argparse.ArgumentParser(
        description="U.GG から客観的パッチ統計を取得し fetched_meta を更新する")
    ap.add_argument("--role", choices=ROLES, action="append",
                    help="特定ロールのみ（複数回指定可）。省略時は全ロール")
    ap.add_argument("--diff", action="store_true",
                    help="キャッシュがあれば再取得せずそれを使用する")
    ap.add_argument("--dry-run", action="store_true",
                    help="JSONを書き換えず取得結果だけを表示する")
    args = ap.parse_args()

    roles = args.role or ROLES
    fetched_at = datetime.date.today().isoformat()

    # DDragon key -> 内部名（U.GG champion_id = DDragon key）
    print("[1/3] DDragon championFull を読み込みます...")
    dd = ddragon.DDragon("latest", load_full=True)
    if not dd.champion_keys:
        print("[error] DDragon championFull が取得できません。終了します。")
        return 1
    key2name = {str(k): v for k, v in dd.champion_keys.items()}
    print(f"      DDragon {dd.version}: {len(key2name)} 体の key->名前 解決")

    print(f"[2/3] U.GG 統計を取得します（ロール: {', '.join(roles)}）...")
    role_stats, params, note = gather(roles, args.diff, key2name)
    print(f"      {note}")
    if not role_stats:
        print("[error] 取得データが空のため fetched_meta を更新しません。")
        return 1

    # 結果サマリ（dry-run でも表示）
    for role in roles:
        n = len(role_stats.get(role, {}))
        # 上位/下位サンプル
        rs = role_stats.get(role, {})
        sample = sorted(rs.items(), key=lambda kv: kv[1]["win_rate"] or 0, reverse=True)
        top3 = ", ".join(f"{nm}({(s['win_rate'] or 0):.1f}%/{s['tier']})"
                         for nm, s in sample[:3])
        print(f"      {role}: {n} 体 | 勝率上位 {top3}")

    print("[3/3] fetched_meta を更新します...")
    n = update_meta_json(role_stats, params or {}, fetched_at, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"      完了: {n} 件の fetched_meta を更新 -> {os.path.basename(paths.meta_mapping_path())}")
        print("      ※ MDへの反映は generate_md.py を実行してください（Step 3 で対応）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
