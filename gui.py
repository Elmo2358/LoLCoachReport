"""elmo2358 個人用 LoL コーチングレポート GUI（tkinter / Hextech 風ダークテーマ）。

PyInstaller で exe 化することを想定。ゲームIDを入力して「取得して出力」を押すと、
選択した形式（クリップボード / JSON / CSV / Markdown）で結果を保存する。
"""
import json
import os
import sys
import threading
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import paths
import core
import csv_export
import config
import appfonts
import ddragon
import generate_md
import meta_loader
import counter_merge
import init_meta_json
from counter_merge import CounterMergeError
from riot_client import RiotClient, SERVER_TO_PLATFORM

# バンドルフォント（Cinzel）をプロセスに登録（Tk 生成前）
appfonts.register()

# ---- LoL / Hextech カラーパレット ----
BG       = "#0A1428"   # 深紺（ベース）
PANEL    = "#1E2328"   # パネル
PANEL2   = "#2A2D34"   # 明るいパネル
GOLD     = "#C8AA6E"   # ヘクスティーク金
GOLD_DK  = "#785A28"   # 暗い金（ボーダー）
TEXT     = "#F0E6D2"   # 羊皮紙（標準文字）
MUTED    = "#A09B8C"   # 薄い金灰
ACCENT   = "#0397AB"   # ヘクスティーク青
ACCENT2  = "#0AC8B9"   # 明るい青緑
WIN_GRN  = "#1FE5A0"   # 勝利
LOSE_RED = "#E84057"   # 敗北/エラー

HEAD = (appfonts.display(), 16, "bold")
SECTION = (appfonts.display(), 11, "bold")
BODY = (appfonts.body(), 10)
SEMIBOLD = ("Segoe UI Semibold", 10)
SMALL = (appfonts.body(), 9)
MONO = ("Consolas", 9)


