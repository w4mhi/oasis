import os, sys, json, tempfile, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
import roster  # noqa: E402
import satnogs  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _real_txs():
    """Real captured SatNOGS records through the real parser — the uplink shape
    is the thing under test, and a hand-written fixture would let us assert a
    shape SatNOGS does not actually emit."""
    with open(os.path.join(_FIXTURES, "satnogs-transmitters.json"), encoding="utf-8") as fh:
        return satnogs.parse_transmitters(json.load(fh))


class RosterTest(unittest.TestCase):
    def test_load_seeds_empty_envelope_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            data = roster.load(p)
            self.assertTrue(os.path.exists(p))
            self.assertEqual(data["satellites"], [])
            self.assertEqual(data["labels"], {})

    def test_set_selected_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            json.dump({"updated": "x", "labels": {}, "satellites":
                       [{"norad": 25544, "selected": False, "transmitters": []}]},
                      open(p, "w"))
            roster.set_selected(p, 25544, True)
            iss = json.load(open(p))["satellites"][0]
            self.assertTrue(iss["selected"])

    def test_load_recovers_in_memory_without_clobbering_garbled_file(self):
        # A garbled read is usually a TRANSIENT mid-write (atomic save() makes even
        # that impossible now). load() must recover IN-MEMORY, but must NEVER
        # overwrite the file — doing so let a concurrent /select persist an empty
        # roster over a full one and wipe the whole list.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            with open(p, "w") as fh:
                json.dump([1, 2, 3], fh)          # valid JSON, wrong shape
            data = roster.load(p)
            self.assertEqual(data["satellites"], [])          # recovered in-memory
            with open(p) as fh:
                self.assertEqual(json.load(fh), [1, 2, 3])    # file left INTACT, not clobbered

    def test_save_is_atomic_no_temp_left_behind(self):
        # save() writes a unique temp then os.replace()s it in; on success the dir
        # holds exactly satellites.json and no leftover .satellites-*.tmp files.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            roster.save(p, {"updated": "x", "source": "t", "labels": {},
                            "satellites": [{"norad": 25544, "selected": False}]})
            self.assertEqual(os.listdir(d), ["satellites.json"])
            with open(p) as fh:
                self.assertEqual(json.load(fh)["satellites"][0]["norad"], 25544)

    def test_legacy_downlinks_flattens_transmitters(self):
        sat = {"transmitters": [
            {"mode": "FM", "downlink": {"freq_mhz": 145.8, "freq_high_mhz": None},
             "uplink": {"freq_mhz": 145.99, "freq_high_mhz": None}},
            {"mode": "APRS", "downlink": {"freq_mhz": 145.825, "freq_high_mhz": None},
             "uplink": None},
            {"mode": "BEACONONLYUP", "downlink": None,
             "uplink": {"freq_mhz": 435.0, "freq_high_mhz": None}}]}
        dls = roster.legacy_downlinks(sat)
        self.assertEqual(dls, [
            {"mode": "FM", "freq_mhz": 145.8,
             "uplink": {"freq_mhz": 145.99, "freq_high_mhz": None,
                        "invert": False, "ctcss_hz": None, "simplex": False}},
            {"mode": "APRS", "freq_mhz": 145.825, "uplink": None}])

    def test_legacy_downlinks_dedup_and_aprs_naming(self):
        sat = {"transmitters": [
            {"mode": "AFSK", "description": "ISS APRS Digipeater", "downlink": {"freq_mhz": 145.825}},
            {"mode": "AFSK", "description": "ISS APRS Digipeater", "downlink": {"freq_mhz": 145.825}},
            {"mode": "FM", "description": "Voice Repeater", "downlink": {"freq_mhz": 437.8}},
            {"mode": "FM", "description": "Voice Repeater", "downlink": {"freq_mhz": 437.8}},
            {"mode": "SSTV", "description": "SSTV", "downlink": {"freq_mhz": 145.8}}]}
        dls = roster.legacy_downlinks(sat)
        self.assertEqual(dls, [
            {"mode": "APRS", "freq_mhz": 145.825, "uplink": None},   # AFSK + APRS-in-description → APRS
            {"mode": "FM", "freq_mhz": 437.8, "uplink": None},       # dup collapsed
            {"mode": "SSTV", "freq_mhz": 145.8, "uplink": None}])


