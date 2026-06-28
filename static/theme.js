/*
 * theme.js — shared light/dark theme toggle for OASIS pages.
 *
 * Injected automatically before </head> by the Flask server (see the
 * after_request hook in server/app.py) on every owned HTML page except the
 * ones that manage their own theming (the 7" kiosk, the APRS map, and the
 * graywolf-handbook). Loaded synchronously in <head> so the saved theme is
 * applied before first paint (no flash).
 *
 * Pages that already provide their own toggle button (e.g. index.html, which
 * has one in its top bar) are detected and left alone — only the floating
 * button is skipped; the theme is still applied.
 */
(function () {
  "use strict";
  var KEY = "oasis_theme";

  // 1) Apply the saved theme as early as possible (this runs during <head>
  //    parsing, before the body paints). Dark is the default.
  try {
    if (localStorage.getItem(KEY) === "light")
      document.documentElement.dataset.theme = "light";
  } catch (e) {}

  function isLight() { return document.documentElement.dataset.theme === "light"; }
  // Show the theme you'd switch TO: a sun while in dark, a moon while in light.
  function glyph()   { return isLight() ? "\u263E" : "\u2600"; }  // ☾ moon / ☀ sun

  // Exposed globally so an in-page button (index.html) can call it too.
  window.toggleTheme = function () {
    var light = isLight();
    if (light) delete document.documentElement.dataset.theme;
    else       document.documentElement.dataset.theme = "light";
    try { localStorage.setItem(KEY, light ? "dark" : "light"); } catch (e) {}
    var g = document.getElementById("theme-glyph");
    if (g) g.textContent = glyph();
  };

  // 2) Drop a floating toggle button in — unless the page already has one.
  function injectStyle() {
    if (document.getElementById("theme-fab-style")) return;
    var css =
      ".theme-toggle-fab{position:fixed;top:.55rem;right:.6rem;z-index:9999;" +
      "width:1.9rem;height:1.9rem;display:inline-flex;align-items:center;" +
      "justify-content:center;padding:0;border:1px solid var(--border);" +
      "border-radius:999px;background:var(--panel-2);color:var(--text);" +
      "font-size:1rem;line-height:1;cursor:pointer;" +
      "transition:color .15s,border-color .15s,background .15s}" +
      ".theme-toggle-fab:hover{color:var(--accent);border-color:var(--accent-dim)}";
    var s = document.createElement("style");
    s.id = "theme-fab-style";
    s.textContent = css;
    (document.head || document.documentElement).appendChild(s);
  }

  function injectButton() {
    if (document.getElementById("theme-toggle")) return;   // page provides its own
    if (!document.body) return;
    injectStyle();
    var btn = document.createElement("button");
    btn.id = "theme-toggle";
    btn.className = "theme-toggle-fab";
    btn.type = "button";
    btn.title = "Toggle light / dark theme";
    btn.setAttribute("aria-label", "Toggle light / dark theme");
    btn.addEventListener("click", window.toggleTheme);
    var g = document.createElement("span");
    g.id = "theme-glyph";
    g.textContent = glyph();
    btn.appendChild(g);
    document.body.appendChild(btn);
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", injectButton);
  else
    injectButton();
})();