def apply_theme(root):
    """Hextech 風ダーク＋ゴールドの ttk テーマを適用。"""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=TEXT, font=BODY, borderwidth=0)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT, font=BODY)
    style.configure("Title.TLabel", font=HEAD, foreground=GOLD, background=BG)
    style.configure("Sub.TLabel", font=SMALL, foreground=MUTED, background=BG)
    style.configure("Section.TLabel", font=SECTION, foreground=GOLD, background=PANEL)
    style.configure("Field.TLabel", background=BG, foreground=TEXT, font=BODY)
    style.configure("Hint.TLabel", background=BG, foreground=MUTED, font=SMALL)
    style.configure("Status.TLabel", background=BG, foreground=ACCENT2, font=BODY)
    style.configure("Gold.TSeparator", background=GOLD)

    # Entry
    style.configure("Hex.TEntry", fieldbackground=PANEL, foreground=TEXT,
                    bordercolor=GOLD_DK, lightcolor=GOLD_DK, darkcolor=GOLD_DK,
                    insertcolor=TEXT, padding=5)
    style.map("Hex.TEntry", bordercolor=[("focus", GOLD)],
              lightcolor=[("focus", GOLD)], darkcolor=[("focus", GOLD_DK)])

    # 通常ボタン（金縁・暗金背景）
    style.configure("Hex.TButton", background=GOLD_DK, foreground=TEXT,
                    font=SEMIBOLD, borderwidth=1, focusthickness=3,
                    focuscolor=GOLD, padding=(14, 7))
    style.map("Hex.TButton",
              background=[("active", GOLD), ("pressed", "#5A4418"), ("disabled", PANEL2)],
              foreground=[("active", BG), ("disabled", MUTED)],
              bordercolor=[("focus", GOLD)])

    # 主ボタン（ゴールド塗り）
    style.configure("Primary.TButton", background=GOLD, foreground=BG,
                    font=("Segoe UI Semibold", 10, "bold"), borderwidth=0, padding=(18, 9))
    style.map("Primary.TButton",
              background=[("active", ACCENT2), ("pressed", GOLD_DK), ("disabled", "#3C3C41")],
              foreground=[("active", BG), ("disabled", MUTED)])

    # チェックボックス
    style.configure("Hex.TCheckbutton", background=BG, foreground=TEXT, font=BODY,
                    focuscolor=BG)
    style.map("Hex.TCheckbutton",
              background=[("active", BG)],
              indicatorbackground=[("selected", ACCENT), ("active", PANEL2), ("!selected", PANEL)],
              indicatorforeground=[("selected", BG)],
              foreground=[("active", GOLD)])

    # スクロールバー
    style.configure("Vertical.TScrollbar", background=PANEL, troughcolor=BG,
                    bordercolor=BG, arrowcolor=GOLD, gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", GOLD_DK)])


class _NullStd:
    def write(self, *args, **kwargs):
        pass

    def flush(self):
        pass


def _gold_rule(parent, thickness=2):
    line = tk.Frame(parent, height=thickness, bg=GOLD, bd=0, highlightthickness=0)
    line.pack(fill="x")
    return line


class App:
    def __init__(self, root):
        self.root = root
        apply_theme(root)
        _, _, full, _, _ = config.get_my_account()
        self.my_full = full or "自分"
        root.title(f"LoL コーチングレポート ({self.my_full})")
        root.geometry("920x740")
        root.minsize(740, 580)
        root.configure(bg=BG)
        try:
            if os.path.exists(paths.icon_path()):
                root.iconbitmap(paths.icon_path())
        except Exception:
            pass
        self.result = None
        self.paste_btn = None
        self.patch_btn = None
        self._build_ui()
        self._load_settings()

    # ---------------- UI 構築 ----------------
    def _panel(self, parent, title):
        """ゴールド見出し付きのパネルフレームを返す（中身は呼び出し側で pack/grid）。"""
        outer = ttk.Frame(parent, style="Panel.TFrame", padding=(12, 8, 12, 10))
        outer.pack(fill="x", padx=10, pady=(6, 4))
        ttk.Label(outer, text=title, style="Section.TLabel").pack(anchor="w")
        tk.Frame(outer, height=1, bg=GOLD_DK, bd=0, highlightthickness=0).pack(fill="x", pady=(2, 8))
        body = ttk.Frame(outer, style="Panel.TFrame")
        body.pack(fill="x")
        return body

    def _build_ui(self):
        # ---- ヘッダー ----
        header = ttk.Frame(self.root, padding=(14, 12, 14, 4))
        header.pack(fill="x")
        ttk.Label(header, text="LoL コーチングレポート", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"  {self.my_full}  •  Gemini 分析・コーチング",
                  style="Sub.TLabel").pack(side="left", padx=(6, 0), pady=(6, 0))
        _gold_rule(self.root)

        content = ttk.Frame(self.root)
        content.pack(fill="x")

        # ---- 試合情報パネル ----
        p1 = self._panel(content, "試合情報")
        p1.columnconfigure(1, weight=1)
        ttk.Label(p1, text="APIキー", style="Field.TLabel").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=3)
        self.key_var = tk.StringVar()
        ttk.Entry(p1, textvariable=self.key_var, show="*", style="Hex.TEntry").grid(
            row=0, column=1, columnspan=3, sticky="we", pady=3)
        ttk.Label(p1, text="試合ID", style="Field.TLabel").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=3)
        self.match_var = tk.StringVar()
        ttk.Entry(p1, textvariable=self.match_var, style="Hex.TEntry").grid(row=1, column=1, sticky="we", pady=3)
        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(p1, text="空欄時は自分の直近試合を自動取得", style="Hex.TCheckbutton",
                        variable=self.auto_var).grid(row=1, column=2, columnspan=2, sticky="w", padx=(8, 0), pady=3)
        ttk.Label(p1, text="分析対象", style="Field.TLabel").grid(row=2, column=0, sticky="e", padx=(0, 8), pady=3)
        self.player_var = tk.StringVar()
        ttk.Entry(p1, textvariable=self.player_var, style="Hex.TEntry").grid(row=2, column=1, sticky="we", pady=3)
        ttk.Label(p1, text=f"空欝=自分 {self.my_full}", style="Hint.TLabel").grid(
            row=2, column=2, columnspan=2, sticky="w", padx=(8, 0), pady=3)
        self.coach_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(p1, text="コーチング指示プロンプトを付与（Geminiにそのまま貼れる）",
                        style="Hex.TCheckbutton", variable=self.coach_var).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 2))

        # ---- 出力形式パネル ----
        p2 = self._panel(content, "出力形式（複数選択可）")
        self.out_clip = tk.BooleanVar(value=True)
        self.out_md = tk.BooleanVar(value=False)
        self.out_json = tk.BooleanVar(value=False)
        self.out_csv = tk.BooleanVar(value=False)
        ttk.Checkbutton(p2, text="クリップボードにコピー（Markdown・Geminiに貼る）",
                        style="Hex.TCheckbutton", variable=self.out_clip).grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(p2, text="Markdown(.md)", style="Hex.TCheckbutton",
                        variable=self.out_md).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(p2, text="JSON(.json)", style="Hex.TCheckbutton",
                        variable=self.out_json).grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(p2, text="CSV(.csv)", style="Hex.TCheckbutton",
                        variable=self.out_csv).grid(row=1, column=1, sticky="w", padx=4, pady=2)

        # ---- ボタン ----
        bf = ttk.Frame(self.root, padding=(10, 8, 10, 2))
        bf.pack(fill="x")
        self.run_btn = ttk.Button(bf, text="取得して出力", style="Primary.TButton", command=self._on_run)
        self.run_btn.pack(side="left", padx=(0, 8))
        ttk.Button(bf, text="サンプルでテスト（オフライン）", style="Hex.TButton", command=self._on_sample).pack(side="left")
        self.patch_btn = ttk.Button(bf, text="最新パッチへ更新", style="Hex.TButton",
                                    command=self._on_patch_update)
        self.patch_btn.pack(side="left", padx=(12, 0))

        # ---- ステータス ----
        self.status_var = tk.StringVar(value="準備完了。試合IDを入力（または空欄で自分の直近）して「取得して出力」。")
        ttk.Label(self.root, textvariable=self.status_var, style="Status.TLabel").pack(anchor="w", padx=14, pady=(2, 4))

        # ---- チャンピオン対策メンテナンス（折りたたみ可能） ----
        # クリックで展開/折りたたみ。普段は畳んで結果プレビューの縦スペースを確保。
        cm_outer = ttk.Frame(self.root, style="Panel.TFrame", padding=(12, 8, 12, 10))
        cm_outer.pack(fill="x", padx=10, pady=(6, 4))
        self.cm_header = ttk.Label(
            cm_outer, text="▶ チャンピオン対策メンテナンス（クリックで展開）",
            style="Section.TLabel", cursor="hand2")
        self.cm_header.pack(anchor="w")
        self.cm_header.bind("<Button-1>", self._toggle_cm)
        tk.Frame(cm_outer, height=1, bg=GOLD_DK, bd=0, highlightthickness=0).pack(fill="x", pady=(2, 8))
        self.cm_body = ttk.Frame(cm_outer, style="Panel.TFrame")
        # デフォルトは折りたたみ（pack しない → プレビュー領域を広く確保）
        self.paste_text = tk.Text(self.cm_body, height=8, wrap="word", font=MONO, bg=PANEL, fg=TEXT,
                                  insertbackground=TEXT, borderwidth=0, highlightthickness=1,
                                  highlightbackground=GOLD_DK, highlightcolor=GOLD,
                                  selectbackground=ACCENT, selectforeground=BG,
                                  padx=10, pady=8, spacing1=1, spacing3=1)
        self.paste_text.pack(fill="x")
        cm_row = ttk.Frame(self.cm_body, style="Panel.TFrame")
        cm_row.pack(fill="x", pady=(6, 0))
        self.paste_btn = ttk.Button(cm_row, text="解析してJSONを更新", style="Hex.TButton",
                                    command=self._on_paste_apply)
        self.paste_btn.pack(side="left")
        self.paste_status_var = tk.StringVar(
            value="AI 出力の ```lol-counter ブロックを貼り付け → ボタンで対策JSONとMDを更新。")
        ttk.Label(cm_row, textvariable=self.paste_status_var, style="Hint.TLabel").pack(
            side="left", padx=(10, 0))

        # ---- プレビュー ----
        self._panel(self.root, "結果プレビュー")
        pf = ttk.Frame(self.root, style="Panel.TFrame")
        pf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.preview = tk.Text(pf, wrap="word", font=MONO, bg=PANEL, fg=TEXT,
                               insertbackground=TEXT, borderwidth=0, highlightthickness=1,
                               highlightbackground=GOLD_DK, highlightcolor=GOLD,
                               selectbackground=ACCENT, selectforeground=BG,
                               padx=10, pady=8, spacing1=1, spacing3=1)
        self.preview.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(pf, command=self.preview.yview, style="Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.preview.configure(yscrollcommand=sb.set)
        self.preview.configure(state="disabled")
        # プレビュー用タグ
        self.preview.tag_configure("head", foreground=GOLD, font=(appfonts.display(), 11, "bold"))
        self.preview.tag_configure("key", foreground=ACCENT2, font=("Consolas", 9, "bold"))
        self.preview.tag_configure("table", foreground=MUTED)

    # ---------------- 設定（APIキー） ----------------
    def _load_settings(self):
        key = ""
        try:
            with open(paths.settings_path(), encoding="utf-8") as f:
                key = json.load(f).get("api_key", "")
        except Exception:
            pass
        self.key_var.set(key or os.getenv("RIOT_API_KEY", ""))

    def _save_settings(self):
        try:
            with open(paths.settings_path(), "w", encoding="utf-8") as f:
                json.dump({"api_key": self.key_var.get().strip()}, f)
        except Exception:
            pass

    # ---------------- 実行 ----------------
    def _normalize_match_id(self, raw):
        """数値のみ（例: 595633237）は自分のホームサーバーの前置詞を補完（JP1_595633237）。"""
        raw = (raw or "").strip()
        if raw and "_" not in raw and raw.isdigit():
            _, _, _, _, server = config.get_my_account()
            prefix = SERVER_TO_PLATFORM.get(server, "JP1")
            return f"{prefix}_{raw}"
        return raw

    def _on_run(self):
        api_key = self.key_var.get().strip()
        match_id = self._normalize_match_id(self.match_var.get())
        if not api_key:
            messagebox.showwarning("APIキー", "APIキーを入力してください。\nRiot Developer Portal で取得（24時間で期限切れ）。")
            return
        if not match_id and not self.auto_var.get():
            messagebox.showwarning("試合ID", "試合IDを入力するか「自分の直近試合を自動取得」を有効にしてください。")
            return
        self._save_settings()
        self._set_busy(True, "取得中... (API呼び出し)")
        threading.Thread(target=self._worker_real, args=(api_key, match_id), daemon=True).start()

    def _on_sample(self):
        self._set_busy(True, "サンプルで生成中...")
        threading.Thread(target=self._worker_sample, daemon=True).start()

    def _make_progress(self):
        def progress(msg):
            self.root.after(0, lambda m=msg: self.status_var.set(m))
        return progress

    def _worker_real(self, api_key, match_id):
        try:
            result = core.process(api_key, match_id=match_id or None,
                                  auto_me=self.auto_var.get(), lang="ja",
                                  coach=self.coach_var.get(),
                                  player=self.player_var.get().strip() or None,
                                  progress=self._make_progress())
            self.root.after(0, lambda: self._on_done(result))
        except core.ProcessError as e:
            self.root.after(0, lambda: self._on_error(str(e)))
        except Exception as e:
            self.root.after(0, lambda: self._on_error(f"予期しないエラー: {e}"))

    def _worker_sample(self):
        try:
            sp = paths.sample_path()
            with open(sp, encoding="utf-8") as f:
                match = json.load(f)
            _, server = RiotClient.parse_region(match["metadata"]["matchId"])
            result = core.process_match_data(match, server, lang="ja",
                                             coach=self.coach_var.get(),
                                             player=self.player_var.get().strip() or None,
                                             progress=self._make_progress())
            self.root.after(0, lambda: self._on_done(result, sample=True))
        except Exception as e:
            self.root.after(0, lambda: self._on_error(f"サンプル処理エラー: {e}"))

    def _render_preview(self, text):
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        for line in text.split("\n"):
            tag = None
            if line.startswith("#"):
                tag = "head"
            elif line.startswith("|"):
                tag = "table"
            elif line.lstrip().startswith("- **") or line.startswith("**チーム"):
                tag = "key"
            if tag:
                self.preview.insert("end", line + "\n", tag)
            else:
                self.preview.insert("end", line + "\n")
        self.preview.configure(state="disabled")

    def _on_done(self, result, sample=False):
        self.result = result
        self._set_busy(False, "")
        msgs = []
        mid = result["match_id"] or "match"
        md = result["markdown"]

        self._render_preview(md)

        if self.out_clip.get():
            self.root.clipboard_clear()
            self.root.clipboard_append(md)
            msgs.append("クリップボードにコピー")

        if self.out_md.get():
            path = filedialog.asksaveasfilename(
                title="Markdownで保存", defaultextension=".md",
                initialfile=f"{mid}.md", initialdir=paths.reports_dir(),
                filetypes=[("Markdown", "*.md"), ("すべて", "*.*")])
            if path:
                self._write(path, md, "utf-8")
                msgs.append(f"MD: {path}")

        if self.out_json.get():
            path = filedialog.asksaveasfilename(
                title="JSONで保存", defaultextension=".json",
                initialfile=f"{mid}.json", initialdir=paths.matches_dir(),
                filetypes=[("JSON", "*.json"), ("すべて", "*.*")])
            if path:
                self._write(path, json.dumps(result["ir"], ensure_ascii=False, indent=2), "utf-8")
                msgs.append(f"JSON: {path}")

        if self.out_csv.get():
            path = filedialog.asksaveasfilename(
                title="CSVで保存", defaultextension=".csv",
                initialfile=f"{mid}.csv", initialdir=paths.reports_dir(),
                filetypes=[("CSV", "*.csv"), ("すべて", "*.*")])
            if path:
                self._write(path, csv_export.to_csv(result["ir"]), "utf-8-sig")
                msgs.append(f"CSV: {path}")

        focal = result.get("focal")
        who = f"対象: {focal['name']}（{focal['champion']}）" if focal else "全体分析"
        status = f"✔ 完了（{who}）。" + (" / ".join(msgs) if msgs else "出力形式未選択（プレビューのみ）")
        if sample:
            status = "[サンプル] " + status
        self.status_var.set(status)

    def _on_error(self, msg):
        self._set_busy(False, "")
        self.status_var.set("✕ エラー")
        messagebox.showerror("エラー", msg, parent=self.root)

    # ---------------- チャンピオン対策メンテナンス（機能2） ----------------
    def _toggle_cm(self, event=None):
        if self.cm_body.winfo_ismapped():
            self.cm_body.pack_forget()
            self.cm_header.configure(text="▶ チャンピオン対策メンテナンス（クリックで展開）")
        else:
            self.cm_body.pack(fill="x")
            self.cm_header.configure(text="▼ チャンピオン対策メンテナンス（クリックで折りたたみ）")

    def _on_paste_apply(self):
        raw = self.paste_text.get("1.0", "end-1c")
        if not raw.strip():
            messagebox.showwarning("入力なし", "```lol-counter ブロックを貼り付けてください。")
            return
        self._set_busy(True, "対策ブロックを解析中...")
        threading.Thread(target=self._worker_paste_apply, args=(raw,), daemon=True).start()

    def _prepare_meta_and_dd(self, progress):
        """[worker thread] meta_mapping と DDragon(latest, full) を準備して dd を返す。
        失敗時は CounterMergeError を送出（呼出元で catch 済）。"""
        if not os.path.exists(paths.meta_mapping_path()):
            progress("初回のため対策データを初期化中（数十秒）...")
            init_meta_json.main()
        progress("Data Dragon を準備中（初回は数十秒）...")
        dd = ddragon.DDragon("latest", locale="ja_JP", load_full=True)
        if not dd.champion_detail:
            raise CounterMergeError("Data Dragon のスキル詳細が取得できませんでした（ネットワークを確認）。")
        return dd

    def _worker_paste_apply(self, raw):
        try:
            progress = self._make_progress()
            dd = self._prepare_meta_and_dd(progress)
            result = counter_merge.apply_counter_block(raw, dd=dd, progress=progress)
            msg = (f"{len(result.updated)} 件のチャンピオン対策を更新しました: "
                   + ", ".join(f"{n}({r})" for n, r in result.updated))
            if result.skipped_role:
                msg += f"\nロール不一致でスキップ: {len(result.skipped_role)} 件"
            if result.unknown_champions:
                msg += f"\n未知のチャンピオン(スキップ): {', '.join(result.unknown_champions)}"
            self.root.after(0, lambda: self._on_paste_done(msg, result))
        except CounterMergeError as e:
            self.root.after(0, lambda: self._on_error(str(e)))
        except Exception as e:
            self.root.after(0, lambda: self._on_error(f"予期しないエラー: {e}"))

    def _on_paste_done(self, msg, result):
        self._set_busy(False, "")
        self.paste_status_var.set(f"✔ 更新完了（{len(result.updated)} 件）")
        self.status_var.set(f"✔ 対策を {len(result.updated)} 件更新しました")
        messagebox.showinfo("対策メンテナンス完了", msg, parent=self.root)

    # ---------------- パッチ更新（機能3） ----------------
    def _on_patch_update(self):
        if not messagebox.askyesno(
                "パッチ更新",
                "最新パッチのデータを取得し、U.GG 統計と対策Markdownを再生成します。\n"
                "（ネットワーク接続が必要。数十秒〜1分程度かかります）\n続行しますか？"):
            return
        self._set_busy(True, "最新パッチを確認中...")
        threading.Thread(target=self._worker_patch_update, daemon=True).start()

    def _worker_patch_update(self):
        try:
            progress = self._make_progress()
            dd = self._prepare_meta_and_dd(progress)
            version = dd.version
            progress("U.GG 統計を取得中（失敗時はスキップ）...")
            key2name = {str(k): v for k, v in (dd.champion_keys or {}).items()}
            role_stats, params, note = meta_loader.gather(
                meta_loader.ROLES, use_cache=False, key2name=key2name)
            updated_fetched = 0
            if role_stats:
                progress("fetched_meta を JSON に反映中...")
                meta_loader.update_meta_json(
                    role_stats, params or {}, datetime.date.today().isoformat())
                updated_fetched = sum(len(v) for v in role_stats.values())
            progress("対策Markdownを再生成中...")
            meta = generate_md._load_meta()
            valid_items = set((dd.items or {}).values())
            today = datetime.date.today().isoformat()
            generate_md._run_full(dd, meta, valid_items, today)
            generate_md._write_state(version, dd.locale)
            self.root.after(0, lambda: self._on_patch_done(version, updated_fetched, note))
        except CounterMergeError as e:
            self.root.after(0, lambda: self._on_error(str(e)))
        except Exception as e:
            self.root.after(0, lambda: self._on_error(f"予期しないエラー: {e}"))

    def _on_patch_done(self, version, updated_fetched, note):
        self._set_busy(False, "")
        self.status_var.set(f"✔ パッチ {version} に更新しました（fetched_meta {updated_fetched} 件）")
        messagebox.showinfo("パッチ更新完了",
            f"パッチ {version} に更新しました。\n"
            f"fetched_meta: {updated_fetched} 件\n"
            f"U.GG: {note}\n"
            f"対策Markdown を再生成しました。",
            parent=self.root)

    # ---------------- ユーティリティ ----------------
    def _set_busy(self, busy, text):
        state = "disabled" if busy else "normal"
        for btn in (self.run_btn, self.paste_btn, self.patch_btn):
            if btn is not None:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass
        if text:
            self.status_var.set(text)

    @staticmethod
    def _write(path, content, encoding):
        with open(path, "w", encoding=encoding, newline="") as f:
            f.write(content)


def _fix_stdio():
    if getattr(sys, "frozen", False):
        if sys.stdout is None:
            sys.stdout = _NullStd()
        if sys.stderr is None:
            sys.stderr = _NullStd()


def _set_app_user_model_id(app_id="elmo2358.LoLCoachReport"):
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def main():
    _fix_stdio()
    _set_app_user_model_id()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
