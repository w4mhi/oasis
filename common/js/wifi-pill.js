// wifi-pill.js
// -----------------------------------------------------------------------------
// Shared Wi-Fi status-pill logic for index.html and the OASIS Dashboard kiosk, so
// the two pills stay identical: same text, same colour scheme, same signal
// thresholds. Each page maps the returned `tone` to its own theme colour vars via
// matching `.wtone-*` CSS, but the decision lives here once.
//
// Pill scheme (both pages):
//   AP fallback     → text "OASIS", tone 'ap'      (blue)
//   client link     → text = SSID,  tone by signal (green ≥66 / amber ≥33 / red <33;
//                                    'unknown' = neutral when the % isn't available)
//   not connected   → text "Wi-Fi", tone 'none'    (dim)
//
// The signal % drives the COLOUR only — it is never rendered as text.
(function (root) {
  'use strict';

  // Signal (nmcli SIGNAL, 0–100) → degradation tone. null/undefined → 'unknown'.
  function oasisWifiTone(sig) {
    if (sig == null || isNaN(sig)) return 'unknown';
    return sig >= 66 ? 'strong' : sig >= 33 ? 'med' : 'weak';
  }

  // Extract the connected network's signal % from a /api/wifi/scan payload.
  // Prefer nmcli's in_use flag, but fall back to matching the connected SSID by
  // name: nmcli intermittently fails to tag the active AP after a --rescan, which
  // would otherwise blank the signal and flicker the pill to the neutral tone even
  // though the link is fine. `ssid` is the connected SSID from /api/wifi/status.
  // Returns the signal (0–100) or null when it genuinely can't be determined.
  function oasisWifiSignal(scan, ssid) {
    if (!scan || !scan.ok || !Array.isArray(scan.networks)) return null;
    var cur = scan.networks.find(function (n) { return n.in_use; });
    if (!cur && ssid) cur = scan.networks.find(function (n) { return n.ssid === ssid; });
    return cur && typeof cur.signal === 'number' ? cur.signal : null;
  }

  // (status from /api/wifi/status, optional in-use signal %) → {text, tone} for the
  // pill, or null when the pill should be hidden (non-Linux / helper absent).
  function oasisWifiPill(status, signal) {
    if (!status || !status.supported) return null;
    if (status.mode === 'ap') return { text: 'OASIS', tone: 'ap' };
    if (status.mode === 'client' && status.ssid) {
      return { text: status.ssid, tone: oasisWifiTone(signal) };
    }
    return { text: 'Wi-Fi', tone: 'none' };
  }

  root.oasisWifiTone = oasisWifiTone;
  root.oasisWifiSignal = oasisWifiSignal;
  root.oasisWifiPill = oasisWifiPill;
})(typeof window !== 'undefined' ? window : this);
