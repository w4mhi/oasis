# Security Policy

## The threat model, stated plainly

**OASIS has no authentication, and that is deliberate.** It binds to `0.0.0.0`
so every phone, tablet, and laptop on the local network can reach it, and it
treats everyone who can reach it as an operator. That is the right design for a
field station on its own hotspot, where the alternative — a login prompt on a
handheld in the rain, at night, during an incident — costs more than it protects.

It is the **wrong** design for anything reachable from the internet.

> **Do not port-forward OASIS. Do not expose it to an untrusted network, a
> shared/public Wi-Fi, or a hotel LAN.** If you need remote access, put it
> behind a VPN (WireGuard/Tailscale) or an authenticating reverse proxy and
> treat *that* as your security boundary.

Anyone who can reach the OASIS port can, by design:

- read and modify station configuration, the net log, and ICS form data;
- start and stop services, and reassign radio hardware between them;
- transmit on the air through the APRS, Winlink, and warning-broadcast paths;
- browse files through the file browser, within its configured roots;
- open a root-capable shell if Web SSH (ttyd) is installed.

That last group is the important one. **A vulnerability that lets a
same-LAN user do something in that list is not a vulnerability — it is the
documented design.** Please don't file those.

## What *is* a vulnerability

Reports are very welcome for anything that breaks the boundary above:

- **Escaping the intended boundary** — path traversal out of the file browser
  or tile roots; reading files outside the configured user folders.
- **Privilege escalation on the host** — command injection into a shell-out,
  an unsafe `sudo` grant in the installers, a writable path in a systemd unit
  or sudoers rule that a non-root user could exploit to get root.
- **Cross-origin attack from a browser** — a CSRF hole that lets a malicious
  page an operator visits act on their station, or an XSS that executes
  attacker-controlled script in the dashboard. These matter *even in the
  trusted-LAN model*, because the attacker doesn't need to be on the LAN.
- **Unintended transmission** — anything that causes the station to key up or
  beacon without operator action. On amateur radio this is a licensing
  problem, not just a bug.
- **Secret disclosure** — a Winlink password, Wi-Fi PSK, or API token written
  to a world-readable file, leaked into a log, or returned by an API.
- **Supply chain** — a problem in how the offline bundle fetches and verifies
  the third-party payloads listed in
  [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## Reporting

Please report privately first, so a fix can land before the details are public:

1. **Preferred** — open a [GitHub security advisory](https://github.com/W4MHI/oasis/security/advisories/new)
   on this repository. This is private to you and the maintainer.
2. If that isn't available to you, open a normal issue that says only *"security
   report, please contact me"* with **no technical detail**, and wait to be
   contacted.

Please include, if you can: the OASIS version (`version.json` or the dashboard
header), the platform (Pi OS release + model, or desktop OS), what you did, what
happened, and what you expected. A minimal reproduction is worth more than a
scanner report — **automated-scanner output with no demonstrated exploit will
generally be closed**, because "no auth on the LAN" is the design and a scanner
will find it every time.

## What to expect

OASIS is maintained by **one person** as a hobby project, alongside a job and a
radio bench. There is no security team and no SLA. Realistically:

- an acknowledgement within about a week;
- an assessment of whether it falls inside the boundary above;
- for a real issue, a fix in `main` and a note in [CHANGELOG.md](CHANGELOG.md)
  crediting you, unless you'd rather not be named.

If a report is urgent and you've heard nothing in two weeks, please bump the
thread.

## Supported versions

Only the **latest release on `main`** is supported. There are no backports and
no long-term-support branches. Upgrading is a code pull plus a service restart —
see [Updating OASIS](docs/SETUP.md#updating-oasis).

## Operator hardening notes

Nothing here changes the trusted-LAN model, but if you want to reduce exposure:

- **Skip Web SSH** if you don't need a browser terminal — it is the single
  largest grant of authority in the suite.
- **Don't install service controls** if nobody needs to start/stop daemons
  from the dashboard; the feature adds a `sudo` grant scoped to specific
  `systemctl` invocations.
- **Use the AP fallback** (`OASIS` hotspot) rather than joining a network you
  don't control. Note the default PSK is *not* a secret — change it, and see
  [Using OASIS in the field](docs/SETUP.md#using-oasis-in-the-field-no-internet).
- **Keep the resource guardian on** if you run unattended. It won't stop an
  attacker, but it will stop a runaway service from cooking the Pi.
