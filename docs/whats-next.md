# What's Next — OASIS Development Roadmap

Planned improvements and new features. Items are grouped by area and roughly
ordered by priority within each group. This is a living document — completed
items are removed, not crossed out.

---

## Net Logger

- **Lat/lon in CSV export.** Exported net logs will include the latitude and
  longitude for each check-in so that re-imported logs display accurate map
  positions rather than falling back to grid square approximations.

---

## Tools & Calculators

- **Grid / Bearing — manual lat/lon input.** The grid calculator will accept
  GPS coordinates (decimal degrees) as an alternative to Maidenhead grid
  squares, improving accuracy for operators who know their precise location.

- **Grid / Bearing — great-circle path visualization.** The calculator will
  display short-path and long-path bearings on a simple SVG globe sketch so
  operators can confirm antenna orientation at a glance.

---

## Winlink

- **Position report workflow completion.** `winlink/position-report.html` and
  `winlink/to-position.html` will be reviewed and completed to cover the full
  Winlink position reporting workflow — paste, parse, display, export — as a
  coherent end-to-end tool.

- **Radio settings content expansion.** `winlink/radio-settings.html` will be
  expanded to cover all radio models present in `radio-cards/` and
  `repeater-guide/`, keeping settings synchronized across the suite.

---

## Radio Reference Content

- **Radio cards for DMR handhelds.** DMR-capable handhelds (Anytone, Ailunce
  HD1) are increasingly common in EmComm deployments. Radio cards covering
  zone/channel programming and DMR-specific menu navigation will be added.


