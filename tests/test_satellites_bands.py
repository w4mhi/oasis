"""What reaches the roster card: the usable-band filter and the (freq, demod) grouping.

Both rules were derived from measurements against the live SatNOGS catalogue, so
the numbers in the assertions are real: the ISS card rendered 28 downlink buttons,
19 of which no amateur station can tune (Soyuz VHF, three spacesuit channels,
Zarya, Zvezda, Regul, Kvant), and 437.800 carried three transmitters that produce
a byte-identical capture command.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))

import bands      # noqa: E402
import roster     # noqa: E402


def _tx(mode, freq_mhz, description=""):
    return {"mode": mode, "type": "Transmitter", "description": description,
            "downlink": {"freq_mhz": freq_mhz}}


class UsableBandsTest(unittest.TestCase):
    def test_the_amateur_and_weather_bands_are_usable(self):
        for f in (145.8, 145.825, 437.8, 435.4, 1250.0, 137.1, 137.9):
            self.assertTrue(bands.usable_downlink(f), f)

    def test_the_iss_clutter_is_not(self):
        # Every one of these is a real ISS downlink inside RTL tuning range, and
        # every one of them was a button on the card.
        for f, what in ((143.625, "Soyuz VHF-1"), (130.167, "Soyuz VHF-2"),
                        (121.75, "Orlan suit"), (121.1, "Suit 1"), (121.275, "Suit 2"),
                        (632.0, "Zarya"), (630.128, "Zvezda"), (922.763, "Regul"),
                        (768.975, "Kvant"), (414.2, "EMU"), (417.1, "EMU Alt"),
                        (400.575, "EVA/WVS"), (400.5, "CUCU"), (468.1, "ICARUS")):
            self.assertFalse(bands.usable_downlink(f), what)

    def test_band_edges_are_inclusive(self):
        for f in (144.0, 148.0, 420.0, 450.0, 137.0, 138.0):
            self.assertTrue(bands.usable_downlink(f), f)
        for f in (143.999, 148.001, 136.999, 138.001, 419.9, 450.1):
            self.assertFalse(bands.usable_downlink(f), f)

    def test_an_unknown_frequency_is_not_usable(self):
        # None is not evidence of anything; it must not become a button.
        self.assertFalse(bands.usable_downlink(None))

    def test_satnogs_shares_these_bands_rather_than_redeclaring_them(self):
        # Two modules asking the same question must not drift apart.
        import satnogs
        self.assertIs(satnogs._AMATEUR_BANDS_MHZ, bands.AMATEUR_BANDS_MHZ)
        self.assertIs(satnogs._WX_BAND_MHZ, bands.WX_BAND_MHZ)


class LegacyDownlinkFilterTest(unittest.TestCase):
    def test_unusable_downlinks_never_become_buttons(self):
        sat = {"transmitters": [
            _tx("FM", 145.8), _tx("FMN", 143.625), _tx("FM", 121.75),
            _tx("APRS", 145.825, "APRS digipeater"), _tx("FM", 632.0),
        ]}
        got = [(d["mode"], d["freq_mhz"]) for d in roster.legacy_downlinks(sat)]
        self.assertEqual(got, [("FM", 145.8), ("APRS", 145.825)])

    def test_a_weather_downlink_survives(self):
        # 137-138 is not amateur. An amateur-only rule would delete every
        # APT/LRPT button on the card.
        sat = {"transmitters": [_tx("APT", 137.1), _tx("LRPT", 137.9)]}
        self.assertEqual(len(roster.legacy_downlinks(sat)), 2)

    def test_the_record_is_untouched(self):
        # The filter is a VIEW. labels_for reads the record to decide roster
        # membership, and 345 birds live there on an out-of-band DATA downlink
        # alone — filtering the record would delete them.
        sat = {"transmitters": [_tx("FM", 145.8), _tx("BPSK", 400.5)]}
        before = [dict(t) for t in sat["transmitters"]]
        roster.legacy_downlinks(sat)
        self.assertEqual(sat["transmitters"], before)


class GroupDownlinksTest(unittest.TestCase):
    """Grouping collapses the ACTION, never the INFORMATION."""

    def test_the_iss_437_800_collapses_to_one_entry(self):
        entries = [
            {"mode": "FSK", "freq_mhz": 437.8, "demod": "fm", "supported": True,
             "blurb": "FM-family — hearable now"},
            {"mode": "SSTV", "freq_mhz": 437.8, "demod": "fm", "supported": True,
             "blurb": "SSTV image (FM) — hear the warble"},
            {"mode": "FM", "freq_mhz": 437.8, "demod": "fm", "supported": True,
             "blurb": "FM voice audio"},
        ]
        got = roster.group_downlinks(entries)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["freq_mhz"], 437.8)

    def test_every_mode_survives_the_collapse(self):
        # The operator must not go blind: listening for the voice repeater and
        # getting SSTV warble has to be something the card already said.
        entries = [
            {"mode": "FSK", "freq_mhz": 437.8, "demod": "fm", "blurb": "b1"},
            {"mode": "SSTV", "freq_mhz": 437.8, "demod": "fm", "blurb": "b2"},
            {"mode": "FM", "freq_mhz": 437.8, "demod": "fm", "blurb": "b3"},
        ]
        got = roster.group_downlinks(entries)[0]
        self.assertEqual(got["modes"], ["FM", "FSK", "SSTV"])
        for b in ("b1", "b2", "b3"):
            self.assertIn(b, got["blurb"])

    def test_different_demods_on_one_frequency_do_not_collapse(self):
        # NORAD 31130: FM and CW both on 435.245. CW is tuned 700 Hz low so the
        # carrier lands as an audible tone — collapsing would break the capture.
        entries = [
            {"mode": "FM", "freq_mhz": 435.245, "demod": "fm"},
            {"mode": "CW", "freq_mhz": 435.245, "demod": "usb"},
        ]
        got = roster.group_downlinks(entries)
        self.assertEqual(len(got), 2)
        self.assertEqual({g["mode"] for g in got}, {"FM", "CW"})

    def test_the_representative_mode_is_deterministic(self):
        # Any member drives the demodulator correctly (they share a demod by
        # construction), so it must not depend on SatNOGS ordering.
        a = [{"mode": m, "freq_mhz": 437.8, "demod": "fm"} for m in ("SSTV", "FM", "FSK")]
        b = [{"mode": m, "freq_mhz": 437.8, "demod": "fm"} for m in ("FSK", "SSTV", "FM")]
        self.assertEqual(roster.group_downlinks(a)[0]["mode"],
                         roster.group_downlinks(b)[0]["mode"])
        self.assertEqual(roster.group_downlinks(a)[0]["mode"], "FM")

    def test_unsupported_entries_group_by_their_own_absent_demod(self):
        # demod None (BPSK/QPSK/LRPT) is still a key: two unsupported modes on
        # one frequency are one dead button, not two.
        entries = [
            {"mode": "BPSK", "freq_mhz": 437.5, "demod": None, "supported": False},
            {"mode": "QPSK", "freq_mhz": 437.5, "demod": None, "supported": False},
        ]
        got = roster.group_downlinks(entries)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["modes"], ["BPSK", "QPSK"])

    def test_distinct_frequencies_stay_distinct(self):
        entries = [
            {"mode": "FM", "freq_mhz": 145.8, "demod": "fm"},
            {"mode": "APRS", "freq_mhz": 145.825, "demod": "fm"},
        ]
        self.assertEqual(len(roster.group_downlinks(entries)), 2)

    def test_empty_input_is_not_an_error(self):
        self.assertEqual(roster.group_downlinks([]), [])


if __name__ == "__main__":
    unittest.main()
