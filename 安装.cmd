@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
if errorlevel 1 (
  echo Setup failed. See the message above.
) else (
  echo Setup complete. Load the browser-extension folder in Chrome or Edge.
)
pause