class OperatorFieldsTest(unittest.TestCase):
    """The bell is per-BIRD and lives in the roster, so the kiosk can see a bell
    armed from a laptop. Muting stays per-DEVICE in the browser and must never
    appear here."""

    def _seed(self, d, sats):
        p = os.path.join(d, "satellites.json")
        json.dump({"updated": "x", "labels": {}, "satellites": sats}, open(p, "w"))
        return p

    def test_set_bell_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [{"norad": 25544, "selected": True, "transmitters": []}])
            roster.set_bell(p, 25544, True)
            self.assertTrue(json.load(open(p))["satellites"][0]["bell"])

    def test_bells_and_selection_are_independent(self):
        """Disarming a bell must not un-monitor the bird, and vice versa — they
        are separate standing choices that happen to live in the same record."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [{"norad": 25544, "selected": True, "bell": True,
                                "transmitters": []}])
            roster.set_bells_many(p, {25544: False})
            iss = json.load(open(p))["satellites"][0]
            self.assertTrue(iss["selected"])
            self.assertFalse(iss["bell"])

    def test_bulk_bells_are_one_write(self):
        """The lesson /select paid for: a set must land as ONE load-modify-save,
        never one request per bird."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [{"norad": n, "transmitters": []} for n in (25544, 33591, 43017)])
            writes = []
            real_save = roster.save
            try:
                roster.save = lambda path, data: (writes.append(1), real_save(path, data))[1]
                roster.set_bells_many(p, {25544: True, 33591: True, 43017: False})
            finally:
                roster.save = real_save
            self.assertEqual(len(writes), 1)
            armed = {s["norad"]: s["bell"] for s in json.load(open(p))["satellites"]}
            self.assertEqual(armed, {25544: True, 33591: True, 43017: False})

    def test_unknown_norads_cannot_add_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [{"norad": 25544, "transmitters": []}])
            roster.set_bells_many(p, {999999: True})
            self.assertEqual([s["norad"] for s in json.load(open(p))["satellites"]], [25544])

    def test_unparseable_key_skips_without_failing_the_batch(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [{"norad": 25544, "transmitters": []}])
            roster.set_bells_many(p, {"nope": True, "25544": True})
            self.assertTrue(json.load(open(p))["satellites"][0]["bell"])

    def test_a_non_operator_field_is_refused(self):
        """Guards the typo that would write a junk key the aggregator then drops
        on the next rebuild — a bug that only surfaces weeks later."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [{"norad": 25544, "transmitters": []}])
            with self.assertRaises(ValueError):
                roster._set_flag_many(p, {25544: True}, "name")

    def test_operator_state_reports_every_field_defaulting_off(self):
        state = roster.operator_state({"satellites": [
            {"norad": 25544, "selected": True, "bell": True},
            {"norad": 33591},                       # pre-bell roster row
        ]})
        self.assertEqual(state[25544], {"selected": True, "bell": True})
        self.assertEqual(state[33591], {f: False for f in roster.OPERATOR_FIELDS})

    def test_operator_state_skips_rows_with_no_usable_norad(self):
        state = roster.operator_state({"satellites": [{"selected": True},
                                                      {"norad": None}]})
        self.assertEqual(state, {})

    def test_muting_is_not_an_operator_field(self):
        """Muting is per-DEVICE (localStorage) so the shack kiosk can be silent
        overnight while a laptop still chimes. Persisting it per-bird here would
        silence every screen at once."""
        self.assertNotIn("muted", roster.OPERATOR_FIELDS)
        self.assertNotIn("mute", roster.OPERATOR_FIELDS)

    # ── The one-shot "monitoring arms the bell" backfill ─────────────────────
    # Selecting a bird now arms its pass alert. These cover the rosters written
    # before that rule, where every monitored bird sits at bell=false and would
    # otherwise stay silent forever — the exact silent failure the change was
    # made to remove.

    def test_bell_default_arms_every_monitored_bird_once(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [{"norad": 25544, "selected": True, "transmitters": []},
                               {"norad": 33591, "selected": True, "transmitters": []},
                               {"norad": 43017, "selected": False, "transmitters": []}])
            roster.apply_bell_default_once(p)
            got = {s["norad"]: s.get("bell") for s in json.load(open(p))["satellites"]}
            self.assertEqual(got[25544], True)
            self.assertEqual(got[33591], True)
            # An unmonitored bird is not armed: the rule is that MONITORING arms
            # the bell, not that every bird in the roster rings.
            self.assertNotEqual(got[43017], True)

    def test_a_disarm_after_the_backfill_is_never_undone(self):
        """The one that matters. Run twice, this would re-arm every bird the
        operator had deliberately disarmed — and they would only find out at
        03:00, from the other side of the house."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [{"norad": 25544, "selected": True, "transmitters": []}])
            roster.apply_bell_default_once(p)
            roster.set_bells_many(p, {25544: False})      # "not this one, thanks"
            roster.apply_bell_default_once(p)
            self.assertFalse(json.load(open(p))["satellites"][0]["bell"])

    def test_the_backfill_is_recorded_even_with_nothing_to_migrate(self):
        """A fresh box has an empty roster (build-roster.py fills it later). If
        the flag were only stamped when something was armed, this would stay
        un-migrated and then fire against a roster the operator had since
        curated — arming birds they had turned off."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [])
            roster.apply_bell_default_once(p)
            self.assertTrue(json.load(open(p))[roster.BELL_DEFAULT_KEY])

    def test_an_explicit_bell_write_settles_the_migration(self):
        """The ordering hazard. The backfill runs on the roster READ, so a disarm
        that lands before the first read would be silently undone — and the
        operator would have no way to tell that their choice never took."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [{"norad": 25544, "selected": True, "transmitters": []}])
            roster.set_bells_many(p, {25544: False})     # lands before any read
            roster.apply_bell_default_once(p)
            self.assertFalse(json.load(open(p))["satellites"][0]["bell"])

    def test_selecting_does_not_settle_the_migration(self):
        """Only a BELL write counts. A station still running an older client
        posts selections without bells, and stamping on those would strand every
        bird it monitors at bell=false — the silent pass this all exists to fix."""
        with tempfile.TemporaryDirectory() as d:
            p = self._seed(d, [{"norad": 25544, "transmitters": []}])
            roster.set_selected_many(p, {25544: True})
            self.assertNotIn(roster.BELL_DEFAULT_KEY, json.load(open(p)))
            roster.apply_bell_default_once(p)
            self.assertTrue(json.load(open(p))["satellites"][0]["bell"])

    def test_the_backfill_flag_survives_a_roster_rebuild(self):
        """A SOURCE tripwire, not a behaviour test: build-roster.py's output dict
        is a fixed top-level key set, so a flag not named there is dropped on the
        next rebuild — and the backfill would re-run and undo every disarm. The
        assembly lives inside main(), behind two network fetches, so this checks
        the one thing that can silently go missing."""
        src = os.path.join(os.path.dirname(_HERE), "services", "satellites", "build-roster.py")
        with open(src, encoding="utf-8") as fh:
            self.assertIn("BELL_DEFAULT_KEY", fh.read())


