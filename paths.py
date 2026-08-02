"""アプリのデータ保存先を解決する。

PyInstaller で --onefile 化した exe では %LOCALAPPDATA%\\lolalytics を、
開発中（未凍結）は ./data を使う。バンドルしたリソース（サンプル試合）は
凍結時は sys._MEIPASS から読む。
"""
import os
import sys


def is_frozen():
    return getattr(sys, "frozen", False)


def app_dir():
    if is_frozen():
        base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                            "lolalytics")
    else:
        base = os.path.join(os.getcwd(), "data")
    os.makedirs(base, exist_ok=True)
    return base


def _subdir(name):
    d = os.path.join(app_dir(), name)
    os.makedirs(d, exist_ok=True)
    return d


def ddragon_cache_dir():
    return _subdir("ddragon_cache")


def matches_dir():
    return _subdir("matches")


def reports_dir():
    return _subdir("reports")


def settings_path():
    return os.path.join(app_dir(), "settings.json")


def sample_path():
    """同梱サンプル試合。凍結時は _MEIPASS、開発時は ./data から。"""
    if is_frozen():
        return os.path.join(sys._MEIPASS, "data", "sample_match.json")
    return os.path.join(os.getcwd(), "data", "sample_match.json")


def icon_path():
    """アプリアイコン。凍結時は _MEIPASS、開発時は ./icon.ico。"""
    if is_frozen():
        return os.path.join(sys._MEIPASS, "icon.ico")
    return os.path.join(os.getcwd(), "icon.ico")


def fonts_dir():
    """バンドルフォント。凍結時は _MEIPASS/fonts、開発時は ./fonts。"""
    if is_frozen():
        return os.path.join(sys._MEIPASS, "fonts")
    return os.path.join(os.getcwd(), "fonts")
