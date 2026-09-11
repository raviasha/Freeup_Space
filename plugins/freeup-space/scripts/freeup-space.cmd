@echo off
setlocal DisableDelayedExpansion
if exist "%~dp0..\.runtime-path" goto use_runtime
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 "%~dp0freeup_space.py" %*
exit /b %errorlevel%
:use_python
python "%~dp0freeup_space.py" %*
exit /b %errorlevel%
:use_runtime
rem The installer writes UTF-8. Read it as data, never as a batch command.
for /f "tokens=2 delims=:" %%C in ('chcp') do set "FREEUP_CODEPAGE=%%C"
chcp 65001 >nul
set "FREEUP_RUNTIME="
set /p "FREEUP_RUNTIME=" < "%~dp0..\.runtime-path"
if defined FREEUP_CODEPAGE chcp %FREEUP_CODEPAGE% >nul
"%FREEUP_RUNTIME%" --cli %*
exit /b %errorlevel%
