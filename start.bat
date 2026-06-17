@echo off
:: OASIS - Off-grid Amateur Station Information Suite — Windows launcher
:: Double-click this file from the USB drive to start.

setlocal enabledelayedexpansion

:: ── Resolve suite root ───────────────────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
set "SERVER_DIR=%SCRIPT_DIR%server"
set "WHEELS_DIR=%SERVER_DIR%\wheels"
set "VENV_DIR=%SCRIPT_DIR%.venv"

echo.
echo   OASIS — Starting...
echo.

:: ── Find Python 3.9+ ─────────────────────────────────────────────────────────
set "PYTHON="
for %%C in (python3 python python3.12 python3.11 python3.10 python3.9) do (
    if not defined PYTHON (
        where %%C >nul 2>&1 && (
            for /f "delims=" %%V in ('%%C -c "import sys; print(sys.version_info >= (3,9))" 2^>nul') do (
                if "%%V"=="True" set "PYTHON=%%C"
            )
        )
    )
)

if not defined PYTHON (
    echo.
    echo   ERROR: Python 3.9+ not found.
    echo   Download it from https://python.org
    echo   Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%V in ('!PYTHON! --version 2^>^&1') do echo   Using: %%V

:: ── Create virtualenv if not present ─────────────────────────────────────────
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo   Creating virtual environment...
    !PYTHON! -m venv "%VENV_DIR%"
)

set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

:: ── Install Flask from bundled wheels (offline, idempotent) ──────────────────
echo   Installing dependencies...
"%VENV_PIP%" install ^
    --quiet ^
    --no-index ^
    --find-links "%WHEELS_DIR%" ^
    flask >nul 2>&1

:: ── Launch server ─────────────────────────────────────────────────────────────
echo   Starting server...
echo.
cd /d "%SERVER_DIR%"
"%VENV_PYTHON%" app.py

echo.
echo   Server stopped. Press any key to close.
pause >nul
