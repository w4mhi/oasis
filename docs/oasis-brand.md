# OASIS — Brand Reference

## **Off-grid Amateur Station Information Suite**

### Name

**OASIS** in the title; the full name — *Off-grid Amateur Station Information
Suite* — as the subtitle.

### Tagline

> *Comms when the network's gone dark.*

## Why OASIS

The name captures the project's core identity in one word: an island of working
communications in a desert of failed infrastructure. Every feature — offline
maps, ICS forms, FCC lookup, APRS, Winlink helpers, calculators — exists to
keep operators functional when nothing else is.

The acronym expands naturally to a one-line description: no forced words,
no redundancy.

## Colors

Sourced from `css/common.css`. Do not change accent or background without
updating the CSS variables.

| Role            | Token            | Hex       |
|-----------------|------------------|-----------|
| Background      | `--bg`           | `#0d1117` |
| Panel           | `--panel`        | `#161b22` |
| Border          | `--border`       | `#2a3441` |
| **Accent**      | `--accent`       | `#00d98b` |
| Accent dim      | `--accent-dim`   | `#0a7a52` |
| Amber (labels)  | `--amber`        | `#ffb454` |
| Text            | `--text`         | `#d7e0ea` |
| Text dim        | `--text-dim`     | `#8b97a6` |

The accent green (`#00d98b`) is the primary brand color — used for headings,
active indicators, and interactive elements. It reads as "alive / operational",
which is exactly right for an emergency communications dashboard.

`common.css` also defines secondary surface and status tokens — `--panel-2`
(`#1c2330`), `--amber-dim` (`#c98a2e`), `--red` (`#ff6b6b`), `--green`
(`#3fb950`), `--blue` (`#58a6ff`) — for nested panels and status indicators.
The palette above is the core brand set; these are functional accents.

## Typography

Monospace throughout (`--mono`: `ui-monospace`, SF Mono, Cascadia Mono, Roboto
Mono, Menlo, Consolas). The mono font reinforces the terminal / field-station
aesthetic and ensures consistent rendering of callsigns, frequencies, and
grid squares.

For icons, a bundled **NotoEmoji** font (`static/dependencies/fonts/`, loaded
via `@font-face` as the `--emoji` fallback) renders emoji on a headless Pi or
minimal Linux that has no system emoji font — staying true to offline-first.

## Voice

- Direct. No fluff.
- Operator-first. Assume the reader holds an amateur radio license.
- Offline-first framing. Features are described by what they do with no
  internet, not what they do with internet.

## Namespace

| Surface        | Value                  |
|----------------|------------------------|
| Display name   | OASIS                  |
| Full name      | Off-grid Amateur Station Information Suite |
| GitHub repo    | `oasis-emcomm`         |
| PyPI package   | `oasis-emcomm` *(reserved — runs from source, not published)* |
| Domain (avail) | `oasis-ham.com`        |

## Concept credit

KM4ACK (original *ACK Off-Grid Ham Radio Server* concept) / W4MHI (OASIS extension)
