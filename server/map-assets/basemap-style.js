// OASIS shared offline base-map style.
//
// Single source of truth for the MapLibre GL vector style used by both the
// offline map viewer (maps/map.html) and the live APRS station map
// (server/aprs/map.html). Served from /map-assets/basemap-style.js so any page can
// load it with an absolute path, no build step, no CDN.
//
// Usage:
//   const map = new maplibregl.Map({
//     container: 'map',
//     style: oasisBaseMapStyle('pmtiles://' + location.origin + '/maps/foo.pmtiles'),
//     center: [...], zoom: ...
//   });
//
// The vector source is always registered under the id 'basemap'. To swap the
// underlying archive at runtime, call: map.getSource('basemap').setUrl(newUrl).
//
// Layer ids (stable — relied on by maps/map.html for click-inspect + toggles):
//   background, water-poly-fill, waterways-line,
//   landcover-wood, landcover-grass, landuse-park,
//   landuse-residential, landuse-commercial, landuse-industrial,
//   road-minor, road-secondary, road-primary, road-motorway,
//   buildings-fill, road-labels, place-labels, pois-circle, mountain-peak

function oasisBaseMapStyle(sourceUrl, options) {
  options = options || {};
  const origin = options.origin || window.location.origin;

  return {
    version: 8,
    glyphs: origin + '/map-assets/fonts/{fontstack}/{range}.pbf',
    sources: {
      basemap: { type: 'vector', url: sourceUrl }
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

      // ── Parks / nature ────────────────────────────────────────────────────
      { id: 'landuse-park', type: 'fill',
        source: 'basemap', 'source-layer': 'landuse',
        filter: ['match', ['get', 'class'], ['park', 'national_park', 'nature_reserve'], true, false],
        paint: { 'fill-color': '#162616', 'fill-opacity': 0.7 } },

      // ── Land use ──────────────────────────────────────────────────────────
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
      { id: 'place-labels', type: 'symbol',
        source: 'basemap', 'source-layer': 'place',
        filter: ['match', ['get', 'class'], ['city', 'town'], true, false],
        layout: {
          'text-field':        ['get', 'name'],
          'text-font':         ['Open Sans Regular'],
          'text-size':         ['interpolate', ['linear'], ['zoom'], 5, 10, 12, 14],
          'text-anchor':       'center',
          'text-padding':      6,
          'text-max-width':    8,
          'text-allow-overlap': false
        },
        paint: {
          'text-color':      '#e0e0e0',
          'text-halo-color': '#080810',
          'text-halo-width': 1.5
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
      }

    ]
  };
}
