@echo off
setlocal
if not exist "%~dp0.venv\Scripts\pythonw.exe" goto missing
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0material_trial.pyw"
exit /b 0
:missing
echo Project Python environment is not ready. Please run the environment installer.
pause
exit /b 1
