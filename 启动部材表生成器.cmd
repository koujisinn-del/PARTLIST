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
echo 找不到可用的 Python 3.12。请确认公司批准的 64 位 Python 3.12 已安装。
goto failed

:use_py
py -3.12 setup_source.py --launch zh %*
if errorlevel 1 goto failed
exit /b 0

:use_python
"%PYTHON312%" setup_source.py --launch zh %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo 环境检查或软件启动失败。请拍下上方错误信息。
pause
exit /b 1
