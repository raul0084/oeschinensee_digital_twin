// Terrain change statistics inside a drawn polygon. Pure JS (no Cesium) so it can be tested headless:
//   osascript -l JavaScript tests/test_zonestats.js
//
// grid: Int16Array of centimetres on the WGS84 grid described by meta (viewer/data/dod.json)
// ring: [[lon, lat], ...] polygon vertices in degrees (open or closed)

function insideRing(x, y, ring) {  // even-odd ray casting
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function zoneStats(grid, meta, ring) {
  const dLon = (meta.east - meta.west) / meta.width, dLat = (meta.north - meta.south) / meta.height;
  const lons = ring.map((p) => p[0]), lats = ring.map((p) => p[1]);
  const c0 = Math.max(0, Math.floor((Math.min(...lons) - meta.west) / dLon));
  const c1 = Math.min(meta.width - 1, Math.floor((Math.max(...lons) - meta.west) / dLon));
  const r0 = Math.max(0, Math.floor((meta.north - Math.max(...lats)) / dLat));
  const r1 = Math.min(meta.height - 1, Math.floor((meta.north - Math.min(...lats)) / dLat));
  // pixel area in m² at the zone's latitude (spherical approximation, < 0.5 % error here)
  const lat0 = ((Math.min(...lats) + Math.max(...lats)) / 2) * Math.PI / 180;
  const pxArea = dLon * 111320 * Math.cos(lat0) * dLat * 110574;

  const s = { pixels: 0, noData: 0, sum: 0, lossPx: 0, gainPx: 0, loss: 0, gain: 0, minDh: Infinity, maxDh: -Infinity };
  for (let r = r0; r <= r1; r++) {
    const lat = meta.north - (r + 0.5) * dLat;
    for (let c = c0; c <= c1; c++) {
      if (!insideRing(meta.west + (c + 0.5) * dLon, lat, ring)) continue;
      const v = grid[r * meta.width + c];
      if (v === meta.nodata) { s.noData++; continue; }
      const d = v * meta.scale;
      s.pixels++;
      s.sum += d;
      if (d < s.minDh) s.minDh = d;
      if (d > s.maxDh) s.maxDh = d;
      if (d <= -meta.lod_m) { s.lossPx++; s.loss -= d; }
      else if (d >= meta.lod_m) { s.gainPx++; s.gain += d; }
    }
  }
  const sigPx = s.lossPx + s.gainPx;
  return {
    area_m2: s.pixels * pxArea,
    noData_m2: s.noData * pxArea,
    mean_dh_m: s.pixels ? s.sum / s.pixels : NaN,
    min_dh_m: s.pixels ? s.minDh : NaN,
    max_dh_m: s.pixels ? s.maxDh : NaN,
    changed_pct: s.pixels ? (100 * sigPx) / s.pixels : NaN,
    loss_m3: s.loss * pxArea,
    gain_m3: s.gain * pxArea,
    net_m3: (s.gain - s.loss) * pxArea,
    // Conservative: treats the noise as fully correlated over the changed area
    uncertainty_m3: meta.stable_nmad_m * sigPx * pxArea,
    // What the stable-ground offset alone contributes over the whole zone
    bias_m3: meta.stable_median_m * s.pixels * pxArea,
  };
}

// Elevation profile statistics. dist: metres along the line, elev: metres a.s.l. (same length)
function profileStats(dist, elev) {
  let ascent = 0, descent = 0, maxSlope = 0;
  for (let i = 1; i < dist.length; i++) {
    const dz = elev[i] - elev[i - 1], dx = dist[i] - dist[i - 1];
    if (dz > 0) ascent += dz; else descent -= dz;
    if (dx > 0) maxSlope = Math.max(maxSlope, Math.atan(Math.abs(dz) / dx) * 180 / Math.PI);
  }
  return { length_m: dist[dist.length - 1], min_m: Math.min(...elev), max_m: Math.max(...elev),
           ascent_m: ascent, descent_m: descent, max_slope_deg: maxSlope };
}

// SVG cross-section: `now` solid cyan, `before` dashed (NaN gaps allowed in before)
function profileSvg(dist, now, before, w = 300, h = 130) {
  const pad = { l: 38, r: 6, t: 8, b: 18 };
  const all = now.concat(before.filter(Number.isFinite));
  const lo = Math.min(...all), hi = Math.max(...all), span = Math.max(hi - lo, 1), L = dist[dist.length - 1] || 1;
  const x = (d) => pad.l + (d / L) * (w - pad.l - pad.r);
  const y = (z) => pad.t + (1 - (z - lo) / span) * (h - pad.t - pad.b);
  const path = (zs) => zs.map((z, i) => (Number.isFinite(z) ? `${i && Number.isFinite(zs[i - 1]) ? "L" : "M"}${x(dist[i]).toFixed(1)},${y(z).toFixed(1)}` : "")).join("");
  const t = (px, py, s, a = "start") => `<text x="${px}" y="${py}" text-anchor="${a}">${s}</text>`;
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" class="profile">` +
    `<path d="M${pad.l},${pad.t}V${h - pad.b}H${w - pad.r}" class="axis"/>` +
    `<path d="${path(before)}" class="before"/><path d="${path(now)}" class="now"/>` +
    t(pad.l - 4, pad.t + 8, Math.round(hi), "end") + t(pad.l - 4, h - pad.b, Math.round(lo), "end") +
    t(pad.l, h - 4, "0") + t(w - pad.r, h - 4, `${(L / 1000).toFixed(2)} km`, "end") + `</svg>`;
}

// Runs of a polyline that lie at or above `level`. heights: one per vertex. Returns runs as arrays of
// fractional vertex indices (i + t), cut exactly where the line crosses the level.
function levelRuns(heights, level) {
  const runs = [];
  let run = [];
  for (let i = 0; i < heights.length - 1; i++) {
    const a = heights[i], b = heights[i + 1];
    if (a >= level) run.push(i);
    if ((a >= level) !== (b >= level)) {
      run.push(i + (level - a) / (b - a));
      if (a >= level) { if (run.length > 1) runs.push(run); run = []; }
    }
  }
  if (heights[heights.length - 1] >= level) run.push(heights.length - 1);
  if (run.length > 1) runs.push(run);
  return runs;
}
