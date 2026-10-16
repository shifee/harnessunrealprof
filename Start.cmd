@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
where py >nul 2>nul
if errorlevel 1 goto python
py -3 -m unreal_harness start %*
goto done
:python
where python >nul 2>nul
if errorlevel 1 goto missing
python -m unreal_harness start %*
goto done
:missing
echo Python 3.9+ is required. Install Python and reopen Start.cmd.
pause
exit /b 2
:done
if errorlevel 1 pause
endlocal
