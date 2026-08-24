@echo off
REM Start the JARVIS Python server and then run Electron (development)
SETLOCAL

REM Activate venv if exists
if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
)

REM Start electron using npm scripts (requires Node + npm + electron installed)
if exist package.json (
  echo Starting Electron (npm start)...
  npm start
) else (
  echo package.json not found. Please run this from the project root where package.json exists.
)

ENDLOCAL
pause
