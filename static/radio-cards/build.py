#!/usr/bin/env python3
"""
radio-cards/build.py

Reads radio-cards.csv where:
  - Row 0       : headers  → "Operation", Radio1, Radio2, ...
  - Section rows: col 0 has a label, all radio cols empty  → rendered as section divider
  - Data rows   : col 0 = operation name, cols 1+ = steps per radio

Generates:
  - radio-cards/<slug>.html    one page per radio column
  - Updates the Radio Cards <ul> in ../../index.html

Run: ./build.py  (from radio-cards/ or anywhere)
"""

import csv
import html
import re
import sys
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
CSV_FILE   = SCRIPT_DIR / "radio-cards.csv"
OUT_DIR    = SCRIPT_DIR
CSS_PATH   = "../../css/common.css"
MAIN_INDEX = SCRIPT_DIR.parent.parent / "index.html"


# ── helpers ───────────────────────────────────────────────────────────────────

def read_csv(path: Path) -> list:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def to_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ── per-radio page ─────────────────────────────────────────────────────────────

def build_radio_page(radio: str, col: int, rows: list) -> str:
    """Generate one radio page. Returns the output filename."""
    slug     = to_slug(radio)
    filename = f"{slug}.html"

    tbody_lines = []
    for row in rows:
        op     = row[0].strip() if len(row) > 0 else ""
        steps  = row[col].strip() if len(row) > col else ""

        # Section divider: col 0 has a label, all radio cols empty
        all_empty = not any((row[c].strip() if len(row) > c else "")
                            for c in range(1, len(rows[0]) + 1) if c != 0)
        if all_empty and op:
            tbody_lines.append(
                f'<tr class="row-section"><td colspan="2">{html.escape(op)}</td></tr>'
            )
            continue

        # Skip rows where both op and steps are empty
        if not op and not steps:
            continue

        dim = ' class="empty-steps"' if not steps else ""
        steps_cell = html.escape(steps) if steps else '<span class="na">—</span>'
        tbody_lines.append(
            f"<tr{dim}>"
            f'<td class="op">{html.escape(op)}</td>'
            f'<td class="steps">{steps_cell}</td>'
            f"</tr>"
        )

    tbody = "\n        ".join(tbody_lines)

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(radio)}</title>
<link rel="stylesheet" href="{CSS_PATH}">
<style>
.wrap {{ max-width: 1280px; }}
.op    {{ color: var(--amber); font-size: .82rem; white-space: nowrap;
         width: 220px; vertical-align: top; padding-top: 7px; }}
.steps {{ color: var(--text); font-size: .82rem; vertical-align: top;
         white-space: pre-wrap; line-height: 1.55; }}
