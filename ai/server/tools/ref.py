"""Offline ham-radio reference search for the AI assistant.

Parses the repo's bundled reference data — the quick-ref HTML tables and the
band-plan JSON — into flat Entry records, then keyword-searches them. Pure and
stdlib-only (html.parser + json); reads local files from disk (no HTTP, no deps,
no embeddings). Registered as the `ref_search` MCP tool.
"""
import glob
import json
import os
from collections import namedtuple
from html.parser import HTMLParser

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUITE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))

QUICKREF_TOPICS = ("qcodes", "phonetic", "prowords", "rst", "itu-prefixes")

Entry = namedtuple("Entry", "topic key body")


class _TableRowParser(HTMLParser):
    """Collect table rows as lists of <td> cell text, stripping inner tags.

    Header rows (all <th>) yield no <td> cells and are dropped. Text inside
    nested tags (e.g. <span>) still arrives via handle_data while we're inside
    a <td>, so it is kept while the tags themselves are discarded.
    """
    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(c for c in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_quickref(html, topic):
    parser = _TableRowParser()
    parser.feed(html)
    entries = []
    for cells in parser.rows:
        key = cells[0]
        if not key:
            continue
        body = " · ".join(c for c in cells[1:] if c)
        entries.append(Entry(topic, key, body))
    return entries


def load_bandplan(data_dir):
    entries = []
    for path in sorted(glob.glob(os.path.join(data_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        parts = [d.get("name", ""), d.get("license", "")]
        parts.extend(d.get("notes", []))
        for row in d.get("rows", []):
            for seg in row.get("segments", []):
                label = seg.get("label", "")
                detail = seg.get("detail", "")
                if label or detail:
                    parts.append(f"{label}: {detail}".strip(": "))
        body = " · ".join(p for p in parts if p)
        entries.append(Entry("bandplan", d.get("band") or os.path.basename(path), body))
    return entries


_index_cache = None


def build_index(suite_root=None):
    global _index_cache
    default = suite_root is None
    if default and _index_cache is not None:
        return _index_cache
    root = suite_root or _SUITE_ROOT
    entries = []
    qr_dir = os.path.join(root, "static", "quick-ref")
    for topic in QUICKREF_TOPICS:
        path = os.path.join(qr_dir, f"{topic}.html")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                entries.extend(parse_quickref(fh.read(), topic))
    entries.extend(load_bandplan(os.path.join(root, "static", "band-plan", "data")))
    if default:
        _index_cache = entries
    return entries


_TOPIC_ALIASES = {
    "phonetics": "phonetic", "nato": "phonetic",
    "itu": "itu-prefixes", "prefixes": "itu-prefixes", "prefix": "itu-prefixes",
    "band": "bandplan", "bands": "bandplan", "band-plan": "bandplan",
    "q-codes": "qcodes", "qcode": "qcodes", "q-code": "qcodes",
    "proword": "prowords", "prosigns": "prowords",
}


def _normalize_topic(topic):
    t = (topic or "").lower().strip()
    return _TOPIC_ALIASES.get(t, t)


def search(index, query, topic="", limit=5):
    q = (query or "").lower().strip()
    tokens = [t for t in q.split() if t]
    want_topic = _normalize_topic(topic)
    scored = []
    for e in index:
        if want_topic and e.topic != want_topic:
            continue
        hay = (e.key + " " + e.body).lower()
        score = 0
        if e.key.lower() == q:
            score += 10
        if q and q in hay:
            score += 5
        score += sum(1 for t in tokens if t in hay)
        if score > 0:
            scored.append((score, e))
    # stable sort: equal scores keep original index order
    scored.sort(key=lambda se: -se[0])
    return [e for _, e in scored[:limit]]


def format_results(entries):
    if not entries:
        return "No matching reference entry."
    lines = []
    for e in entries:
        body = e.body if len(e.body) <= 240 else e.body[:237] + "..."
        lines.append(f"[{e.topic}] {e.key} — {body}" if body else f"[{e.topic}] {e.key}")
    return "\n".join(lines)
