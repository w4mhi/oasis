/* ============================================================================
   winlink/read-state.js — shared Winlink read/unread state (offline, no deps)

   Used by BOTH the dashboard (index.html → Winlink service card) and the mail
   client (winlink/mail.html) so the unread count agrees everywhere. State lives
   in localStorage (same origin = same store).

   Model: readSet = MIDs considered read; seenSet = every MID we've ever shown.
   The first time we encounter a message we take its read state from Pat's
   `Unread` flag (seed); after that the user's choices (open / mark read / mark
   unread) are authoritative — Pat clears its own Unread flag once a message is
   opened, so we can't lean on it for "mark unread".
   ============================================================================ */
(function (g) {
  const READ_KEY = "oasis_winlink_read";
  const SEEN_KEY = "oasis_winlink_seen";

  function load(key) {
    try { return new Set(JSON.parse(localStorage.getItem(key) || "[]")); }
    catch (_) { return new Set(); }
  }
  function save(key, set) {
    try { localStorage.setItem(key, JSON.stringify([...set])); } catch (_) {}
  }

  let readSet = load(READ_KEY);
  let seenSet = load(SEEN_KEY);

  function midOf(m) { return m && (m.MID != null ? m.MID : m.Mid != null ? m.Mid : m.mid); }
  function patUnread(m) { return !!(m && (m.Unread != null ? m.Unread : m.unread)); }

  const WinlinkRead = {
    midOf,
    isUnread(mid) { return mid != null && !readSet.has(mid); },

    markRead(mid) {
      if (mid != null && !readSet.has(mid)) { readSet.add(mid); save(READ_KEY, readSet); }
    },
    markUnread(mid) {
      if (mid != null && readSet.has(mid)) { readSet.delete(mid); save(READ_KEY, readSet); }
    },
    markAllRead(mids) {
      let changed = false;
      for (const mid of (mids || [])) {
        if (mid != null && !readSet.has(mid)) { readSet.add(mid); changed = true; }
      }
      if (changed) save(READ_KEY, readSet);
    },

    // Take read state from Pat the first time we see each message; never again.
    seed(msgs) {
      let changed = false;
      for (const m of (msgs || [])) {
        const mid = midOf(m);
        if (mid != null && !seenSet.has(mid)) {
          seenSet.add(mid);
          if (!patUnread(m)) readSet.add(mid);
          changed = true;
        }
      }
      if (changed) { save(SEEN_KEY, seenSet); save(READ_KEY, readSet); }
    },

    unreadCount(msgs) {
      let n = 0;
      for (const m of (msgs || [])) if (this.isUnread(midOf(m))) n++;
      return n;
    },
  };

  g.WinlinkRead = WinlinkRead;
})(window);