tr.row-section td {{
  background: var(--panel-2);
  color: var(--text-dim);
  font-size: .6rem;
  text-transform: uppercase;
  letter-spacing: .12em;
  padding: 6px 8px 5px;
  border-bottom: 1px solid var(--border);
}}
tr.empty-steps .op {{ color: var(--text-dim); }}
.na {{ color: var(--border); }}
@media print {{
  body {{ background: #fff; color: #000; }}
  header {{ background: #fff !important; border-color: #ccc !important; }}
  header h1, header p {{ color: #000 !important; }}
  .actions {{ display: none !important; }}
  .tbl-wrap {{ overflow: visible; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ border: 1px solid #ccc !important; }}
  tr.row-section td {{ background: #eee !important; color: #444 !important; }}
  .op {{ color: #555 !important; }}
  .steps {{ color: #000 !important; }}
  .na {{ color: #aaa !important; }}
}}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <a class="home-link" href="/">⌂ HOME</a>
    <h1>{html.escape(radio)}</h1>
    <p>Radio reference card</p>
  </header>

  <div class="actions">
    <button class="sbtn" onclick="window.print()" title="Print this reference card">Print</button>
    <button class="sbtn" onclick="document.getElementById('csvImport').click()" title="Import a two-column CSV (Operation, Steps) to replace the table">Import CSV</button>
    <input type="file" id="csvImport" accept=".csv,text/csv,text/plain" style="display:none" onchange="importCSV(this)">
    <button class="sbtn" onclick="savePage()" title="Download this card as a standalone HTML file">Save</button>
  </div>

  <div class="sec">
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>Operation</th><th>Steps</th></tr></thead>
        <tbody>
        {tbody}
        </tbody>
      </table>
    </div>
  </div>

</div>
<script>
'use strict';

// ── Minimal CSV parser (handles double-quoted fields with embedded commas) ──
function parseCSV(text) {{
  const rows = [];
  const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  for (const line of lines) {{
    if (!line.trim()) continue;
    const fields = [];
    let cur = '', inQ = false;
    for (let i = 0; i < line.length; i++) {{
      const c = line[i];
      if (inQ) {{
        if (c === '"' && line[i+1] === '"') {{ cur += '"'; i++; }}
        else if (c === '"') {{ inQ = false; }}
        else {{ cur += c; }}
      }} else {{
        if (c === '"') {{ inQ = true; }}
        else if (c === ',') {{ fields.push(cur); cur = ''; }}
        else {{ cur += c; }}
      }}
    }}
    fields.push(cur);
    rows.push(fields);
  }}
  return rows;
}}

// ── Import CSV ───────────────────────────────────────────────────────────────
function importCSV(input) {{
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {{
    const rows = parseCSV(e.target.result);
    if (rows.length < 2) {{ alert('CSV appears empty or has only a header row.'); return; }}
    const tbody = document.querySelector('tbody');
    tbody.innerHTML = '';
    // rows[0] is header (Operation, Steps) — skip it
    rows.slice(1).forEach(r => {{
      const op    = (r[0] || '').trim();
      const steps = (r[1] || '').trim();
      if (!op && !steps) return;
      // Section divider: op present, steps absent
      if (op && !steps) {{
        const tr = document.createElement('tr');
        tr.className = 'row-section';
        tr.innerHTML = '<td colspan="2">' + op + '</td>';
        tbody.appendChild(tr);
        return;
      }}
      const tr = document.createElement('tr');
      if (!steps) tr.className = 'empty-steps';
      tr.innerHTML =
        '<td class="op">'    + op    + '</td>' +
        '<td class="steps">' + (steps || '<span class="na">\u2014</span>') + '</td>';
      tbody.appendChild(tr);
    }});
    input.value = '';
  }};
  reader.readAsText(file);
}}

// ── Save page ────────────────────────────────────────────────────────────────
function savePage() {{
  const html = '<!DOCTYPE html>\n' + document.documentElement.outerHTML;
  const blob = new Blob([html], {{ type: 'text/html' }});
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(blob);
  a.download = document.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + '.html';
  a.click();
  URL.revokeObjectURL(a.href);
}}
</script>
</body>
</html>
"""
    (OUT_DIR / filename).write_text(page, encoding="utf-8")
    return filename


# ── update main index.html ────────────────────────────────────────────────────

def update_main_index(pages: list) -> None:
    """Replace the <ul>...</ul> inside the <!-- Radio Cards --> card."""
    text = MAIN_INDEX.read_text(encoding="utf-8")

    items = "\n".join(
        f'      <li><a href="static/radio-cards/{fn}">{html.escape(name)} <span class="desc">reference</span></a></li>'
        for name, fn in pages
    )
    new_ul = f"    <ul>\n{items}\n    </ul>"

    # Match <!-- Radio Cards --> ... <ul> ... </ul>  (first ul after the marker)
    pattern = re.compile(
        r"(<!-- Radio Cards -->.*?)\s*<ul>.*?</ul>",
        re.DOTALL,
    )
    new_text, n = pattern.subn(lambda m: m.group(1) + "\n    " + new_ul, text, count=1)

    if n == 0:
        print("  WARNING: could not find '<!-- Radio Cards -->' marker in index.html",
              file=sys.stderr)
        return

    MAIN_INDEX.write_text(new_text, encoding="utf-8")
    print(f"  updated    ../../index.html  ({len(pages)} radio(s))")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not CSV_FILE.exists():
        print(f"ERROR: {CSV_FILE} not found", file=sys.stderr)
        sys.exit(1)

    all_rows  = read_csv(CSV_FILE)
    header    = all_rows[0]   # ["Operation", "FT-2DR", "FT-5DR", ...]
    data_rows = all_rows[1:]

    # Radios are every column after col 0
    radios = [(header[col], col) for col in range(1, len(header)) if header[col].strip()]

    pages = []
    for radio, col in radios:
        filename = build_radio_page(radio, col, data_rows)
        pages.append((radio, filename))
        print(f"  generated  {filename}  ({radio})")

    pages.sort(key=lambda x: x[0].upper())
    update_main_index(pages)

