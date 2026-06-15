@echo off
REM Convert ICS 214 PDF → base64 JS template
REM Usage: convert-template.bat [path\to\ics_forms_214.pdf]
setlocal
set PDF=%~1
if "%PDF%"=="" set PDF=ics_forms_214.pdf
if not exist "%PDF%" (echo ERROR: PDF not found: %PDF% & exit /b 1)
python3 --version >nul 2>&1 || (echo ERROR: python3 not found & exit /b 1)
if exist ics214-template.js (copy /y ics214-template.js ics214-template.js.bak & echo Backed up ics214-template.js)
python3 -c "
import base64, datetime, sys
pdf, out = sys.argv[1], 'ics214-template.js'
data = open(pdf,'rb').read()
b64 = base64.b64encode(data).decode()
ts = datetime.datetime.now().isoformat()[:19]
content = '// ICS 214 PDF Template\n// Generated: '+ts+'\n// Source: '+pdf+'\nconst ICS214_PDF_B64 = \"'+b64+'\";\n'
open(out,'w').write(content)
print('PDF:', len(data)//1024, 'KB')
print('Base64:', len(b64)//1024, 'KB')
print('Written:', out)
" "%PDF%"
echo Done.
endlocal
