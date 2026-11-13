@echo off
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js 22.12 or newer is required. Install Node.js first.
  pause
  exit /b 1
)
set PUPPETEER_SKIP_DOWNLOAD=1
npm install
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)
echo WhatsApp Web dependencies installed.
echo Install Microsoft Edge or Google Chrome if neither is already available.
pause
