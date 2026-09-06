@echo off
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 "%~dp0freeup_space.py" %*
exit /b %errorlevel%
:use_python
python "%~dp0freeup_space.py" %*
exit /b %errorlevel%
