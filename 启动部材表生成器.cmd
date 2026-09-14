@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\pythonw.exe" goto launch
echo First run: preparing the project environment...
call "%~dp0安装开发环境.cmd" --auto
if errorlevel 1 goto failed

:launch
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app_launcher.pyw"
exit /b 0

:failed
echo The project environment could not be prepared.
echo Confirm that approved Python 3.12 and the company package source are available.
pause
exit /b 1
