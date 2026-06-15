#!/usr/bin/env bash
# Convert ICS 214 PDF → base64 JS template
# Usage: ./convert-template.sh [path/to/ics_forms_214.pdf]
set -euo pipefail
PDF="${1:-ics_forms_214.pdf}"
OUT="ics214-template.js"
if [[ ! -f "$PDF" ]]; then
  echo "ERROR: PDF not found: $PDF" >&2; exit 1
fi
MAGIC=$(head -c 4 "$PDF")
if [[ "$MAGIC" != "%PDF" ]]; then
  echo "ERROR: $PDF does not appear to be a valid PDF (magic bytes: $MAGIC)" >&2; exit 1
fi
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found" >&2; exit 1; }
[[ -f "$OUT" ]] && cp "$OUT" "${OUT}.bak" && echo "Backed up $OUT → ${OUT}.bak"
python3 - "$PDF" "$OUT" <<'PYEOF'
import base64, datetime, sys
pdf, out = sys.argv[1], sys.argv[2]
data = open(pdf,'rb').read()
b64 = base64.b64encode(data).decode()
ts = datetime.datetime.now().isoformat()[:19]
content = f"// ICS 214 PDF Template\n// Generated: {ts}\n// Source: {pdf}\nconst ICS214_PDF_B64 = \"{b64}\";\n"
open(out,'w').write(content)
print(f"  PDF size : {len(data)//1024} KB")
print(f"  Base64   : {len(b64)//1024} KB")
print(f"  Written  : {out}")
PYEOF
echo "Done."
