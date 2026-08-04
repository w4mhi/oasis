import os, sys, time, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from services.aprs.common import warning_broadcast as wb


class FormatTest(unittest.TestCase):
    def test_object_name_prefix_and_len(self):
        self.assertEqual(wb.object_name("3f9a1b2c9999"), "W3f9a1b2c")
        self.assertEqual(len(wb.object_name("ab")), 9)  # padded

    def test_format_lat(self):
        self.assertEqual(wb.format_lat(47.5495), "4732.97N")
        self.assertEqual(wb.format_lat(-33.0), "3300.00S")

    def test_format_lon(self):
        self.assertEqual(wb.format_lon(-122.0298), "12201.79W")
        self.assertEqual(wb.format_lon(5.5), "00530.00E")

    def test_object_payload_shape(self):
        # Object name now comes from the STORED tactical aprs_name, not the id.
        w = {"id": "3f9a1b2cdeadbeef", "aprs_name": "FLOOD01",
             "lat": 47.5495, "lon": -122.0298, "note": "road out"}
        p = wb.object_payload(w, "\\", "w", "both", 1800)
        self.assertEqual(p["type"], "object")
        self.assertEqual(p["object_name"], "FLOOD01")
        self.assertEqual(p["symbol_table"], "\\")
        self.assertEqual(p["symbol"], "w")
        self.assertEqual(p["send_path"], "both")
        self.assertEqual(p["interval"], 1800)
        self.assertEqual(p["comment"], "road out")
        self.assertAlmostEqual(p["latitude"], 47.5495)
        self.assertNotIn("callsign", p)   # no source_callsign given -> omitted

    def test_object_payload_uses_stored_name_and_source_call(self):
        w = {"id": "x", "aprs_name": "AID01", "lat": 47.5, "lon": -122.0,
             "note": "AID01 inserted by W4MHI-1"}
        p = wb.object_payload(w, "/", "+", "both", 1800, "W4MHI-1")
        self.assertEqual(p["object_name"], "AID01")
        self.assertEqual(p["callsign"], "W4MHI-1")
        self.assertEqual(p["comment"], "AID01 inserted by W4MHI-1")

    def test_kill_info_has_underscore_and_name(self):
        ts = time.struct_time((2026, 8, 3, 18, 30, 0, 0, 0, 0))
        info = wb.kill_info("W3f9a1b2c", 47.5495, -122.0298, "\\", "w", ts)
        self.assertTrue(info.startswith(";W3f9a1b2c"))
        self.assertEqual(info[10], "_")            # kill flag after 9-char name
        self.assertIn("031830z", info)             # DDHHMMz
        self.assertIn("4732.97N", info)
        self.assertIn("12201.79W", info)
        self.assertTrue(info.endswith("w"))        # symbol code last

    def test_kill_payload_is_custom(self):
        ts = time.gmtime(0)
        p = wb.kill_payload("W3f9a1b2c", 1.0, 2.0, "/", "o", "both", ts)
        self.assertEqual(p["type"], "custom")
        self.assertTrue(p["custom_info"].startswith(";W3f9a1b2c_"))
        self.assertEqual(p["send_path"], "both")
        self.assertNotIn("callsign", p)   # no source_callsign given -> omitted

    def test_kill_payload_sets_source_callsign(self):
        ts = time.gmtime(0)
        p = wb.kill_payload("AID01", 1.0, 2.0, "/", "o", "both", ts, "W4MHI-1")
        self.assertEqual(p["callsign"], "W4MHI-1")

    def test_clean_send_path(self):
        self.assertEqual(wb.clean_send_path("is_only", "both"), "is_only")
        self.assertEqual(wb.clean_send_path("rf", "both"), "rf")
        self.assertEqual(wb.clean_send_path("bogus", "both"), "both")   # invalid -> default
        self.assertEqual(wb.clean_send_path(None, "is_only"), "is_only")

    def test_clean_send_path_non_string_falls_back(self):
        self.assertEqual(wb.clean_send_path(["x"], "both"), "both")     # list -> default, no crash
        self.assertEqual(wb.clean_send_path({"a": 1}, "rf"), "rf")      # dict -> default
        self.assertEqual(wb.clean_send_path(123, "is_only"), "is_only") # int -> default

    def test_tactical_name_sequence(self):
        self.assertEqual(wb.tactical_name("AID", set()), "AID01")
        self.assertEqual(wb.tactical_name("FLOOD", {"FLOOD01"}), "FLOOD02")
        self.assertEqual(wb.tactical_name("FLOOD", {"FLOOD01", "FLOOD02"}), "FLOOD03")

    def test_tactical_name_truncated_to_nine_chars(self):
        # 7-char abbr + 2-digit suffix = 9 chars exactly; never exceeds 9.
        self.assertEqual(len(wb.tactical_name("ROADBLK", set())), 9)
        self.assertEqual(wb.tactical_name("ROADBLK", set()), "ROADBLK01")

    def test_tactical_name_bounded_past_99(self):
        # generate 120 sequential names, all unique and <=9 chars
        names = {wb.tactical_name("ROADBLK", set())}  # seed: ROADBLK01
        for _ in range(119):
            nm = wb.tactical_name("ROADBLK", names)
            self.assertLessEqual(len(nm), 9)
            self.assertNotIn(nm, names)
            names.add(nm)
        self.assertEqual(len(names), 120)   # no collisions across the 99->100 boundary


