const path = require('path');
const g = require(path.join(__dirname, '..', '..', 'services', 'satellites', 'static', 'sat-geometry.js'));
let pass = true; const ok = (c, m) => { if (!c) { pass = false; console.error('  x ' + m); } else console.log('  ok ' + m); };

// Equirectangular: (0,0) → center; (90,-180) → top-left.
ok(JSON.stringify(g.project(0, 0, 360, 180)) === JSON.stringify({x:180,y:90}), 'project center');
ok(JSON.stringify(g.project(90, -180, 360, 180)) === JSON.stringify({x:0,y:0}), 'project top-left');

// Antimeridian: a track crossing +180/-180 splits into 2.
const segs = g.splitAntimeridian([{lat:0,lon:170},{lat:0,lon:179},{lat:0,lon:-179},{lat:0,lon:-170}]);
ok(segs.length === 2, 'antimeridian split into 2 segments');

// Split at now: past includes boundary, future starts at boundary (shared point).
const pts = [{a:1},{a:2},{a:3},{a:4}];
const sp = g.splitAtNow(pts, 2);
ok(sp.past.length === 3 && sp.future.length === 2 && sp.past[2] === sp.future[0], 'split shares boundary point');

// Polar: el=90 → center; el=0 due north → straight up from center.
ok(JSON.stringify(g.polar(0, 90, 100, 100, 90)) === JSON.stringify({x:100,y:100}), 'polar zenith = center');
const north = g.polar(0, 0, 100, 100, 90);
ok(Math.abs(north.x - 100) < 1e-9 && north.y < 100, 'polar horizon-north points up');

// Footprint radius: 0° at the surface, grows with altitude (~27° at 800 km).
ok(Math.abs(g.footprintRadiusDeg(0)) < 1e-9, 'footprint radius 0 at surface');
ok(Math.abs(g.footprintRadiusDeg(800) - 27.3) < 1.5, 'footprint radius ~27° at 800 km');

// Footprint ring: n+1 points, longitudes normalised to -180..180.
const fp = g.footprint(47.5, -122, 20, 36);
ok(fp.length === 37, 'footprint returns n+1 points');
ok(fp.every(p => p.lon >= -180 && p.lon <= 180 && p.lat >= -90 && p.lat <= 90), 'footprint coords in range');

// footprintPaths: a mid-latitude circle → one closed polygon.
const p1 = g.footprintPaths(47.5, -122, 20, 720, 360);
ok(p1.length === 1 && /Z$/.test(p1[0]), 'footprint path: single closed polygon when clear');
// A circle over a pole (80°+20°>90°) → a cap closed along the top edge (y=0).
const p2 = g.footprintPaths(80, 0, 20, 720, 360);
ok(p2.length === 1 && /^M0 0 /.test(p2[0]) && / L720 0 Z$/.test(p2[0]), 'footprint path: north-pole cap closed along top edge');

console.log(pass ? '\nALL PASS' : '\nFAIL'); process.exit(pass ? 0 : 1);
