"""
common/sudo_grant.py
--------------------
One question, asked honestly: **will sudo run this command WITHOUT a password?**

Everything OASIS does with root — the dashboard's service buttons, the boot
reconciler's `sudo -n systemctl start …`, the setup wizard's Reboot — runs
non-interactively as the operator, so the only thing that matters is whether a
NOPASSWD rule covers the exact command. No password can ever be supplied: there
is no terminal, and the web layer must never hold a credential.

Why this module exists instead of `sudo -n -l <cmd>`, which is what the two
call sites used to do:

    `sudo -n -l <cmd>` answers "is this user AUTHORISED to run <cmd>", not
    "can this user run <cmd> without a password".

Measured on pi5draws, 2026-08-17, with the grant file predating oasis-nwr:

    $ sudo -n -l /bin/systemctl restart oasis-nwr.service   ->  rc=0
    $ sudo -n    /bin/systemctl restart oasis-nwr.service   ->  "sudo: a
                                                                password is
                                                                required"

The operator is in the `sudo` group, so the listing lookup matches the blanket
`(ALL : ALL) ALL` entry from /etc/sudoers.d and answers yes for ANY command on
the box — including commands no NOPASSWD rule mentions, and including units
that do not exist. The NOPASSWD entry is a separate line, and oasis-nwr was not
in it. Both probes therefore reported "granted": start-oasis skipped the
re-grant, `_systemctl_seq` swallowed the refusal, and the always-on weather
watch did not come back after a reboot while the dashboard stayed green.

So parse the tags. `sudo -n -l` with no command prints every entry with its
tags, which is the only place the NOPASSWD/PASSWD distinction is visible:

    User pi may run the following commands on pi5draws:
        (ALL : ALL) ALL
        (root) NOPASSWD: /usr/bin/systemctl start graywolf.service, \\
            /usr/bin/systemctl stop graywolf.service, …

Notes on reading that output, each of which has already bitten:

* The NOPASSWD entry is ONE entry however long it is — ours lists 5 actions ×
  11 units plus five more commands. sudo wraps it with a trailing backslash and
  a 4-space indent, so the lines must be re-joined before splitting on commas.
* Tags are sticky within an entry and are re-printed when they change
  (`NOPASSWD: /bin/a, PASSWD: /bin/b`), so tag state is tracked per entry.
* The rule names `/usr/bin/systemctl`; a probe hardcoding `/bin/systemctl` is
  testing a path the rule never mentions. Commands are compared by basename.
* Commands are compared token by token, never by substring: `oasis-nwr.service`
  must not be answered by a grant for `oasis-nwr-foo.service`.

`-n` everywhere: a probe that can prompt is a probe that can hang the startup
path or the setup page on a password prompt nobody will ever see.
"""

import os
import re
import subprocess

# A policy lookup is cheap; anything slower than this is a broken sudo.
SUDO_LIST_TIMEOUT_S = 5

# Tags sudo prints ahead of a command in `sudo -l` output (NOEXEC:, SETENV:,
# LOG_INPUT:, …). Only PASSWD/NOPASSWD change the answer; the rest are skipped.
_TAG_RE = re.compile(r"^([A-Z_]+):\s*")


