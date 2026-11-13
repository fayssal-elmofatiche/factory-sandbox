@echo off
rem fx for Windows PowerShell and cmd: runs the fx script next to this file with Python.
rem In Git Bash, ./fx works as it is.
rem
rem Windows names Python three ways. Take the first that really runs: the py launcher,
rem then python, then python3 (which is often only a shortcut to the Microsoft Store).
setlocal
set "FX_PY="
py -3 -c "import sys" >nul 2>nul && set "FX_PY=py -3"
if not defined FX_PY python -c "import sys" >nul 2>nul && set "FX_PY=python"
if not defined FX_PY python3 -c "import sys" >nul 2>nul && set "FX_PY=python3"
if not defined FX_PY (
  echo fx: no Python found. Install Python 3.10 or later, for example: winget install Python.Python.3.13 1>&2
  exit /b 1
)
%FX_PY% "%~dp0fx" %*
exit /b %ERRORLEVEL%
