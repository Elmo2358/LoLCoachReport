"""elmo2358 個人用 LoL コーチングレポート GUI（tkinter）。

PyInstaller で exe 化することを想定。ゲームIDを入力して「取得して出力」を押すと、
選択した形式（クリップボード / JSON / CSV / Markdown）で結果を保存する。
"""
import json
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import paths
import core
import csv_export
import config
from riot_client import SERVER_TO_PLATFORM


class _NullStd:
    """--windowed（コンソール無し）実行時、sys.stdout/stderr が None になる対策。"""

    def write(self, *args, **kwargs):
        pass

    def flush(self):
        pass


class App:
    def __init__(self, root):
        self.root = root
        _, _, full, _, _ = config.get_my_account()
        self.my_full = full or "自分"
        root.title(f"LoL コーチングレポート ({self.my_full})")
        root.geometry("820x680")
        root.minsize(680, 540)
        try:
            root.iconbitmap(default=False)  # 念のためリセット
            if os.path.exists(paths.icon_path()):
                root.iconbitmap(paths.icon_path())
        except Exception:
            pass
        self.result = None
        self._build_ui()
        self._load_settings()

    # ---------------- UI 構築 ----------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        f = ttk.Frame(self.root, padding=10)
        f.pack(fill="x")

        # APIキー
        ttk.Label(f, text="APIキー（期限切れなら再生成して貼り付け）:").grid(row=0, column=0, sticky="w", **pad)
        self.key_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.key_var, width=52, show="*").grid(row=0, column=1, columnspan=3, sticky="we", **pad)

        # 試合ID
        ttk.Label(f, text="試合ID:").grid(row=1, column=0, sticky="w", **pad)
        self.match_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.match_var, width=28).grid(row=1, column=1, sticky="we", **pad)
        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="空欄時は自分の直近試合を自動取得", variable=self.auto_var).grid(row=1, column=2, columnspan=2, sticky="w", **pad)

        # 分析対象
        ttk.Label(f, text="分析対象:").grid(row=2, column=0, sticky="w", **pad)
        self.player_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.player_var, width=28).grid(row=2, column=1, sticky="we", **pad)
        ttk.Label(f, text=f"（空欝=自分 {self.my_full}／チャンプ名や名前で上書き）").grid(row=2, column=2, columnspan=2, sticky="w", **pad)

        # コーチ
        self.coach_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="コーチング指示プロンプトを付与（Geminiにそのまま貼れる）", variable=self.coach_var).grid(row=3, column=0, columnspan=4, sticky="w", **pad)

        # 出力形式
        ttk.Label(f, text="出力形式（複数選択可）:").grid(row=4, column=0, sticky="nw", **pad)
        of = ttk.Frame(f)
        of.grid(row=4, column=1, columnspan=3, sticky="w", **pad)
        self.out_clip = tk.BooleanVar(value=True)
        self.out_md = tk.BooleanVar(value=False)
        self.out_json = tk.BooleanVar(value=False)
        self.out_csv = tk.BooleanVar(value=False)
        ttk.Checkbutton(of, text="クリップボードにコピー", variable=self.out_clip).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(of, text="Markdown(.md)", variable=self.out_md).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(of, text="JSON(.json)", variable=self.out_json).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(of, text="CSV(.csv)", variable=self.out_csv).grid(row=1, column=1, sticky="w")

        # ボタン
        bf = ttk.Frame(f)
        bf.grid(row=5, column=0, columnspan=4, pady=8)
        self.run_btn = ttk.Button(bf, text="取得して出力", command=self._on_run)
        self.run_btn.grid(row=0, column=0, padx=4)
        ttk.Button(bf, text="サンプルでテスト（オフライン）", command=self._on_sample).grid(row=0, column=1, padx=4)

        # ステータス
        self.status_var = tk.StringVar(value="準備完了。試合IDを入力（または空欄で自分の直近）して「取得して出力」。")
        ttk.Label(self.root, textvariable=self.status_var, foreground="#444").pack(anchor="w", padx=12)

        # プレビュー
        ttk.Label(self.root, text="結果プレビュー:").pack(anchor="w", padx=12, pady=(6, 0))
        pf = ttk.Frame(self.root)
        pf.pack(fill="both", expand=True, padx=12, pady=6)
        self.preview = tk.Text(pf, wrap="word", font=("Consolas", 9))
        self.preview.pack(side="left", fill="both", expand=True)
        ttk.Scrollbar(pf, command=self.preview.yview).pack(side="right", fill="y")
        self.preview.configure(yscrollcommand=lambda *a: None)

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
            from riot_client import RiotClient
            _, server = RiotClient.parse_region(match["metadata"]["matchId"])
            result = core.process_match_data(match, server, lang="ja",
                                             coach=self.coach_var.get(),
                                             player=self.player_var.get().strip() or None,
                                             progress=self._make_progress())
            self.root.after(0, lambda: self._on_done(result, sample=True))
        except Exception as e:
            self.root.after(0, lambda: self._on_error(f"サンプル処理エラー: {e}"))

    def _on_done(self, result, sample=False):
        self.result = result
        self._set_busy(False, "")
        msgs = []
        mid = result["match_id"] or "match"
        md = result["markdown"]

        # プレビュー
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", md)

        # クリップボード
        if self.out_clip.get():
            self.root.clipboard_clear()
            self.root.clipboard_append(md)
            msgs.append("クリップボードにコピー")

        # Markdown
        if self.out_md.get():
            path = filedialog.asksaveasfilename(
                title="Markdownで保存", defaultextension=".md",
                initialfile=f"{mid}.md", initialdir=paths.reports_dir(),
                filetypes=[("Markdown", "*.md"), ("すべて", "*.*")])
            if path:
                self._write(path, md, "utf-8")
                msgs.append(f"MD保存: {path}")

        # JSON（処理済みIR）
        if self.out_json.get():
            path = filedialog.asksaveasfilename(
                title="JSONで保存", defaultextension=".json",
                initialfile=f"{mid}.json", initialdir=paths.matches_dir(),
                filetypes=[("JSON", "*.json"), ("すべて", "*.*")])
            if path:
                self._write(path, json.dumps(result["ir"], ensure_ascii=False, indent=2), "utf-8")
                msgs.append(f"JSON保存: {path}")

        # CSV
        if self.out_csv.get():
            path = filedialog.asksaveasfilename(
                title="CSVで保存", defaultextension=".csv",
                initialfile=f"{mid}.csv", initialdir=paths.reports_dir(),
                filetypes=[("CSV", "*.csv"), ("すべて", "*.*")])
            if path:
                self._write(path, csv_export.to_csv(result["ir"]), "utf-8-sig")
                msgs.append(f"CSV保存: {path}")

        focal = result.get("focal")
        who = f"対象: {focal['name']}（{focal['champion']}）" if focal else "全体分析"
        status = f"✅ 完了 ({who})。" + (" / ".join(msgs) if msgs else "出力形式未選択（プレビューのみ）")
        self.status_var.set(status)
        if sample:
            self.status_var.set("[サンプル] " + status)

    def _on_error(self, msg):
        self._set_busy(False, "")
        self.status_var.set("❌ エラー")
        messagebox.showerror("エラー", msg)

    # ---------------- ユーティリティ ----------------
    def _set_busy(self, busy, text):
        self.run_btn.configure(state="disabled" if busy else "normal")
        if text:
            self.status_var.set(text)

    @staticmethod
    def _write(path, content, encoding):
        with open(path, "w", encoding=encoding, newline="") as f:
            f.write(content)


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass


def _fix_stdio():
    if getattr(sys, "frozen", False):
        if sys.stdout is None:
            sys.stdout = _NullStd()
        if sys.stderr is None:
            sys.stderr = _NullStd()


def _set_app_user_model_id(app_id="elmo2358.LoLCoachReport"):
    """Windows のタスクバーがデフォルト(Tk/Python)アイコンを使わないよう、
    プロセスに固有の AppUserModelID を設定する（ウィンドウ生成より前）。"""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def main():
    _fix_stdio()
    _load_env()
    _set_app_user_model_id()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
