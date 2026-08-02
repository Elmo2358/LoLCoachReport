"""アプリ専用フォントの読み込み。

LoL の Beaufort に近いフリーフォント Cinzel（OFL）をバンドルし、実行時に
プロセス私的に登録（AddFontResourceExW / FR_PRIVATE）して tkinter から名前で
使えるようにする。日本語グリフは OS のフォントリンクで自動補完される
（LoL の日本語版も Beaufort＋和文フォントの組み合わせ）。
"""
import ctypes
import os
import sys

import paths

# Cinzel.ttf の内部ファミリ名（実測）
DISPLAY = "Cinzel"      # 見出し用（Beaufort 代替）
BODY = "Segoe UI"       # 本文用（Spiegel 代替。クリーンなヒューマニストサンス）

_registered = False


def register():
    """バンドルフォントをプロセスに登録（初回のみ）。Tk 生成前に呼ぶ。"""
    global _registered
    if _registered:
        return
    cinzel = os.path.join(paths.fonts_dir(), "Cinzel.ttf")
    if os.path.exists(cinzel) and os.name == "nt":
        try:
            # FR_PRIVATE = 0x10 : システム全体ではなく自プロセスのみ
            ctypes.windll.gdi32.AddFontResourceExW(cinzel, 0x10, 0)
        except Exception:
            pass
    _registered = True


def display():
    """見出し用フォントファミリ（Cinzel 未登録時は Georgia にフォールバック）。"""
    return DISPLAY


def body():
    return BODY