class UplinkView(unittest.TestCase):
    def _grouped(self, norad):
        import listen
        txs = _real_txs()[norad]
        entries = [dict(d, **listen.mode_support(d.get("mode")))
                   for d in roster.legacy_downlinks({"transmitters": txs})]
        return {round(g["freq_mhz"], 6): g for g in roster.group_downlinks(entries)}

    def test_grouped_entry_attributes_its_uplink_to_the_right_mode(self):
        # 437.800 groups FM + FSK + SSTV (one radio, reconfigured). Only the FM
        # member has an uplink; naming it is what stops the card implying you can
        # transmit to the SSTV or telemetry service.
        g = self._grouped(25544)[437.8]
        self.assertEqual(sorted(g["modes"]), ["FM", "FSK", "SSTV"])
        self.assertEqual(len(g["uplinks"]), 1)
        self.assertEqual(g["uplinks"][0]["freq_mhz"], 145.99)
        self.assertEqual(g["uplinks"][0]["mode"], "FM")

    def test_ctcss_is_extracted_from_the_description(self):
        self.assertEqual(self._grouped(25544)[437.8]["uplinks"][0]["ctcss_hz"], 67.0)

    def test_no_ctcss_in_the_description_yields_none_not_a_guess(self):
        # 145.800 FM's description carries no tone. None is the correct answer;
        # a wrong tone is worse than no tone.
        self.assertIsNone(self._grouped(25544)[145.8]["uplinks"][0]["ctcss_hz"])

    def test_uplink_equal_to_downlink_is_flagged_simplex(self):
        g = self._grouped(25544)[145.825]
        self.assertEqual(g["uplinks"][0]["freq_mhz"], 145.825)
        self.assertTrue(g["uplinks"][0]["simplex"])

    def test_linear_transponder_keeps_its_passband_and_inversion(self):
        g = self._grouped(7530)[145.925]
        up = g["uplinks"][0]
        self.assertEqual((up["freq_mhz"], up["freq_high_mhz"]), (432.125, 432.175))
        self.assertTrue(up["invert"])

    def test_an_entry_with_no_uplink_carries_an_empty_list(self):
        # NOAA 19 APT: receive-only. Empty list, never a missing key — the client
        # renders `uplinks.length` and an undefined would throw.
        self.assertEqual(self._grouped(33591)[137.1]["uplinks"], [])

    def test_grouped_entries_drop_the_singular_uplink_key(self):
        for g in self._grouped(25544).values():
            self.assertNotIn("uplink", g)


if __name__ == "__main__":
    unittest.main()