class FakeClient:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.beacons = {}      # id -> payload
        self.sent = []         # beacon ids that got send_now
        self._created = []     # every payload passed to create_beacon (incl. deleted ones)
        self._n = 0
    def health(self): return self.healthy
    def create_beacon(self, payload):
        if not self.healthy: raise wb.GraywolfError("down")
        self._n += 1; bid = str(self._n)
        rec = dict(payload); rec["id"] = bid; self.beacons[bid] = rec
        self._created.append(dict(rec))
        # object_name is echoed for object beacons
        return bid
    def update_beacon(self, bid, payload):
        if bid in self.beacons: self.beacons[bid].update(payload)
    def delete_beacon(self, bid): self.beacons.pop(bid, None)
    def send_now(self, bid): self.sent.append(bid)
    def list_beacons(self): return list(self.beacons.values())


SYMS = {"flood": ("\\", "w"), "fire": ("/", ":")}


class BroadcasterTest(unittest.TestCase):
    def _w(self, wid="3f9a1b2cdead", typ="flood", broadcast=True, gw=None,
           aprs_name=None):
        # aprs_name defaults to the legacy 'W'+id[:8] shape so tests that
        # don't care about tactical naming (and rely on the source_callsign=None
        # fallback ownership path) keep matching the old on-air names.
        return {"id": wid, "type": typ, "lat": 47.5, "lon": -122.0,
                "note": "x", "broadcast": broadcast, "gw_beacon_id": gw,
                "aprs_name": aprs_name or ("W" + str(wid)[:8])}

    def test_advertise_creates_object_beacon(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        bid = b.advertise(self._w())
        self.assertEqual(bid, "1")
        self.assertEqual(c.beacons["1"]["type"], "object")
        self.assertEqual(c.beacons["1"]["symbol"], "w")

    def test_advertise_unknown_type_uses_fallback_symbol(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        b.advertise(self._w(typ="mystery"))
        self.assertEqual(c.beacons["1"]["symbol_table"], "\\")
        self.assertEqual(c.beacons["1"]["symbol"], "!")

    def test_advertise_returns_none_when_down(self):
        c = FakeClient(healthy=False); b = wb.WarningBroadcaster(c, SYMS)
        self.assertIsNone(b.advertise(self._w()))

    def test_unadvertise_deletes_and_sends_kill(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS, kill_repeat=3)
        w = self._w(); w["gw_beacon_id"] = b.advertise(w)
        b.unadvertise(w)
        # object beacon gone; a custom kill beacon was created + sent 3× + removed
        self.assertNotIn("1", c.beacons)
        self.assertEqual(len(c.sent), 3)
        self.assertEqual(c.beacons, {})   # transient kill beacon cleaned up

    def test_reconcile_creates_missing_and_kills_orphans(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        # pre-existing OASIS-owned orphan object with no matching warning
        c.beacons["99"] = {"id": "99", "type": "object",
                           "object_name": "Wdeadbeef"}
        # one broadcast warning not yet advertised, one non-broadcast (ignored)
        warnings = [self._w(wid="11111111aaaa", broadcast=True, gw=None),
                    self._w(wid="22222222bbbb", broadcast=False, gw=None)]
        out = b.reconcile(warnings)
        self.assertEqual(out["created"], 1)
        self.assertEqual(out["killed"], 1)
        self.assertNotIn("99", c.beacons)                    # orphan removed
        names = [x.get("object_name") for x in c.beacons.values()]
        self.assertIn("W11111111", names)                    # missing created

    def test_reconcile_readvertises_on_send_path_change(self):
        # Change a live alert's destination (IS -> RF): the reconciler kills the
        # old beacon and re-advertises on the new path.
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        w = self._w(wid="33333333cccc", broadcast=True, gw=None)
        w["send_path"] = "is_only"
        b.reconcile([w])                                     # first advertise
        first_id = w["gw_beacon_id"]
        self.assertIsNotNone(first_id)
        self.assertEqual(w["gw_send_path"], "is_only")
        w["send_path"] = "rf"                                # operator switches
        b.reconcile([w])
        self.assertEqual(w["gw_send_path"], "rf")
        self.assertNotEqual(w["gw_beacon_id"], first_id)     # a fresh beacon
        live = [x for x in c.beacons.values() if x.get("type") == "object"]
        self.assertEqual(len(live), 1)                       # no duplicate on air
        self.assertEqual(live[0]["send_path"], "rf")         # on the new path

    def test_reconcile_ignores_non_oasis_beacons(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        c.beacons["7"] = {"id": "7", "type": "object", "object_name": "REPEATER1"}
        b.reconcile([])                                      # no broadcast warnings
        self.assertIn("7", c.beacons)                        # operator beacon untouched

    def test_reconcile_sets_gw_beacon_id_on_created(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        w = self._w(wid="11111111aaaa", broadcast=True, gw=None)
        out = b.reconcile([w])
        self.assertEqual(out["created"], 1)
        self.assertIsNotNone(w["gw_beacon_id"])

    def test_reconcile_orphan_delete_failure_not_counted(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        c.beacons["99"] = {"id": "99", "type": "object", "object_name": "Wdeadbeef"}
        orig_delete = c.delete_beacon
        def boom(bid):
            if bid == "99":
                raise wb.GraywolfError("down")
            orig_delete(bid)
        c.delete_beacon = boom
        out = b.reconcile([])            # orphan present, no broadcast warnings
        self.assertEqual(out["killed"], 0)   # delete failed -> not counted
        self.assertIn("99", c.beacons)       # still on air

    def test_reconcile_ownership_by_source_callsign(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS, source_callsign="W4MHI-1")
        # operator beacon (different callsign) must be untouched even with an
        # OASIS-looking name -- structurally unreachable by the kill/orphan path.
        c.beacons["op"] = {"id": "op", "type": "object", "object_name": "FLOOD09",
                            "callsign": "W4MHI"}
        b.reconcile([])
        self.assertIn("op", c.beacons)                 # not OASIS-owned -> untouched
        # OASIS orphan (our callsign, no matching warning) -> killed
        c.beacons["ours"] = {"id": "ours", "type": "object", "object_name": "AID01",
                              "callsign": "W4MHI-1"}
        b.reconcile([])
        self.assertNotIn("ours", c.beacons)
        self.assertIn("op", c.beacons)                 # still untouched

    def test_advertise_sets_source_callsign_on_beacon(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS, source_callsign="W4MHI-1")
        bid = b.advertise(self._w(aprs_name="AID01"))
        self.assertEqual(c.beacons[bid]["callsign"], "W4MHI-1")

    def test_advertise_uses_per_warning_send_path(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS, source_callsign="W4MHI-1", send_path="both")
        w = {"id":"x","aprs_name":"AID01","type":"first_aid","lat":47.5,"lon":-122.0,
             "note":"","broadcast":True,"gw_beacon_id":None,"send_path":"is_only"}
        b.advertise(w)
        self.assertEqual(next(iter(c.beacons.values()))["send_path"], "is_only")   # not the "both" default

    def test_kill_uses_per_warning_send_path(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS, source_callsign="W4MHI-1", send_path="both")
        w = {"id":"y","aprs_name":"AID02","type":"first_aid","lat":47.5,"lon":-122.0,
             "note":"","gw_beacon_id":None,"send_path":"rf"}
        b._send_kill(w)
        # the transient custom (kill) beacon created carried send_path "rf"
        self.assertTrue(any(bc.get("type")=="custom" and bc.get("send_path")=="rf" for bc in c._created))

    def test_reconcile_matches_warning_by_stored_aprs_name(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS, source_callsign="W4MHI-1")
        w = self._w(wid="anything", aprs_name="AID07", broadcast=True)
        out = b.reconcile([w])
        self.assertEqual(out["created"], 1)
        self.assertEqual(c.beacons[w["gw_beacon_id"]]["object_name"], "AID07")
        # re-running reconcile with the same warning must not re-advertise
        out2 = b.reconcile([w])
        self.assertEqual(out2["created"], 0)


class ReconcileDriverTest(unittest.TestCase):
    def _w(self, wid, **kw):
        base = {"id": wid, "type": "flood", "lat": 47.5, "lon": -122.0,
                "note": "", "broadcast": False, "gw_beacon_id": None,
                "aprs_name": "W" + str(wid)[:8]}
        base.update(kw); return base

    def test_pending_delete_killed_then_removed_on_confirm(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        w = self._w("11111111aaaa", broadcast=True); w["gw_beacon_id"] = b.advertise(w)
        w["pending_delete"] = True; w["broadcast"] = False
        out = b.reconcile([w])
        self.assertIn("11111111aaaa", out["removed"])   # confirmed kill -> removable
        self.assertNotIn(w["gw_beacon_id"], c.beacons)  # object beacon deleted

    def test_pending_delete_not_removed_when_kill_fails(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        w = self._w("22222222bbbb", broadcast=True); w["gw_beacon_id"] = b.advertise(w)
        w["pending_delete"] = True; w["broadcast"] = False
        orig = c.delete_beacon
        c.delete_beacon = lambda bid: (_ for _ in ()).throw(wb.GraywolfError("down")) if bid == w["gw_beacon_id"] else orig(bid)
        out = b.reconcile([w])
        self.assertNotIn("22222222bbbb", out["removed"])  # kill failed -> tombstone stays

    def test_broadcast_off_kills_and_clears_id(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        w = self._w("33333333cccc", broadcast=True); w["gw_beacon_id"] = b.advertise(w)
        w["broadcast"] = False                      # off, id still set -> kill pending
        out = b.reconcile([w])
        self.assertIsNone(w["gw_beacon_id"])        # cleared after confirmed kill
        self.assertGreaterEqual(out["killed"], 1)

    def test_broadcast_on_no_id_advertises_and_sets_id(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        w = self._w("44444444dddd", broadcast=True)  # on, no id
        out = b.reconcile([w])
        self.assertIsNotNone(w["gw_beacon_id"]); self.assertEqual(out["created"], 1)

    def test_on_air_no_id_adopts_existing_beacon_id(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        w = self._w("55555555eeee", broadcast=True); bid = b.advertise(w)
        w["gw_beacon_id"] = None                      # id lost locally but beacon still on air
        b.reconcile([w])
        self.assertEqual(w["gw_beacon_id"], bid)      # re-adopted, no duplicate advertise

    def test_orphan_still_killed(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        c.beacons["99"] = {"id": "99", "type": "object", "object_name": "Wdeadbeef"}
        out = b.reconcile([]); self.assertNotIn("99", c.beacons); self.assertGreaterEqual(out["killed"], 1)


if __name__ == "__main__":
    unittest.main()
