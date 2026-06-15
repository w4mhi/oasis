#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────────
# convert-template.sh
# Converts an ICS 205 AcroForm PDF into the base64 JS template used by
# ics-205.html at runtime.
#
# Usage:
#   ./convert-template.sh                        # uses default: ics_forms_205.pdf
#   ./convert-template.sh path/to/my-form.pdf    # use a specific file
#
# Output: ics205-template.js  (overwrites; previous version backed up)
# ────────────────────────────────────────────────────────────────────────────

set -euo pipefail

PDF="${1:-ics_forms_205.pdf}"
OUT="ics205-template.js"
BACKUP="${OUT}.bak"

# ── Checks ────────────────────────────────────────────────────────────────────
if [[ ! -f "$PDF" ]]; then
  echo "ERROR: PDF file not found: $PDF"
  echo "Usage: $0 [path/to/ics_forms_205.pdf]"
  exit 1
fi

# Verify it's a PDF (starts with %PDF)
MAGIC=$(head -c 4 "$PDF" 2>/dev/null || true)
if [[ "$MAGIC" != "%PDF" ]]; then
  echo "ERROR: '$PDF' does not appear to be a valid PDF file."
  exit 1
fi

# Check python3 is available
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 is required but not found."
  exit 1
fi

# ── Backup existing template ──────────────────────────────────────────────────
if [[ -f "$OUT" ]]; then
  cp "$OUT" "$BACKUP"
  echo "Backed up existing template → $BACKUP"
fi

# ── Convert ───────────────────────────────────────────────────────────────────
PDF_SIZE=$(wc -c < "$PDF" | tr -d ' ')
echo "Converting: $PDF  (${PDF_SIZE} bytes)"

python3 - "$PDF" "$OUT" <<'PYEOF'
import sys, base64, os, datetime

pdf_path = sys.argv[1]
out_path  = sys.argv[2]

with open(pdf_path, 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()

ts     = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
source = os.path.basename(pdf_path)

with open(out_path, 'w', encoding='utf-8') as out:
    out.write(f'// ICS 205 PDF Template — embedded as base64\n')
    out.write(f'// Source : {source}\n')
    out.write(f'// Generated: {ts}\n')
    out.write(f'// The variable ICS205_PDF_B64 is loaded by ics-205.html at runtime.\n')
    out.write(f"const ICS205_PDF_B64 = '{b64}';\n")

js_size = os.path.getsize(out_path)
print(f"Written : {out_path}  ({js_size:,} bytes, {len(b64):,} base64 chars)")
PYEOF

echo "Done. Reload ics-205.html in your browser to pick up the new template."
