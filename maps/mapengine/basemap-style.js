// OASIS shared offline base-map style — OpenMapTiles schema, dark + rich.
//
// Single source of truth for the MapLibre GL vector style used by the live
// traffic map (maps/traffic/map.html). Served from /maps/mapengine/basemap-style.js
// so any page can load it with an absolute path — no build step, no CDN.
//
// The station's map archives are GrayWolf's downloaded tiles at
// /var/lib/graywolf/tiles/state — OpenMapTiles builds (planetiler), NOT Protomaps.
// So this style targets the OMT schema (water/waterway/landcover/landuse/park/
// transportation/place/…). Glyphs are the vendored Open Sans faces served locally
// from /maps/mapengine/fonts/ — no sprite sheet is used.
//
// It is a DARK base (ours) with the richness that made GrayWolf's Americana style
// look better GRAFTED in on the same OMT source-layers: named protected areas
// (national forests / wilderness / parks), named water bodies, state/region
// labels, and sparse POIs. Route shields are NOT here — those need the
// maplibre-shield-generator runtime plugin (a later step), not a style layer.
//
// Usage:
//   const map = new maplibregl.Map({
//     container: 'map',
//     style: oasisBaseMapStyle('pmtiles://' + location.origin + '/…washington.pmtiles'),
//   });
//
// The vector source is always registered under the id 'basemap'. To swap the
// underlying archive at runtime, call: map.getSource('basemap').setUrl(newUrl).
//
// Layer ids (stable — relied on by map.html for click-inspect + layer toggles):
//   background, water-poly-fill, waterways-line,
//   landcover-wood, landcover-grass, landuse-park,
//   gr-park-fill, gr-park-outline,
//   landuse-residential, landuse-commercial, landuse-industrial, airport-area,
//   road-minor, road-secondary, road-primary, road-motorway,
//   railways, ferry-line, airport-runway, buildings-fill, boundaries,
//   road-labels, place-labels, pois-circle, airport-label, mountain-peak,
//   gr-park-label, gr-water-label, exit-numbers

