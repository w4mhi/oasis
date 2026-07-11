# Vendored wheels for the GeekPi ZP-0129 case feature

Drop the **`rpi_ws281x`** wheel here — built once for your Pi's architecture and
Python version — e.g.:

```
rpi_ws281x-5.0.0-cp311-cp311-linux_aarch64.whl   # Pi 4/5, 64-bit Bookworm (Python 3.11)
```

Get it on an online Pi of the same OS/arch:

```bash
pip download rpi_ws281x -d features/geek-pi-case/wheels/
```

The installer (`install-geek-pi-case.py`) installs it offline with
`pip install --no-index --find-links features/geek-pi-case/wheels …`.

**Why here (not `offline-packages/`)?** The wheel lives with the feature so the
directory is self-contained: delete `features/geek-pi-case/` and the system is
clean — no orphaned artifacts left behind. The `*.whl` files are gitignored
(`features/*/wheels/*.whl`) but are bundled into the offline image by the
tree-walk in `create-oasis-offline.py`.
