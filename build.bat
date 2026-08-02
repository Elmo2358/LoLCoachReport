@echo off
REM LoL コーチングレポート GUI を単一 exe にビルド
REM 使い方: ダブルクリック または `build.bat`
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller をインストール中...
    pip install pyinstaller
)

pyinstaller --onefile --windowed --name LoLCoachReport ^
    --icon icon.ico ^
    --add-data "data\sample_match.json;data" ^
    --add-data "icon.ico;." ^
    --add-data "fonts;fonts" ^
    --clean --noconfirm gui.py

REM アカウント設定（MY_*）をexeと同じ場所に配置し、自動取得・強調表示を有効化
if exist .env copy /Y .env dist\.env >nul

echo.
if exist "dist\LoLCoachReport.exe" (
    echo ビルド成功: dist\LoLCoachReport.exe
) else (
    echo ビルド失敗。上記ログを確認してください。
)
pause
