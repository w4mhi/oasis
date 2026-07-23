import json
import os
import tempfile
import unittest

from ai.server.tools import ref


QCODES_HTML = """
<h1>Q-Codes</h1>
<table>
  <thead><tr><th>Code</th><th>Meaning</th><th>Notes</th></tr></thead>
  <tbody>
    <tr><td class="key">QRM</td><td class="def">I am being interfered with</td>
        <td class="note"><span class="tag tag-emcomm">EmComm</span> Man-made interference</td></tr>
    <tr><td class="key">QSL</td><td class="def">I acknowledge receipt</td><td class="note">Confirm received</td></tr>
  </tbody>
</table>
"""

RST_HTML = """
<h1>RST</h1>
<table>
  <thead><tr><th>Value</th><th>Readability</th></tr></thead>
  <tbody>
    <tr><td class="rst-num">5</td><td class="def">Perfectly readable</td></tr>
  </tbody>
</table>
"""


class TestParseQuickref(unittest.TestCase):
    def test_parses_rows_key_and_body_stripping_inner_tags(self):
        entries = ref.parse_quickref(QCODES_HTML, "qcodes")
        self.assertEqual(len(entries), 2)
        qrm = entries[0]
        self.assertEqual(qrm.topic, "qcodes")
        self.assertEqual(qrm.key, "QRM")
        self.assertIn("interfered with", qrm.body)
        self.assertIn("EmComm", qrm.body)        # inner <span> text kept
        self.assertNotIn("<span", qrm.body)       # tag stripped

    def test_excludes_header_rows(self):
        # the <thead> row is all <th>, so it must not become an Entry
        keys = [e.key for e in ref.parse_quickref(QCODES_HTML, "qcodes")]
        self.assertEqual(keys, ["QRM", "QSL"])

    def test_class_agnostic_rst_num(self):
        entries = ref.parse_quickref(RST_HTML, "rst")
        self.assertEqual(entries[0].key, "5")            # rst-num cell captured
        self.assertIn("Perfectly readable", entries[0].body)


class TestLoadBandplan(unittest.TestCase):
    def test_folds_segments_and_notes_into_body(self):
        d = {"band": "20m", "name": "20 Meters", "license": "General+",
             "notes": ["Most popular DX band"],
             "rows": [{"segments": [
                 {"label": "SSB / Phone", "detail": "Phone/SSB 14.225-14.350. SSTV 14.230."}]}]}
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "20m.json"), "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        entries = ref.load_bandplan(tmp)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e.topic, "bandplan")
        self.assertEqual(e.key, "20m")
        self.assertIn("20 Meters", e.body)
        self.assertIn("Most popular DX band", e.body)
        self.assertIn("SSTV 14.230", e.body)          # segment detail folded in


class TestBuildIndexRealFiles(unittest.TestCase):
    def test_index_over_real_bundled_files_has_all_topics(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        idx = ref.build_index(root)
        topics = {e.topic for e in idx}
        for t in ("qcodes", "phonetic", "prowords", "rst", "itu-prefixes", "bandplan"):
            self.assertIn(t, topics)
        # sanity: a well-known entry is present
        self.assertTrue(any(e.topic == "qcodes" and e.key == "QRM" for e in idx))


class TestSearch(unittest.TestCase):
    def _idx(self):
        return [
            ref.Entry("qcodes", "QRM", "I am being interfered with · Man-made interference"),
            ref.Entry("qcodes", "QSL", "I acknowledge receipt · Confirm received"),
            ref.Entry("bandplan", "20m", "20 Meters · General+ · SSB / Phone: SSTV 14.230"),
        ]

    def test_exact_key_ranks_first(self):
        hits = ref.search(self._idx(), "QSL")
        self.assertEqual(hits[0].key, "QSL")

    def test_token_match_in_body(self):
        hits = ref.search(self._idx(), "interfered")
        self.assertEqual([h.key for h in hits], ["QRM"])

    def test_substring_frequency_hits_bandplan(self):
        hits = ref.search(self._idx(), "14.230")
        self.assertEqual(hits[0].key, "20m")

    def test_topic_filter(self):
        hits = ref.search(self._idx(), "receipt", topic="qcodes")
        self.assertTrue(all(h.topic == "qcodes" for h in hits))
        self.assertEqual(ref.search(self._idx(), "receipt", topic="bandplan"), [])

    def test_topic_alias(self):
        # 'q-codes' alias normalizes to 'qcodes'
        hits = ref.search(self._idx(), "QRM", topic="q-codes")
        self.assertEqual(hits[0].key, "QRM")

    def test_no_match_returns_empty(self):
        self.assertEqual(ref.search(self._idx(), "zzzznomatch"), [])


class TestFormat(unittest.TestCase):
    def test_formats_with_topic_labels(self):
        out = ref.format_results([ref.Entry("qcodes", "QRM", "I am being interfered with")])
        self.assertIn("[qcodes] QRM", out)
        self.assertIn("interfered", out)

    def test_empty_has_no_match_text(self):
        self.assertIn("no match", ref.format_results([]).lower())
