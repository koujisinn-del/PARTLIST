@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)" >nul 2>nul
    if not errorlevel 1 goto use_py
)
set "PYTHON312="
where python >nul 2>nul
if errorlevel 1 goto missing_python
for /f "delims=" %%P in ('where python') do (
    "%%P" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON312=%%P"
)
if defined PYTHON312 goto use_python
:missing_python
echo Python 3.12 が見つかりません。会社で承認された 64 ビット版を確認してください。
goto failed

:use_py
py -3.12 setup_source.py --launch ja %*
if errorlevel 1 goto failed
exit /b 0

:use_python
"%PYTHON312%" setup_source.py --launch ja %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo 環境の確認または起動に失敗しました。上のエラーを撮影してください。
pause
exit /b 1
