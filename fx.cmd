@echo off
rem fx for Windows PowerShell and cmd: runs the fx script next to this file with Python.
rem In Git Bash, ./fx works as it is.
python3 "%~dp0fx" %*