def sudo_list(run=None):
    """The stdout of `sudo -n -l`, or None if sudo would not answer.

    Non-zero exit means "no entries, or answering would need a password" —
    either way nothing is proven, so the caller must read None as "not
    granted". `listpw` defaults to `any`, i.e. sudo answers `-l` without
    authenticating as soon as the user has ANY NOPASSWD entry, so a box that
    has been granted always gets an answer here.
    """
    run = run or subprocess.run
    try:
        r = run(["sudo", "-n", "-l"], capture_output=True, text=True,
                timeout=SUDO_LIST_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout or ""


def _logical_lines(listing):
    """`sudo -l` output with its backslash line-wrapping undone.

    Our grant is a single entry hundreds of characters long; sudo wraps it at
    the terminal width. Splitting the raw output on newlines would hand each
    fragment to the parser as if it were its own entry, and the fragments do
    not start with a runas spec, so every one of them would be dropped.
    """
    lines = []
    for raw in (listing or "").splitlines():
        if lines and lines[-1].endswith("\\"):
            lines[-1] = lines[-1][:-1].rstrip() + " " + raw.strip()
        else:
            lines.append(raw.rstrip())
    return lines


def nopasswd_commands(listing):
    """Every command in *listing* that carries an effective NOPASSWD tag, as a
    token list (`["/usr/bin/systemctl", "restart", "oasis-nwr.service"]`).

    A blanket passwordless operator shows up here as the single token `ALL`.
    """
    out = []
    for line in _logical_lines(listing):
        line = line.strip()
        if not line.startswith("("):      # headers, Defaults, blank lines
            continue
        _, _, rest = line.partition(")")  # drop the "(root)" / "(ALL : ALL)" runas
        nopasswd = False
        for spec in rest.split(","):
            spec = spec.strip()
            while True:
                m = _TAG_RE.match(spec)
                if not m:
                    break
                if m.group(1) == "NOPASSWD":
                    nopasswd = True
                elif m.group(1) == "PASSWD":
                    nopasswd = False
                spec = spec[m.end():]
            if nopasswd and spec:
                out.append(spec.split())
    return out


def nopasswd_covers(listing, tokens):
    """True when *listing* has a NOPASSWD entry covering the command *tokens*.

    The general form of nopasswd_covers_systemctl() below, for the commands
    that are not systemctl: the tcpdump feed probe, the zero-arg apply-hardware
    and reboot, the validating eeprom wrapper.

    Matched the same way, and for the same reasons: the program by BASENAME
    (the rule names /usr/bin/tcpdump, the box may have /usr/sbin/tcpdump), the
    arguments by WHOLE TOKEN and in order (a grant is for one exact argv, not
    for anything containing it). A `*` in the granted entry — the sudoers
    wildcard our two self-validating wrappers are granted with — matches
    whatever token stands in its place. Blanket `NOPASSWD: ALL` covers
    everything, because it does.
    """
    for granted in nopasswd_commands(listing):
        if granted == ["ALL"]:
            return True
        if len(granted) != len(tokens):
            continue
        if os.path.basename(granted[0]) != os.path.basename(tokens[0]):
            continue
        if all(g == "*" or g == t for g, t in zip(granted[1:], tokens[1:])):
            return True
    return False


def nopasswd_covers_systemctl(listing, unit, actions=("restart",)):
    """True when *listing* has a NOPASSWD entry for `systemctl <action> <unit>`.

    Blanket `NOPASSWD: ALL` counts: the command will run without a password and
    there is genuinely nothing left for a sudoers rule to grant. That is the
    stock Raspberry Pi OS case (/etc/sudoers.d/010_<user>-nopasswd) and it was
    always the right answer for the sudoers half — see grants_are_current() in
    scripts/enable-service-controls.py for the half it is NOT the right answer
    for.

    Matching is by basename and by whole token, so /bin vs /usr/bin does not
    matter and `oasis-nwr.service` is never answered by `oasis-nwr-x.service`.
    """
    return any(nopasswd_covers(listing, ["systemctl", action, unit])
               for action in actions)


def systemctl_nopasswd_granted(unit, actions=("restart",), run=None):
    """True when `sudo -n systemctl <action> <unit>` will actually run here.

    Root answers True without asking sudo anything: uid 0 runs systemctl with
    no sudoers rule at all, and `sudo -l` as root prints an untagged
    `(ALL : ALL) ALL`, which would otherwise read as "needs a password" and
    re-grant on every single start.

    Anything unproven is False. Re-running the grant is idempotent; assuming a
    permission the station does not have is the silent failure this exists to
    end.
    """
    if os.geteuid() == 0:
        return True
    listing = sudo_list(run=run)
    if listing is None:
        return False
    return nopasswd_covers_systemctl(listing, unit, actions)
