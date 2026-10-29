@echo off
rem Fallback launcher if SyeonPet.exe fails
cd /d "%~dp0"
start "" "%~dp0pythonw.exe" "%~dp0main.py"
