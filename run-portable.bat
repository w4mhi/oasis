@echo off
REM ==========================================================================
REM OASIS Portable — standalone tools only (no Winlink, no APRS)   [Windows]
REM
REM Runs OASIS in the "portable" profile: the dashboard shows ONLY the features
REM that do not depend on a background service — FCC lookup, net logger, ICS
REM forms, radio references, operating tools, handbook, calculators. Winlink and
REM APRS (and the other daemon-backed cards) are hidden and their server routes
REM disabled. Driven entirely by OASIS_FEATURES, so the suite runs read-mostly
REM straight off a USB stick.
REM
REM Double-click this file. It sets OASIS_FEATURES, opens the default web
REM browser, then hands off to scripts\start-server.bat (bundled Python runtime).
REM ==========================================================================
title OASIS Portable
cd /d "%~dp0"

set "OASIS_FEATURES=fcc,forms,repeaterbook"
if "%OASIS_PORT%"=="" set "OASIS_PORT=8083"

echo.
echo   ----------------------------------------
echo   OASIS Portable - standalone tools only
echo   (FCC lookup, net logger, ICS forms, radio refs, handbook)
echo   No Winlink, No APRS  --^>  http://localhost:%OASIS_PORT%
echo   ----------------------------------------

REM Open the browser after a short delay (the server start below blocks).
start "" /b cmd /c "timeout /t 3 >nul & start http://localhost:%OASIS_PORT%"

REM Hand off to the standard Windows launcher (uses _runtime\windows\python.exe).
REM OASIS_FEATURES / OASIS_PORT are inherited by the called script.
call "%~dp0scripts\start-server.bat"
