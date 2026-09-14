@echo off
setlocal
cd /d "%~dp0"
set "AUTO_MODE="
if /I "%~1"=="--auto" set "AUTO_MODE=1"
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python
echo Python was not found. Ask company IT to provide Python 3.12 64-bit.
if not defined AUTO_MODE pause
exit /b 1

:use_py
py -3.12 setup_source.py
goto finished

:use_python
python setup_source.py

:finished
if errorlevel 1 goto failed
echo Source environment is ready. Run the application launcher.
if not defined AUTO_MODE pause
exit /b 0

:failed
echo Setup failed. See the message above; no administrator access is required.
echo If Python 3.12 is not registered with py, run your approved Python with setup_source.py.
if not defined AUTO_MODE pause
exit /b 1
