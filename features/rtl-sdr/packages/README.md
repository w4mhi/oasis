# Feature-local vendored packages — RTL-SDR

The offline `.deb` packages for this feature live here (co-located with the
feature, not in the shared `offline-packages/` tree), so removing
`features/rtl-sdr/` removes everything the feature added.

Layout (written by `create-oasis-offline.py`, resolved via the manifest's
`bundle_base` + `bundle_group`, suite-scoped):

```
packages/rtl-sdr/<suite>/*.deb     # RTL-SDR driver + tools   (bundle_group "rtl-sdr")
packages/direwolf/<suite>/*.deb    # Direwolf packet modem    (bundle_group "direwolf")
```

`<suite>` is the Pi OS release (e.g. `bookworm`, `trixie`). The `.deb` files are
gitignored (`features/*/packages/**/*.deb`) but bundled into the offline image by
the build's tree-walk. The installer (`install-rtl-sdr.py` → `common`-less
`rtl_sdr.py`) reads them from here via `manifest.bundle_dir(...)`.
