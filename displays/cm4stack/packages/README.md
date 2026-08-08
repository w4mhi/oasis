# Feature-local vendored packages — CM4Stack panel

**The panel's device-tree overlay no longer lives here.** It moved to the repo
root's tracked `overlays/`, where every overlay OASIS ships is found and
installed by one mechanism (`common/overlays.py`). Provenance and the rebuild /
refetch rules are in `overlays/SOURCE.md`.

`common/overlays.py` still SEARCHES this directory, so a bundle built before that
change keeps working — but `scripts/create-oasis-offline.py` now writes
`m5stack-cm4.dtbo` into `overlays/`, and nothing new should be dropped here.

This directory is kept for any future feature-local package that is genuinely
not an overlay (a `.deb`, a wheel), following the same
`features/*/packages/` convention.
