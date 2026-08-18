"""A tripwire for the lists that go stale when an SDR service is added.

Adding `nwr` to this station meant teaching EIGHT separate hardcoded lists that
a new RTL-SDR consumer exists. Four were found one at a time, each by someone
tripping over a symptom -- a dongle probed in the wrong order, a conflict prompt
that never fired, a STOP ALL that skipped the one always-on receiver, a service
ops button that did nothing. Four more were found later by a deliberate sweep.
Not one of them was caught by a test.

This does NOT assert the lists are correct. It cannot: there is no honest
derivation. `SERVICE_UNITS` maps aprs and openwebrx to EMPTY unit lists (they
are dual-mode / advisory) while `aprs-sdr-feed` and `openwebrx` are both real
entries in SDR_CONSUMING_UNITS, so "every service's units are in the consuming
set" passes vacuously for precisely the shape that would hide a new omission.
A test written that way would look like coverage and provide none.

So this asserts something narrower and true: that the set of services which can
be assigned an RTL-SDR has not changed without a human looking at the list
below. When this fails, the fix is not to edit the expected set and move on --
it is to walk every entry in the docstring, then edit the set.

Places that must learn about a new RTL-SDR service:

  1. common/hardware.py            SERVICE_UNITS, DEVICE_KIND_FOR_SERVICE
  2. common/hardware_detect.py     SDR_CONSUMING_UNITS  (arbitration + can_burn_serial)
  3. server/routes/service_control.py  _OASIS_SERVICES  (feeds _EMERGENCY_STOP:
                                   a service missing here survives a thermal STOP ALL)
  4. scripts/enable-service-controls.py  UNITS  (no sudoers grant = the console's
                                   buttons and the boot reconciler silently no-op)
  5. index.html                    RTL_SDR_CARDS  (the same-dongle conflict prompt)
  6. oasis-dashboard/dashboard.html    SDR_SVCS + the #svcbar markup
                                   (pinned separately by tests/js/kiosk-svcbar.test.js)
  7. server/system/setup.html      serviceMap  (a running service is auto-detected)
  8. services/rtl-feed/common/feed.py  now DERIVED from DEVICE_KIND_FOR_SERVICE,
                                   so it needs no edit -- and is the model for the rest

Item 8 is the shape to copy. Every list above that can be derived from
common/hardware.py should be, and then it drops off this list for good.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import hardware  # noqa: E402

# Every logical service that may be assigned an RTL-SDR dongle, as of 3.88.0.
RTL_SDR_SERVICES = frozenset({"adsb", "aprs", "nwr", "openwebrx", "satellites"})


class SdrServiceTripwireTest(unittest.TestCase):
    def test_the_set_of_rtl_sdr_services_has_not_changed_unnoticed(self):
        actual = frozenset(
            svc for svc, kinds in hardware.DEVICE_KIND_FOR_SERVICE.items()
            if "rtl-sdr" in kinds
        )
        self.assertEqual(
            actual, RTL_SDR_SERVICES,
            "the set of RTL-SDR services changed. Before editing "
            "RTL_SDR_SERVICES, walk the eight places listed in this module's "
            "docstring -- every one of them has been missed at least once, and "
            "each failed silently on a dashboard that stayed green.")


if __name__ == "__main__":
    unittest.main()
