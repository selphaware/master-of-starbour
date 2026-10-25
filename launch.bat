@echo off
rem Master of Starbour - launch from source (KIT-08). Double-click or run from cmd/PowerShell.
rem No execution policy involved: this calls the venv interpreter directly.
rem   launch.bat                 normal play
rem   launch.bat --seed 1234     pin the world seed
rem   launch.bat --config my.ini use another config file
rem Closing this window ends the game (TL, BUS-06).
setlocal
cd /d "%~dp0"
if not exist "MOS\Scripts\python.exe" (
  echo Interpreter not found at %~dp0MOS\Scripts\python.exe - create the venv MOS first ^(docs\SETUP.md step 1^).
  exit /b 1
)
if not exist "starbour\__main__.py" (
  echo starbour package not built yet - run milestone M1 first ^(specs\M1.md^).
  exit /b 1
)
set PYTHONUTF8=1
"MOS\Scripts\python.exe" -m starbour %*
endlocal
