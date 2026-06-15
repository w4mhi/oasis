#!/usr/bin/env python3
"""
winlink/build.py

Reads winlink-radio-settings.csv (header row + data rows) and generates
radio-settings.html in the same folder.

Run: ./build.py
"""

import csv
import html
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CSV_FILE   = SCRIPT_DIR / "winlink-radio-settings.csv"
OUT_FILE   = SCRIPT_DIR / "radio-settings.html"
CSS_PATH   = "../css/common.css"


def read_csv(path: Path) -> list:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


if __name__ == "__main__":
    if not CSV_FILE.exists():
        print(f"ERROR: {CSV_FILE} not found", file=sys.stderr)
        sys.exit(1)

    rows = read_csv(CSV_FILE)
    if not rows:
        print("ERROR: CSV is empty", file=sys.stderr)
        sys.exit(1)

    headers   = rows[0]
    data_rows = rows[1:]

    # Build <thead>
    th_cells = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    thead = f"<thead><tr>{th_cells}</tr></thead>"

    # Build <tbody> — first column amber, rest plain
    tbody_lines = []
    for row in data_rows:
        # Pad row to header length
        padded = row + [""] * (len(headers) - len(row))
        cells = "".join(
            f'<td class="radio">{html.escape(padded[i])}</td>' if i == 0
            else f"<td>{html.escape(padded[i])}</td>"
            for i in range(len(headers))
        )
        tbody_lines.append(f"<tr>{cells}</tr>")
    tbody = "<tbody>\n" + "\n".join(tbody_lines) + "\n</tbody>"

    # Column width hints: short fixed cols, last (Notes) gets remainder
    short_cols = len(headers) - 2   # all except first (radio) and last (notes)
    col_widths = ""
    col_widths += '<col style="width:9%">\n'        # Radio
    for _ in range(short_cols - 2):
        col_widths += '<col style="width:8%">\n'    # HW/USB/Cable/ACC
    col_widths += '<col style="width:15%">\n'       # Winlink Settings
    col_widths += '<col style="width:18%">\n'       # SoundModem
    col_widths += "<col>\n"                         # Notes — takes remainder

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winlink Radio Settings</title>
<link rel="stylesheet" href="{CSS_PATH}">
<style>
.wrap {{ max-width: 1280px; }}
.tbl-wrap table {{ table-layout: fixed; width: 100%; }}
td, th {{ white-space: normal; vertical-align: top; word-break: break-word; }}
td.radio {{ color: var(--amber); font-weight: 700; white-space: nowrap; }}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <a class="home-link" href="/">🏠 HOME</a>
    <h1>Winlink Radio Settings</h1>
    <p>Hardware, cable, and software configuration per radio</p>
  </header>

  <div class="sec">
    <div class="tbl-wrap">
      <table>
        <colgroup>
{col_widths}        </colgroup>
        {thead}
        {tbody}
      </table>
    </div>
  </div>

</div>
</body>
</html>
"""

    OUT_FILE.write_text(page, encoding="utf-8")
    print(f"  generated  {OUT_FILE.name}  ({len(data_rows)} radio(s))")
