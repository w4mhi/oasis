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
        w = {"id": "3f9a1b2cdeadbeef", "lat": 47.5495, "lon": -122.0298, "note": "road out"}
        p = wb.object_payload(w, "\\", "w", "both", 1800)
        self.assertEqual(p["type"], "object")
        self.assertEqual(p["object_name"], "W3f9a1b2c")
        self.assertEqual(p["symbol_table"], "\\")
        self.assertEqual(p["symbol"], "w")
        self.assertEqual(p["send_path"], "both")
        self.assertEqual(p["interval"], 1800)
        self.assertEqual(p["comment"], "road out")
        self.assertAlmostEqual(p["latitude"], 47.5495)

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


class FakeClient:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.beacons = {}      # id -> payload
        self.sent = []         # beacon ids that got send_now
        self._n = 0
    def health(self): return self.healthy
    def create_beacon(self, payload):
        if not self.healthy: raise wb.GraywolfError("down")
        self._n += 1; bid = str(self._n)
        rec = dict(payload); rec["id"] = bid; self.beacons[bid] = rec
        # object_name is echoed for object beacons
        return bid
    def update_beacon(self, bid, payload):
        if bid in self.beacons: self.beacons[bid].update(payload)
    def delete_beacon(self, bid): self.beacons.pop(bid, None)
    def send_now(self, bid): self.sent.append(bid)
    def list_beacons(self): return list(self.beacons.values())


SYMS = {"flood": ("\\", "w"), "fire": ("/", ":")}


class BroadcasterTest(unittest.TestCase):
    def _w(self, wid="3f9a1b2cdead", typ="flood", broadcast=True, gw=None):
        return {"id": wid, "type": typ, "lat": 47.5, "lon": -122.0,
                "note": "x", "broadcast": broadcast, "gw_beacon_id": gw}

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

    def test_reconcile_ignores_non_oasis_beacons(self):
        c = FakeClient(); b = wb.WarningBroadcaster(c, SYMS)
        c.beacons["7"] = {"id": "7", "type": "object", "object_name": "REPEATER1"}
        b.reconcile([])                                      # no broadcast warnings
        self.assertIn("7", c.beacons)                        # operator beacon untouched

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


if __name__ == "__main__":
    unittest.main()
