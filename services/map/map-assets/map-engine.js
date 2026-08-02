/* mapengine — a standalone MapLibre GL + PMTiles basemap engine.

   Host-agnostic: it owns the map, the online/offline style swap, auto-fly to a
   selected archive, a zoom readout, and a floating layer-toggle card. The host
   supplies how to build a tile URL and the style/layer definitions, then adds its
   own overlays on engine.map. Designed to drop into any vanilla-JS app (OASIS).

   Requires (loaded before this file): maplibre-gl.js, pmtiles.js, and a style
   provider (basemap-style.js -> window.MapEngineStyle).

   MapEngine.create(opts) -> { map, setBasemap(name|''), basemap, on(ev,fn) }
     opts.container    DOM id or element (default 'map')
     opts.tileUrl(name)-> 'pmtiles://…'  (required for offline basemaps)
     opts.online       { url, attribution } raster fallback
     opts.style        fn(sourceUrl) -> MapLibre style   (default MapEngineStyle.style)
     opts.layerGroups  { group:[layerId,…] }             (default MapEngineStyle.layerGroups)
     opts.layerLabels  [[group,label],…]                 (default MapEngineStyle.layerLabels)
     opts.center,opts.zoom
     opts.zoomReadout  (default true)   opts.layerCard (default true)
     opts.scale        (default true — imperial/miles distance scale bar) */
(function (global) {
  "use strict";

  var protoRegistered = false;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // ── Zoom readout — small control showing the live zoom level ────────────────
  function zoomReadoutControl(map) {
    return {
      onAdd: function () {
        var wrap = el("div", "maplibregl-ctrl me-zoom");
        var lbl = el("span", "me-zoom-lbl", "zoom");
        this._val = el("span", "me-zoom-val", "—");
        wrap.appendChild(lbl); wrap.appendChild(this._val);
        var self = this;
        this._update = function () { self._val.textContent = map.getZoom().toFixed(1); };
        map.on("zoom", this._update);
        this._update();
        this._el = wrap;
        return wrap;
      },
      onRemove: function () { map.off("zoom", this._update); this._el.remove(); },
    };
  }

  // ── Floating layer-toggle card ──────────────────────────────────────────────
  function makeLayerCard(map, groups, labels) {
    var card = el("div", "me-layercard collapsed");
    var head = el("div", "me-lc-head");
    var arrow = el("span", "me-lc-arrow", "▾");
    head.appendChild(arrow);
    head.appendChild(el("span", "me-lc-title", "Map Layers"));
    head.addEventListener("click", function () { card.classList.toggle("collapsed"); });
    var body = el("div", "me-lc-body");

    var boxes = [];
    labels.forEach(function (pair) {
      var group = pair[0];
      var row = el("label", "me-lc-row");
      var cb = el("input");
      cb.type = "checkbox"; cb.checked = true; cb.dataset.group = group;
      cb.addEventListener("change", function () { applyGroup(group, cb.checked); });
      row.appendChild(cb);
      row.appendChild(el("span", null, pair[1]));
      body.appendChild(row);
      boxes.push(cb);
    });

    card.appendChild(head); card.appendChild(body);
    map.getContainer().appendChild(card);

    function applyGroup(group, on) {
      (groups[group] || []).forEach(function (id) {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
      });
    }
    // Re-assert all toggle states onto the current style (after a style swap).
    function apply() { boxes.forEach(function (cb) { applyGroup(cb.dataset.group, cb.checked); }); }
    function setVisible(v) { card.style.display = v ? "" : "none"; }

    return { apply: apply, setVisible: setVisible, el: card };
  }

  function create(opts) {
    opts = opts || {};
    var styleProvider = global.MapEngineStyle || {};
    var styleFn = opts.style || styleProvider.style;
    var layerGroups = opts.layerGroups || styleProvider.layerGroups || {};
    var layerLabels = opts.layerLabels || styleProvider.layerLabels || [];
    var online = opts.online || {};
    var tileUrl = opts.tileUrl;
    var current = opts.basemap || ""; // '' = online raster; else boot on this offline archive

    if (!protoRegistered) {
      maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
      protoRegistered = true;
    }

    function onlineStyle() {
      return {
        version: 8,
        sources: { osm: { type: "raster", tiles: [online.url], tileSize: 256, maxzoom: 19, attribution: online.attribution || "" } },
        layers: [{ id: "osm", type: "raster", source: "osm" }],
      };
    }
    function offlineStyle(name) { return styleFn(tileUrl(name)); }

    var map = new maplibregl.Map({
      container: opts.container || "map",
      style: current ? offlineStyle(current) : onlineStyle(),
      center: opts.center || [-98.35, 39.5],
      zoom: opts.zoom != null ? opts.zoom : 3.2,
      attributionControl: opts.attributionControl !== false,
    });
    // Bottom-left stack, top → bottom (matches OASIS): zoom readout, zoom +/-
    // buttons, then the imperial (miles) distance scale. Added in that order so
    // they stack the same way — the first control sits highest in the corner.
    if (opts.zoomReadout !== false) map.addControl(zoomReadoutControl(map), "bottom-left");
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-left");
    if (opts.scale !== false) map.addControl(new maplibregl.ScaleControl({ unit: "imperial", maxWidth: 120 }), "bottom-left");
    map.on("error", function (e) { console.error("[mapengine] error:", (e && e.error) || e); });

    // Dashboards/panels can finish laying out the container after the map is
    // created; keep the canvas sized to it so the map isn't clipped or blank.
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(function () { map.resize(); }).observe(map.getContainer());
    }

    var layerCard = null;
    if (opts.layerCard !== false && layerLabels.length) {
      layerCard = makeLayerCard(map, layerGroups, layerLabels);
      layerCard.setVisible(false); // no vector layers to toggle until an archive loads
    }
    if (layerCard && current) { // booted straight onto an offline archive
      layerCard.setVisible(true);
      map.once("styledata", function () { layerCard.apply(); });
    }

    function flyToArchive(name) {
      var httpUrl = tileUrl(name).replace(/^pmtiles:\/\//, "");
      new pmtiles.PMTiles(httpUrl).getHeader().then(function (h) {
        if (h && isFinite(h.centerLon) && isFinite(h.centerLat)) {
          map.flyTo({ center: [h.centerLon, h.centerLat], zoom: h.centerZoom || 7, duration: 600 });
        }
      }).catch(function () { /* centering is best-effort */ });
    }

    function setBasemap(name) {
      current = name || "";
      map.setStyle(current ? offlineStyle(current) : onlineStyle());
      if (layerCard) {
        layerCard.setVisible(!!current);
        // Re-apply toggle state once the new style's layers exist.
        if (current) map.once("styledata", function () { layerCard.apply(); });
      }
      if (current) flyToArchive(current);
    }

    return {
      map: map,
      setBasemap: setBasemap,
      get basemap() { return current; },
      on: function (ev, fn) { map.on(ev, fn); },
    };
  }

  global.MapEngine = { create: create };
})(window);
