// Run: osascript -l JavaScript tests/test_zonestats.js  (zone + profile maths in viewer/zonestats.js)   (JavaScriptCore, no node needed)
ObjC.import("Foundation");
const here = $.NSString.stringWithString($.NSProcessInfo.processInfo.arguments.js.map((a) => a.js).pop());
const src = $.NSString.stringWithContentsOfFileEncodingError(
  here.stringByDeletingLastPathComponent.stringByDeletingLastPathComponent.stringByAppendingPathComponent("viewer/zonestats.js").js,
  $.NSUTF8StringEncoding, null).js;
eval(src);

function assert(cond, msg) { if (!cond) throw new Error("FAIL: " + msg); }
const near = (a, b, tol) => Math.abs(a - b) <= tol;

// 10 x 10 grid over a 0.01° x 0.01° box at the equator; all -2 m except one +5 m pixel and one nodata
const meta = { west: 0, east: 0.01, south: 0, north: 0.01, width: 10, height: 10,
               scale: 0.01, nodata: -32768, lod_m: 1.0, stable_nmad_m: 0.5, stable_median_m: -0.1 };
const grid = new Int16Array(100).fill(-200);
grid[55] = 500;
grid[0] = -32768;
const all = [[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01]];
const st = zoneStats(grid, meta, all);
const px = 0.001 * 111320 * 0.001 * 110574;

assert(near(st.area_m2, 99 * px, 1), "area counts valid pixels only");
assert(near(st.noData_m2, px, 1), "nodata area");
assert(near(st.loss_m3, 98 * 2 * px, 1), "loss volume");
assert(near(st.gain_m3, 5 * px, 1), "gain volume");
assert(near(st.net_m3, st.gain_m3 - st.loss_m3, 1e-6), "net = gain - loss");
assert(st.min_dh_m === -2 && st.max_dh_m === 5, "min/max");
assert(near(st.changed_pct, 100, 1e-9), "every valid pixel is above LOD");

// Half-box polygon picks only the western 5 columns
const half = zoneStats(grid, meta, [[0, 0], [0.005, 0], [0.005, 0.01], [0, 0.01]]);
assert(near(half.area_m2, 49 * px, 1), "western half: 50 pixels minus the nodata one");
assert(half.gain_m3 === 0, "the +5 m pixel (column 5) is in the eastern half");

// Below LOD changes do not count as volume
const small = zoneStats(new Int16Array(100).fill(50), meta, all);
assert(small.loss_m3 === 0 && small.gain_m3 === 0 && small.changed_pct === 0, "0.5 m < LOD ignored");

// Profile: up 10 m over 10 m (45°), then down 5 m over 10 m
const pr = profileStats([0, 10, 20], [100, 110, 105]);
assert(pr.ascent_m === 10 && pr.descent_m === 5, "ascent/descent");
assert(near(pr.max_slope_deg, 45, 1e-9) && pr.length_m === 20 && pr.min_m === 100 && pr.max_m === 110, "profile stats");
const svg = profileSvg([0, 10, 20], [100, 110, 105], [100, NaN, 104]);
assert(svg.startsWith("<svg") && svg.includes('class="now"') && svg.includes("0.02 km"), "profile svg");

// Wall contour runs: a hill 0-20-0 cut at 10 -> one run from 0.5 to 1.5
let runs = levelRuns([0, 20, 0], 10);
assert(runs.length === 1 && runs[0][0] === 0.5 && runs[0][1] === 1 && runs[0][2] === 1.5, "hill run " + JSON.stringify(runs));
// two hills -> two runs; all above -> one full run; all below -> none
assert(levelRuns([0, 20, 0, 20, 0], 10).length === 2, "two hills");
assert(JSON.stringify(levelRuns([30, 40, 30], 10)) === "[[0,1,2]]", "all above");
assert(levelRuns([0, 5, 0], 10).length === 0, "all below");

"ok";
