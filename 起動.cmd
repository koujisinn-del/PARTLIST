@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\pythonw.exe" goto launch
echo 初回起動のため、プロジェクト環境を準備しています...
call "%~dp0安装开发环境.cmd" --auto
if errorlevel 1 goto failed

:launch
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0launcher_ja.pyw"
exit /b 0

:failed
echo プロジェクト環境を準備できませんでした。
echo 会社で承認された Python 3.12 とパッケージ取得先を確認してください。
pause
exit /b 1
