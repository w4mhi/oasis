/* password-reveal.js — an eye toggle for every masked field in the suite.
 *
 * Five password inputs live across three pages (setup.html x3, index.html and
 * dashboard.html share the Wi-Fi one), and none of them could be read back. On
 * a field station that is not cosmetic: Caps Lock left on, `o` typed for `0`,
 * a WPA2 key copied off a label in bad light. The operator finds out only when
 * something downstream fails, and the failure names the wrong cause.
 *
 * GLYPHS ARE INLINE SVG, NEVER EMOJI. The Pi ships no emoji font, so a glyph
 * character renders as tofu. Same shape as common/js/sat-bells.js: viewBox
 * 24x24, fill="currentColor" so it themes with the page, aria-hidden on the
 * art because the button carries the label.
 *
 * The module owns its own markup and CSS. Pages call it and change nothing
 * else -- the Wi-Fi field in index.html/dashboard.html is built by innerHTML
 * at connect time, so there is nothing in the source to decorate up front.
 *
 * Usage:
 *   PasswordReveal.initAll();          // every password input in the document
 *   PasswordReveal.initAll(formEl);    // just this subtree, after innerHTML
 *   PasswordReveal.attach(inputEl);    // one field
 * All three are idempotent.
 */
(function (global) {
  'use strict';

  var EYE =
    '<svg viewBox="0 0 24 24" width="1.05em" height="1.05em" fill="currentColor" ' +
    'aria-hidden="true"><path d="M12 5c-5 0-9.27 3.11-11 7 1.73 3.89 6 7 11 7s9.27-3.11 ' +
    '11-7c-1.73-3.89-6-7-11-7Zm0 12a5 5 0 1 1 0-10 5 5 0 0 1 0 10Zm0-8a3 3 0 1 0 0 6 3 3 ' +
    '0 0 0 0-6Z"/></svg>';

  var EYE_OFF =
    '<svg viewBox="0 0 24 24" width="1.05em" height="1.05em" fill="currentColor" ' +
    'aria-hidden="true"><path d="M2.1 3.51 3.51 2.1l18.39 18.39-1.41 1.41-3.2-3.2A12.4 ' +
    '12.4 0 0 1 12 19c-5 0-9.27-3.11-11-7a12.3 12.3 0 0 1 4.14-4.95L2.1 3.51ZM12 7a5 5 0 ' +
    '0 1 5 5c0 .64-.12 1.25-.34 1.81l-6.47-6.47C10.75 7.12 11.36 7 12 7Zm0-2c5 0 9.27 ' +
    '3.11 11 7a12.35 12.35 0 0 1-2.53 3.53l-1.42-1.42A10.3 10.3 0 0 0 20.82 12C19.17 ' +
    '9.02 15.79 7 12 7c-.4 0-.79.02-1.18.07L9.2 5.45C10.11 5.16 11.04 5 12 5ZM7.03 ' +
    '9.86A5 5 0 0 0 12 17c.35 0 .69-.04 1.02-.11l-1.7-1.7A3 3 0 0 1 8.81 ' +
    '11.64l-1.78-1.78Z"/></svg>';

  var STYLE_ID = 'pw-reveal-style';
  var CSS =
    '.pw-reveal-wrap{position:relative;display:block}' +
    '.pw-reveal-wrap>input{padding-right:2.4em}' +
    '.pw-reveal-btn{position:absolute;top:50%;right:.35em;transform:translateY(-50%);' +
    'display:inline-flex;align-items:center;justify-content:center;' +
    'width:1.9em;height:1.9em;padding:0;margin:0;border:0;border-radius:5px;' +
    'background:transparent;color:inherit;opacity:.62;cursor:pointer;line-height:0}' +
    '.pw-reveal-btn:hover{opacity:1}' +
    '.pw-reveal-btn:focus-visible{outline:2px solid currentColor;outline-offset:1px;opacity:1}' +
    '.pw-reveal-btn[disabled]{display:none}';

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var el = document.createElement('style');
    el.id = STYLE_ID;
    el.textContent = CSS;
    (document.head || document.documentElement).appendChild(el);
  }

  // Pure: everything the toggle decides, with no DOM in sight, so the part
  // worth getting wrong is testable off-browser. Same split as the Python side
  // (dump1090_env_text, broadcast_probe_state) -- logic apart from its I/O.
  function revealState(shown) {
    return {
      type: shown ? 'text' : 'password',
      label: shown ? 'Hide password' : 'Show password',
      pressed: shown ? 'true' : 'false',
      glyph: shown ? EYE_OFF : EYE,
    };
  }

  function setShown(input, btn, shown) {
    // Only the type changes. The value is never read, copied or logged here --
    // the DOM already holds it, and touching it would be the one way this
    // module could leak a credential.
    var st = revealState(shown);
    input.type = st.type;
    btn.innerHTML = st.glyph;
    btn.setAttribute('aria-pressed', st.pressed);
    btn.setAttribute('aria-label', st.label);
    btn.title = st.label;
  }

  function attach(input) {
    if (!input || input.dataset.pwReveal === '1') return null;
    if (String(input.type).toLowerCase() !== 'password') return null;
    ensureStyle();
    input.dataset.pwReveal = '1';

    var wrap = document.createElement('span');
    wrap.className = 'pw-reveal-wrap';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var btn = document.createElement('button');
    btn.type = 'button';           // never submits the form it sits in
    btn.className = 'pw-reveal-btn';
    btn.tabIndex = 0;
    setShown(input, btn, false);
    wrap.appendChild(btn);

    btn.addEventListener('click', function () {
      setShown(input, btn, input.type === 'password');
      input.focus();
    });

    // A disabled field is showing a masked placeholder, not a real value (see
    // setup.html's Change-password flow), so revealing it would display
    // asterisks and imply they are the password. Mirror the state instead.
    function syncDisabled() { btn.disabled = !!input.disabled; }
    syncDisabled();
    if (global.MutationObserver) {
      new MutationObserver(syncDisabled)
        .observe(input, { attributes: true, attributeFilter: ['disabled'] });
    }

    // Re-mask when the page is hidden. These pages run on a kiosk panel in the
    // open, so a revealed key must not survive the operator walking away.
    document.addEventListener('visibilitychange', function () {
      if (document.hidden && input.type === 'text') setShown(input, btn, false);
    });

    return btn;
  }

  function initAll(root) {
    var scope = root || document;
    var found = scope.querySelectorAll('input[type="password"]');
    for (var i = 0; i < found.length; i++) attach(found[i]);
    return found.length;
  }

  global.PasswordReveal = { attach: attach, initAll: initAll,
                            revealState: revealState, EYE: EYE, EYE_OFF: EYE_OFF };
  // In Node `this` at module scope IS module.exports, which is how the other
  // shared modules stay require()-able from tests/js (see wifi-pill.js).
})(typeof window !== 'undefined' ? window : this);
