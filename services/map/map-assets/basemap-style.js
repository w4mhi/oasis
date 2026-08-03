// OASIS shared offline base-map style — Protomaps Basemap v4 schema.
//
// Single source of truth for the MapLibre GL vector style used by the live
// traffic map (server/map/map.html). Served from /map-assets/basemap-style.js
// so any page can load it with an absolute path — no build step, no CDN.
//
// The station's map archives are Protomaps builds (extracted from a Protomaps
// planet with the map-download tool), so this style targets the Protomaps
// schema (earth/water/roads/places/pois/…), NOT OpenMapTiles. Glyphs are the
// vendored Open Sans faces served locally from /map-assets/fonts/.
//
// Usage:
//   const map = new maplibregl.Map({
//     container: 'map',
//     style: oasisBaseMapStyle('pmtiles://' + location.origin + '/maps/foo.pmtiles'),
//   });
//
// The vector source is always registered under the id 'basemap'. To swap the
// underlying archive at runtime, call: map.getSource('basemap').setUrl(newUrl).
//
// Layer ids (stable — relied on by map.html for click-inspect + layer toggles):
//   background, earth, landcover,
//   landuse-park, landuse-residential, landuse-commercial, landuse-industrial,
//   landuse-institution, airport-area,
//   water, water-basin, water-dock, water-reef, water-pool, waterways-line,
//   water-labels,
//   road-minor, road-medium, road-major, road-highway, railways, ferry-line,
//   buildings, boundaries, airport-runway, road-labels, airport-label,
//   place-locality, place-region, pois-label

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
      { id: 'background', type: 'background',
        paint: { 'background-color': '#0e141c' } },

      // Land vs water
      { id: 'earth', type: 'fill', source: 'basemap', 'source-layer': 'earth',
        paint: { 'fill-color': '#171d26' } },

      { id: 'landcover', type: 'fill', source: 'basemap', 'source-layer': 'landcover',
        paint: {
          'fill-color': ['match', ['get', 'kind'],
            ['forest', 'wood'], '#16241a',
            ['grassland', 'scrub', 'farmland'], '#182219',
            ['glacier', 'snow'], '#20262e',
            '#171d26'],
          'fill-opacity': 0.7 } },

      { id: 'landuse-park', type: 'fill', source: 'basemap', 'source-layer': 'landuse',
        filter: ['match', ['get', 'kind'],
          ['park', 'national_park', 'nature_reserve', 'recreation_ground', 'cemetery', 'forest'], true, false],
        paint: { 'fill-color': '#15241a', 'fill-opacity': 0.6 } },

      // Developed land use (from the landuse layer) — subtle tinted fills.
      { id: 'landuse-residential', type: 'fill', source: 'basemap', 'source-layer': 'landuse',
        filter: ['==', ['get', 'kind'], 'residential'],
        paint: { 'fill-color': '#191b28', 'fill-opacity': 0.5 } },
      { id: 'landuse-commercial', type: 'fill', source: 'basemap', 'source-layer': 'landuse',
        filter: ['match', ['get', 'kind'], ['commercial', 'retail'], true, false],
        paint: { 'fill-color': '#201a26', 'fill-opacity': 0.5 } },
      { id: 'landuse-industrial', type: 'fill', source: 'basemap', 'source-layer': 'landuse',
        filter: ['match', ['get', 'kind'], ['industrial', 'railway', 'quarry'], true, false],
        paint: { 'fill-color': '#1b1f24', 'fill-opacity': 0.5 } },
      { id: 'landuse-institution', type: 'fill', source: 'basemap', 'source-layer': 'landuse',
        filter: ['match', ['get', 'kind'], ['hospital', 'school', 'university', 'college', 'kindergarten'], true, false],
        paint: { 'fill-color': '#241a1e', 'fill-opacity': 0.45 } },

      // Airport grounds (aerodrome/airfield areas)
      { id: 'airport-area', type: 'fill', source: 'basemap', 'source-layer': 'landuse',
        filter: ['match', ['get', 'kind'], ['aerodrome', 'airfield'], true, false],
        paint: { 'fill-color': '#1c2130', 'fill-opacity': 0.7 } },

      { id: 'water', type: 'fill', source: 'basemap', 'source-layer': 'water',
        paint: { 'fill-color': '#12314f' } },

      // Water sub-kinds — tinted over the base fill so harbours, reservoirs,
      // reefs and pools read distinctly.
      { id: 'water-basin', type: 'fill', source: 'basemap', 'source-layer': 'water',
        filter: ['==', ['get', 'kind'], 'basin'],
        paint: { 'fill-color': '#16405f' } },
      { id: 'water-dock', type: 'fill', source: 'basemap', 'source-layer': 'water',
        filter: ['==', ['get', 'kind'], 'dock'],
        paint: { 'fill-color': '#123f5a' } },
      { id: 'water-reef', type: 'fill', source: 'basemap', 'source-layer': 'water',
        filter: ['==', ['get', 'kind'], 'reef'],
        paint: { 'fill-color': '#1a5f5a', 'fill-opacity': 0.8 } },
      { id: 'water-pool', type: 'fill', source: 'basemap', 'source-layer': 'water',
        filter: ['==', ['get', 'kind'], 'swimming_pool'], minzoom: 14,
        paint: { 'fill-color': '#1e73b8' } },

      // Waterways — rivers/canals/streams carried as lines in the water layer.
      { id: 'waterways-line', type: 'line', source: 'basemap', 'source-layer': 'water',
        filter: ['match', ['get', 'kind'], ['river', 'canal', 'stream'], true, false],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#1a4a7a', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1, 14, 3] } },

      { id: 'water-labels', type: 'symbol', source: 'basemap', 'source-layer': 'water',
        minzoom: 9,
        layout: { 'text-field': ['get', 'name'], 'text-font': ['Open Sans Regular'],
                  'text-size': 11 },
        paint: { 'text-color': '#5b86ad', 'text-halo-color': '#08131f', 'text-halo-width': 1 } },

      // Roads, drawn thin -> thick by class
      { id: 'road-minor', type: 'line', source: 'basemap', 'source-layer': 'roads',
        filter: ['match', ['get', 'kind'], ['minor_road', 'other', 'path'], true, false],
        minzoom: 12,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#2a2f3b', 'line-width': 1 } },

      { id: 'road-medium', type: 'line', source: 'basemap', 'source-layer': 'roads',
        filter: ['==', ['get', 'kind'], 'medium_road'], minzoom: 9,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#3a4051', 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1, 15, 4] } },

      { id: 'road-major', type: 'line', source: 'basemap', 'source-layer': 'roads',
        filter: ['==', ['get', 'kind'], 'major_road'], minzoom: 7,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#565d33', 'line-width': ['interpolate', ['linear'], ['zoom'], 7, 1, 15, 5] } },

      { id: 'road-highway', type: 'line', source: 'basemap', 'source-layer': 'roads',
        filter: ['==', ['get', 'kind'], 'highway'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#9a6410', 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 1, 15, 7] } },

      // Railways (rail) and ferry routes — the remaining roads-layer kinds.
      { id: 'railways', type: 'line', source: 'basemap', 'source-layer': 'roads',
        filter: ['==', ['get', 'kind'], 'rail'], minzoom: 9,
        paint: { 'line-color': '#4a4f5e', 'line-dasharray': [3, 3],
                 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.6, 15, 2] } },
      { id: 'ferry-line', type: 'line', source: 'basemap', 'source-layer': 'roads',
        filter: ['==', ['get', 'kind'], 'ferry'],
        paint: { 'line-color': '#2f5f86', 'line-dasharray': [2, 4], 'line-width': 1 } },

      { id: 'buildings', type: 'fill', source: 'basemap', 'source-layer': 'buildings',
        minzoom: 13,
        paint: { 'fill-color': '#1d2130',
                 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 13, 0.4, 16, 0.85] } },

      { id: 'boundaries', type: 'line', source: 'basemap', 'source-layer': 'boundaries',
        layout: { 'line-join': 'round' },
        paint: { 'line-color': '#3b4a5a', 'line-dasharray': [2, 2], 'line-width': 1 } },

      // Airport runways/taxiways (aeroway lines in the roads layer)
      { id: 'airport-runway', type: 'line', source: 'basemap', 'source-layer': 'roads',
        filter: ['==', ['get', 'kind'], 'aeroway'],
        layout: { 'line-cap': 'butt', 'line-join': 'round' },
        paint: { 'line-color': '#5a6472', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1, 15, 5] } },

      // Road name labels (Protomaps carries names on the roads layer itself)
      { id: 'road-labels', type: 'symbol', source: 'basemap', 'source-layer': 'roads',
        minzoom: 13,
        layout: { 'symbol-placement': 'line', 'symbol-spacing': 250,
                  'text-field': ['get', 'name'], 'text-font': ['Open Sans Regular'],
                  'text-size': ['interpolate', ['linear'], ['zoom'], 13, 10, 17, 13] },
        paint: { 'text-color': '#dfe4f2', 'text-halo-color': '#080c12', 'text-halo-width': 1.4 } },

      // Airport labels (aerodrome points in the pois layer)
      { id: 'airport-label', type: 'symbol', source: 'basemap', 'source-layer': 'pois',
        filter: ['==', ['get', 'kind'], 'aerodrome'], minzoom: 10,
        layout: { 'text-field': ['get', 'name'], 'text-font': ['Open Sans Regular'],
                  'text-size': 11, 'text-anchor': 'top', 'text-offset': [0, 0.4] },
        paint: { 'text-color': '#9fb2c9', 'text-halo-color': '#080c12', 'text-halo-width': 1.2 } },

      // Place labels — cities/towns, then regions
      { id: 'place-locality', type: 'symbol', source: 'basemap', 'source-layer': 'places',
        filter: ['==', ['get', 'kind'], 'locality'],
        layout: { 'text-field': ['get', 'name'], 'text-font': ['Open Sans Regular'],
                  'text-size': ['interpolate', ['linear'], ['zoom'], 4, 10, 12, 16],
                  'text-max-width': 8 },
        paint: { 'text-color': '#eef0f6', 'text-halo-color': '#080c12', 'text-halo-width': 1.5 } },

      { id: 'place-region', type: 'symbol', source: 'basemap', 'source-layer': 'places',
        filter: ['==', ['get', 'kind'], 'region'], maxzoom: 8,
        layout: { 'text-field': ['get', 'name'], 'text-font': ['Open Sans Regular'],
                  'text-size': 12, 'text-transform': 'uppercase', 'text-letter-spacing': 0.1 },
        paint: { 'text-color': '#8a93a6', 'text-halo-color': '#080c12', 'text-halo-width': 1.2 } },

      // Amenity POI labels (from the pois layer) — a curated set so the map
      // isn't buried in labels; named features only, at street zoom.
      { id: 'pois-label', type: 'symbol', source: 'basemap', 'source-layer': 'pois',
        filter: ['all', ['has', 'name'],
          ['match', ['get', 'kind'],
            ['hotel', 'marina', 'attraction', 'camp_site', 'caravan_site', 'sports_centre',
             'golf_course', 'hospital', 'reservoir'], true, false]],
        minzoom: 13,
        layout: { 'text-field': ['get', 'name'], 'text-font': ['Open Sans Regular'],
                  'text-size': 10, 'text-anchor': 'top', 'text-offset': [0, 0.4],
                  'text-max-width': 8 },
        paint: { 'text-color': '#9aa6b6', 'text-halo-color': '#080c12', 'text-halo-width': 1 } }
    ]
  };
}
