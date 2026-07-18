const fs = require('fs'), path = require('path');
const dir = path.join(__dirname, '..', '..', 'services', 'satellites', 'static');
const lib = fs.readFileSync(path.join(dir, 'satellite.min.js'), 'utf8');
if (!/twoline2satrec/.test(lib)) { console.error('satellite.js missing API'); process.exit(1); }
const coast = JSON.parse(fs.readFileSync(path.join(dir, 'coastline.json'), 'utf8'));
if (coast.type !== 'FeatureCollection') { console.error('coastline not a FeatureCollection'); process.exit(1); }
console.log('vendor OK: satellite.js +', coast.features.length, 'coastline features');
