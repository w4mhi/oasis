# Feature-local vendored packages — CM4Stack panel

`create-oasis-offline.py` downloads **`m5stack-cm4.dtbo`** (the panel's device-tree
overlay) here, co-located with the feature so removing `displays/cm4stack/` removes
it — nothing left behind in the shared `offline-packages/` tree.

```
packages/m5stack-cm4.dtbo    # single arch-independent overlay (no suite/arch split)
```

The `.dtbo` is gitignored (`displays/*/packages/**/*.dtbo`) but bundled into the
offline image by the build's tree-walk. `install-cm4stack.py` looks for it here
first (`VENDORED_M5_OVERLAY_CANDIDATES`), then in `overlays/` / the feature root as
manual-drop fallbacks.
