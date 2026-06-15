@echo off
REM ────────────────────────────────────────────────────────────────────────────
REM convert-template.bat
REM Converts an ICS 309 AcroForm PDF into the base64 JS template used by
REM ics-309.html at runtime.
REM
REM Usage:
REM   convert-template.bat                        (uses default: ics_forms_309.pdf)
REM   convert-template.bat path\to\my-form.pdf
REM
REM Output: ics309-template.js  (overwrites; previous version backed up)
REM ────────────────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion

if "%~1"=="" (
    set "PDF=ics_forms_309.pdf"
) else (
    set "PDF=%~1"
)

set "OUT=ics309-template.js"
set "BACKUP=ics309-template.js.bak"

REM ── Check PDF exists ─────────────────────────────────────────────────────────
if not exist "%PDF%" (
    echo ERROR: PDF file not found: %PDF%
    echo Usage: convert-template.bat [path\to\ics_forms_309.pdf]
    exit /b 1
)

REM ── Check python3 ────────────────────────────────────────────────────────────
where python3 >nul 2>&1
if errorlevel 1 (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python is required but not found. Install from https://python.org
        exit /b 1
    )
    set "PYTHON=python"
) else (
    set "PYTHON=python3"
)

REM ── Backup existing template ─────────────────────────────────────────────────
if exist "%OUT%" (
    copy /y "%OUT%" "%BACKUP%" >nul
    echo Backed up existing template ^-^> %BACKUP%
)

REM ── Convert ──────────────────────────────────────────────────────────────────
echo Converting: %PDF%

%PYTHON% -c ^
"import sys, base64, os, datetime; ^
pdf=sys.argv[1]; out=sys.argv[2]; ^
b64=base64.b64encode(open(pdf,'rb').read()).decode(); ^
ts=datetime.datetime.now().strftime('%%Y-%%m-%%d %%H:%%M'); ^
src=os.path.basename(pdf); ^
open(out,'w',encoding='utf-8').write('// ICS 309 PDF Template -- embedded as base64\n// Source : '+src+'\n// Generated: '+ts+'\n// The variable ICS309_PDF_B64 is loaded by ics-309.html at runtime.\nconst ICS309_PDF_B64 = \\''+b64+'\\';\n'); ^
print('Written :',out,'(',os.path.getsize(out),bytes,len(b64),'base64 chars)')" ^
"%PDF%" "%OUT%"

if errorlevel 1 (
    echo ERROR: Conversion failed.
    exit /b 1
)

echo Done. Reload ics-309.html in your browser to pick up the new template.
endlocal