function oasisBaseMapStyle(sourceUrl, options) {
  options = options || {};
  const origin = options.origin || window.location.origin;

  return {
    version: 8,
    glyphs: origin + '/maps/mapengine/fonts/{fontstack}/{range}.pbf',
    sources: {
      // `attribution` feeds MapLibre's AttributionControl (the corner credit).
      // GrayWolf's archives are OpenMapTiles builds of OpenStreetMap data — the
      // OSM/OMT credit is licence-required; GrayWolf is the map provider.
      basemap: { type: 'vector', url: sourceUrl,
                 attribution: '© OpenStreetMap · OpenMapTiles · GrayWolf' }
    },
    layers: [

      // ── Background ────────────────────────────────────────────────────────
      { id: 'background', type: 'background',
        paint: { 'background-color': '#111827' } },

      // ── Water bodies ──────────────────────────────────────────────────────
      { id: 'water-poly-fill', type: 'fill',
        source: 'basemap', 'source-layer': 'water',
        paint: { 'fill-color': '#1a3a5c' } },

      // ── Waterways ─────────────────────────────────────────────────────────
      { id: 'waterways-line', type: 'line',
        source: 'basemap', 'source-layer': 'waterway',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#1a4a7a',
          'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1, 14, 4]
        }
      },

      // ── Land cover ────────────────────────────────────────────────────────
      { id: 'landcover-wood', type: 'fill',
        source: 'basemap', 'source-layer': 'landcover',
        filter: ['==', ['get', 'class'], 'wood'],
        paint: { 'fill-color': '#1a2e1a', 'fill-opacity': 0.8 } },

      { id: 'landcover-grass', type: 'fill',
        source: 'basemap', 'source-layer': 'landcover',
        filter: ['match', ['get', 'class'], ['grass', 'crop', 'scrub'], true, false],
        paint: { 'fill-color': '#1e2e18', 'fill-opacity': 0.6 } },

      // ── Parks / nature (landuse polygons) ─────────────────────────────────
      { id: 'landuse-park', type: 'fill',
        source: 'basemap', 'source-layer': 'landuse',
        filter: ['match', ['get', 'class'], ['park', 'national_park', 'nature_reserve'], true, false],
        paint: { 'fill-color': '#162616', 'fill-opacity': 0.7 } },

      // ── Protected areas (park layer: national forests / wilderness) ───────
      // Grafted richness — a green fill + faint outline under everything; the
      // matching name labels are pushed on top below.
      { id: 'gr-park-fill', type: 'fill',
        source: 'basemap', 'source-layer': 'park',
        paint: { 'fill-color': 'hsl(140, 34%, 13%)', 'fill-opacity': 0.55 } },
      { id: 'gr-park-outline', type: 'line',
        source: 'basemap', 'source-layer': 'park', minzoom: 6,
        paint: { 'line-color': 'hsl(140, 30%, 26%)', 'line-width': 0.6 } },

      // ── Developed land use ────────────────────────────────────────────────
      { id: 'landuse-residential', type: 'fill',
        source: 'basemap', 'source-layer': 'landuse',
        filter: ['==', ['get', 'class'], 'residential'],
        paint: { 'fill-color': '#1a1a2a', 'fill-opacity': 0.5 } },

      { id: 'landuse-commercial', type: 'fill',
        source: 'basemap', 'source-layer': 'landuse',
        filter: ['match', ['get', 'class'], ['commercial', 'retail'], true, false],
        paint: { 'fill-color': '#1e1a22', 'fill-opacity': 0.5 } },

      { id: 'landuse-industrial', type: 'fill',
        source: 'basemap', 'source-layer': 'landuse',
        filter: ['==', ['get', 'class'], 'industrial'],
        paint: { 'fill-color': '#1a1e22', 'fill-opacity': 0.5 } },

      // ── Airport grounds (aprons / aerodrome polygons) ─────────────────────
      { id: 'airport-area', type: 'fill',
        source: 'basemap', 'source-layer': 'aeroway',
        filter: ['match', ['get', 'class'], ['aerodrome', 'apron'], true, false],
        paint: { 'fill-color': '#1c2130', 'fill-opacity': 0.7 } },

      // ── Roads — minor / service / track / path ────────────────────────────
      { id: 'road-minor', type: 'line',
        source: 'basemap', 'source-layer': 'transportation',
        filter: ['match', ['get', 'class'], ['minor', 'service', 'track', 'path'], true, false],
        minzoom: 12,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#2a2a3a', 'line-width': 1 } },

      // ── Roads — secondary / tertiary ──────────────────────────────────────
      { id: 'road-secondary', type: 'line',
        source: 'basemap', 'source-layer': 'transportation',
        filter: ['match', ['get', 'class'], ['secondary', 'tertiary'], true, false],
        minzoom: 9,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#3a3a4e', 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1, 14, 3] } },

      // ── Roads — primary ───────────────────────────────────────────────────
      { id: 'road-primary', type: 'line',
        source: 'basemap', 'source-layer': 'transportation',
        filter: ['==', ['get', 'class'], 'primary'],
        minzoom: 7,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#4a4a20', 'line-width': ['interpolate', ['linear'], ['zoom'], 7, 1, 14, 4] } },

      // ── Roads — motorway / trunk ──────────────────────────────────────────
      { id: 'road-motorway', type: 'line',
        source: 'basemap', 'source-layer': 'transportation',
        filter: ['match', ['get', 'class'], ['motorway', 'trunk'], true, false],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#8b5a00', 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 1, 14, 6] } },

      // ── Railways (rail / transit) ─────────────────────────────────────────
      { id: 'railways', type: 'line',
        source: 'basemap', 'source-layer': 'transportation',
        filter: ['match', ['get', 'class'], ['rail', 'transit'], true, false],
        minzoom: 9,
        paint: { 'line-color': '#4a4f5e', 'line-dasharray': [3, 3],
                 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.6, 15, 2] } },

      // ── Ferry routes ──────────────────────────────────────────────────────
      { id: 'ferry-line', type: 'line',
        source: 'basemap', 'source-layer': 'transportation',
        filter: ['==', ['get', 'class'], 'ferry'],
        paint: { 'line-color': '#2f5f86', 'line-dasharray': [2, 4], 'line-width': 1 } },

      // ── Airport runways / taxiways ────────────────────────────────────────
      { id: 'airport-runway', type: 'line',
        source: 'basemap', 'source-layer': 'aeroway',
        filter: ['match', ['get', 'class'], ['runway', 'taxiway'], true, false],
        layout: { 'line-cap': 'butt', 'line-join': 'round' },
        paint: { 'line-color': '#5a6472', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1, 15, 5] } },

      // ── Buildings ─────────────────────────────────────────────────────────
      { id: 'buildings-fill', type: 'fill',
        source: 'basemap', 'source-layer': 'building',
        minzoom: 12,
        paint: {
          'fill-color': '#1e2030',
          'fill-outline-color': '#2a2a4a',
          'fill-opacity': ['interpolate', ['linear'], ['zoom'], 12, 0.5, 15, 0.9]
        }
      },

      // ── Administrative boundaries (country / state) ───────────────────────
      { id: 'boundaries', type: 'line',
        source: 'basemap', 'source-layer': 'boundary',
        filter: ['all', ['<=', ['get', 'admin_level'], 4], ['!=', ['get', 'maritime'], 1]],
        layout: { 'line-join': 'round' },
        paint: { 'line-color': '#3b4a5a', 'line-dasharray': [2, 2], 'line-width': 1 } },

      // ── Road labels ───────────────────────────────────────────────────────
      { id: 'road-labels', type: 'symbol',
        source: 'basemap', 'source-layer': 'transportation_name',
        minzoom: 13,
        layout: {
          'symbol-placement':  'line',
          'symbol-spacing':    250,
          'text-field':        ['get', 'name'],
          'text-font':         ['Open Sans Regular'],
          'text-size':         ['interpolate', ['linear'], ['zoom'], 13, 10, 17, 13],
          'text-max-angle':    30,
          'text-padding':      5,
          'text-allow-overlap': false
        },
        paint: {
          'text-color':      '#e8e8ff',
          'text-halo-color': '#080810',
          'text-halo-width': 1.5
        }
      },

      // ── Place labels — cities / towns ─────────────────────────────────────
      // Made prominent per operator: pure white, larger, heavier halo (we only
      // ship Open Sans Regular glyphs, so weight comes from size + halo).
      { id: 'place-labels', type: 'symbol',
        source: 'basemap', 'source-layer': 'place',
        filter: ['match', ['get', 'class'], ['city', 'town'], true, false],
        layout: {
          'text-field':        ['get', 'name'],
          'text-font':         ['Open Sans Regular'],
          'text-size':         ['interpolate', ['linear'], ['zoom'], 5, 12, 10, 15, 14, 18],
          'text-letter-spacing': 0.05,
          'text-anchor':       'center',
          'text-padding':      6,
          'text-max-width':    8,
          'text-allow-overlap': false
        },
        paint: {
          'text-color':      '#ffffff',
          'text-halo-color': '#05070d',
          'text-halo-width': 2
        }
      },

      // ── POIs — villages, suburbs, neighbourhoods ──────────────────────────
      { id: 'pois-circle', type: 'symbol',
        source: 'basemap', 'source-layer': 'place',
        filter: ['match', ['get', 'class'], ['village', 'suburb', 'hamlet', 'neighbourhood', 'quarter'], true, false],
        minzoom: 9,
        layout: {
          'text-field':        ['get', 'name'],
          'text-font':         ['Open Sans Regular'],
          'text-size':         ['interpolate', ['linear'], ['zoom'], 9, 10, 14, 13],
          'text-anchor':       'center',
          'text-padding':      4,
          'text-allow-overlap': false
        },
        paint: {
          'text-color':      '#b0b0c0',
          'text-halo-color': '#080810',
          'text-halo-width': 1.2
        }
      },

      // ── Airport labels ────────────────────────────────────────────────────
      { id: 'airport-label', type: 'symbol',
        source: 'basemap', 'source-layer': 'aerodrome_label',
        minzoom: 10,
        layout: { 'text-field': ['get', 'name'], 'text-font': ['Open Sans Regular'],
                  'text-size': 11, 'text-anchor': 'top', 'text-offset': [0, 0.4] },
        paint: { 'text-color': '#9fb2c9', 'text-halo-color': '#080810', 'text-halo-width': 1.2 } },

      // ── Mountain peaks ────────────────────────────────────────────────────
      { id: 'mountain-peak', type: 'symbol',
        source: 'basemap', 'source-layer': 'mountain_peak',
        minzoom: 9,
        layout: {
          'text-field':   ['concat', ['get', 'name'], '\n', ['get', 'ele_ft'], 'ft'],
          'text-font':    ['Open Sans Regular'],
          'text-size':    10,
          'text-anchor':  'top',
          'text-offset':  [0, 0.3]
        },
        paint: {
          'text-color':      '#a0c0a0',
          'text-halo-color': '#080810',
          'text-halo-width': 1
        }
      },

      // ── Grafted labels (Americana-style richness, authored dark) ──────────
      // Named protected areas — "Mt Baker-Snoqualmie National Forest" etc.
      // Label ONLY the OMT point anchors, never the polygons: labeling a park
      // polygon places text at its per-tile-clipped centroid, and a degenerate
      // clip (a big national-forest polygon barely entering a tile) yields a NaN
      // placement that blanks the ENTIRE tile (all layers). The `park` layer ships
      // point features as the proper label anchors — use those.
      { id: 'gr-park-label', type: 'symbol',
        source: 'basemap', 'source-layer': 'park', minzoom: 6,
        filter: ['==', ['geometry-type'], 'Point'],
        layout: {
          'text-field': ['get', 'name'], 'text-font': ['Open Sans Regular'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 6, 10, 12, 13],
          'text-max-width': 8, 'text-padding': 6
        },
        paint: { 'text-color': 'hsl(140, 45%, 62%)', 'text-halo-color': '#06120a', 'text-halo-width': 1.4 } },

      // Named water bodies (lakes / large rivers)
      { id: 'gr-water-label', type: 'symbol',
        source: 'basemap', 'source-layer': 'water_name', minzoom: 8,
        layout: {
          'text-field': ['get', 'name'], 'text-font': ['Open Sans Regular'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 8, 10, 14, 13],
          'symbol-placement': 'point', 'text-max-width': 7
        },
        paint: { 'text-color': 'hsl(205, 55%, 68%)', 'text-halo-color': '#04101c', 'text-halo-width': 1.3 } },

      // (POI-name labels intentionally omitted — see memory poi-labels-blank-tile:
      // a `poi`-layer symbol layer blanked whole tiles in dense retail areas.)

      // Highway exit numbers — OMT motorway-junction points carry the exit `ref`.
      // High-zoom only (navigation detail); white-on-green to read like an exit sign.
      { id: 'exit-numbers', type: 'symbol',
        source: 'basemap', 'source-layer': 'transportation_name',
        filter: ['all', ['==', ['get', 'subclass'], 'junction'], ['has', 'ref']],
        minzoom: 13,
        layout: {
          'text-field': ['concat', 'Exit ', ['get', 'ref']],
          'text-font': ['Open Sans Regular'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 13, 10, 16, 12],
          'text-anchor': 'center', 'text-padding': 4,
          // Exits are navigation-critical — always draw them, even over street
          // labels. This layer is last (top z-order), so "Exit N" wins visually;
          // its green halo keeps it legible above whatever's beneath.
          'text-allow-overlap': true, 'text-ignore-placement': true
        },
        paint: { 'text-color': '#ffffff', 'text-halo-color': 'hsl(130, 55%, 20%)', 'text-halo-width': 2.2 } }
    ]
  };
}
