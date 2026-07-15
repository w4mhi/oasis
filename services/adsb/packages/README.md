# Feature-local vendored packages — ADS-B (dump1090-fa)

The offline `.deb` package for `dump1090-fa` lives here (co-located with the
`services/adsb` service, not in the shared `offline-packages/` tree), so
removing `services/adsb/` removes everything the service added.

Layout (written by `create-oasis-offline.py`, resolved via the manifest's
`bundle_base` + `bundle_group`, suite-scoped):

```
packages/dump1090-fa/<suite>/*.deb    # dump1090-fa Mode S/ADS-B decoder   (bundle_group "dump1090-fa")
```

`<suite>` is the Pi OS release (e.g. `bookworm`, `trixie`). The `.deb` files are
gitignored (`services/*/packages/**/*.deb`) but bundled into the offline image
by the build's tree-walk via `manifest.bundle_dir("dump1090-fa")`. The
installer reads them from here at install time.

`dump1090-fa` is distributed by FlightAware, not the base Debian/Raspberry Pi
OS apt repos. `scripts/create-oasis-offline.py` now fetches the package directly
from FlightAware's package indexes per suite/arch and vendors it into this
directory layout during build/update.
