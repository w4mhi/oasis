@echo off
REM Convert ICS 213 PDF → base64 JS template
REM Usage: convert-template.bat [path\to\ics_forms_213.pdf]
setlocal
set PDF=%~1
if "%PDF%"=="" set PDF=ics_forms_213.pdf
if not exist "%PDF%" (echo ERROR: PDF not found: %PDF% & exit /b 1)
python3 --version >nul 2>&1 || (echo ERROR: python3 not found & exit /b 1)
if exist ics213-template.js (copy /y ics213-template.js ics213-template.js.bak & echo Backed up ics213-template.js)
python3 -c "
import base64, datetime, sys
pdf, out = sys.argv[1], 'ics213-template.js'
data = open(pdf,'rb').read()
b64 = base64.b64encode(data).decode()
ts = datetime.datetime.now().isoformat()[:19]
content = '// ICS 213 PDF Template\n// Generated: '+ts+'\n// Source: '+pdf+'\nconst ICS213_PDF_B64 = \"'+b64+'\";\n'
open(out,'w').write(content)
print('PDF:', len(data)//1024, 'KB')
print('Base64:', len(b64)//1024, 'KB')
print('Written:', out)
" "%PDF%"
echo Done.
endlocal
