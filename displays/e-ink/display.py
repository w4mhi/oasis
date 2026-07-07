#!/usr/bin/env python3
"""
Display backend for the OASIS e-ink monitor.

One small abstraction, two backends, chosen automatically:

  * EpdBackend  — drives the real Waveshare panel via the vendored
                  `waveshare_epd` driver (imported by model name from config,
                  e.g. epd2in7_V2). Only importable on the Pi.
  * PngBackend  — when `waveshare_epd` is absent (e.g. a dev laptop), writes
                  the same composed frame to a PNG so the layout is viewable
                  without hardware.

Callers work only with logical, landscape (width x height) 1-bit images and
never care which backend is live. Rotation / 180° flip is applied here, at the
edge, just before the frame reaches the glass.
"""

import importlib


def make_display(cfg, force_simulate=False):
    """Return the EPD backend if the driver imports, else the PNG simulator."""
    disp = cfg["display"]
    if not force_simulate:
        try:
            module = importlib.import_module(f"waveshare_epd.{disp['model']}")
            return EpdBackend(cfg, module)
        except (ImportError, ModuleNotFoundError):
            pass
    return PngBackend(cfg)


class _BaseBackend:
    def __init__(self, cfg):
        self.cfg = cfg
        d = cfg["display"]
        self.width = d["width"]
        self.height = d["height"]
        self.rotate = d.get("rotate", 0)
        self.flip_180 = d.get("flip_180", False)

    def _orient(self, image):
        """Apply configured rotate / flip to a composed logical frame."""
        angle = (self.rotate + (180 if self.flip_180 else 0)) % 360
        return image.rotate(angle) if angle else image

    # Subclasses implement these.
    def init(self):
        raise NotImplementedError

    def show(self, image):
        raise NotImplementedError

    def clear(self):
        raise NotImplementedError

    def sleep(self):
        pass


class EpdBackend(_BaseBackend):
    """Real Waveshare panel."""

    def __init__(self, cfg, module):
        super().__init__(cfg)
        self.epd = module.EPD()
        self.name = f"waveshare_epd.{cfg['display']['model']}"

    def init(self):
        self.epd.init()
        self.epd.Clear()

    def show(self, image):
        frame = self._orient(image)
        self.epd.display(self.epd.getbuffer(frame))

    def clear(self):
        self.epd.Clear()

    def sleep(self):
        self.epd.sleep()


class PngBackend(_BaseBackend):
    """Dev simulator: save the frame to a (scaled) PNG for previewing."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.path = cfg["display"].get("preview_path", "preview.png")
        self.scale = cfg["display"].get("preview_scale", 3)
        self.name = "PNG simulator"

    def init(self):
        pass

    def show(self, image):
        frame = self._orient(image)
        if self.scale and self.scale != 1:
            frame = frame.resize(
                (frame.width * self.scale, frame.height * self.scale),
                resample=0,  # nearest-neighbor: keep the 1-bit look crisp
            )
        frame.convert("L").save(self.path)

    def clear(self):
        pass
