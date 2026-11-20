"""Build the viewer's vector layers as WGS84 GeoJSON in viewer/data/.

- lake.geojson   Oeschinensee outline (swissTLM3D water network)
- names.geojson  clickable names in the AOI (swissNAMES3D), one point per name
- grid.geojson   250 m LV95 grid lines over the AOI
- aoi.geojson    the visible block, 20 m inside the AOI (role=clip), and the same ring every 25 m for the box walls (role=frame)

Stdlib + curl. Data: © swisstopo.

    python3 scripts/fetch_vectors.py
"""
import json
import subprocess
from pathlib import Path

from download_swisstopo import TILES_E, TILES_N

OUT = Path(__file__).resolve().parent.parent / "viewer" / "data"
IDENTIFY = ("https://api3.geo.admin.ch/rest/services/api/MapServer/identify?geometryType=esriGeometryEnvelope"
            "&geometry={}&sr=2056&layers=all:{}&tolerance=0&returnGeometry=true&geometryFormat=geojson&limit=200&lang=de")
E0, E1 = TILES_E[0] * 1000, (TILES_E[-1] + 1) * 1000
N0, N1 = TILES_N[0] * 1000, (TILES_N[-1] + 1) * 1000
GRID = 250  # m
CLIP_INSET = 20  # m, see main()
# swissNAMES3D kinds worth a clickable dot; areas get a dot at their centroid
KINDS = {"Hauptgipfel", "Gipfel", "Pass", "Wasserfall", "Aussichtspunkt", "Gebaeude", "Offenes Gebaeude",
         "Flurname swisstopo", "Lokalname swisstopo", "See", "Gletscher", "Tal", "Grat", "Ort"}


def lv95_to_wgs84(e, n):
    """swisstopo approximate formula, ~1 m accuracy. Returns (lon, lat)."""
    y, x = (e - 2600000) / 1e6, (n - 1200000) / 1e6
    lon = 2.6779094 + 4.728982 * y + 0.791484 * y * x + 0.1306 * y * x * x - 0.0436 * y ** 3
    lat = 16.9023892 + 3.238272 * x - 0.270978 * y * y - 0.002528 * x * x - 0.0447 * y * y * x - 0.0140 * x ** 3
    return [round(lon * 100 / 36, 7), round(lat * 100 / 36, 7)]


def to_wgs84(coords):
    if isinstance(coords[0], (int, float)):
        return lv95_to_wgs84(coords[0], coords[1])
    return [to_wgs84(c) for c in coords]


def identify(layer):
    """All features of a layer in the AOI. Queried per km tile: the API caps each answer at ~200."""
    seen = {}
    for e in TILES_E:
        for n in TILES_N:
            box = f"{e * 1000},{n * 1000},{e * 1000 + 1000},{n * 1000 + 1000}"
            res = json.loads(subprocess.run(["curl", "-fsSL", "--retry", "5", "--retry-all-errors",
                                             IDENTIFY.format(box, layer)], check=True, capture_output=True).stdout)
            assert len(res["results"]) < 200, f"{layer} {box}: hit the result cap"
            for f in res["results"]:
                seen[(f["id"], f["properties"].get("label"))] = f
    return list(seen.values())


def centroid(geom):
    """Vertex average of the largest ring; good enough to anchor a label."""
    if geom["type"].endswith("Point"):
        pts = [geom["coordinates"]] if geom["type"] == "Point" else geom["coordinates"]
        return pts[0]
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    ring = max((p[0] for p in polys), key=len)
    return [sum(c[0] for c in ring) / len(ring), sum(c[1] for c in ring) / len(ring)]


def inside_aoi(e, n):
    return E0 <= e <= E1 and N0 <= n <= N1


def line(points, step=50):
    """LV95 polyline densified every `step` m, so it drapes well on terrain."""
    out = []
    for (e0, n0), (e1, n1) in zip(points, points[1:]):
        k = max(1, int(max(abs(e1 - e0), abs(n1 - n0)) // step))
        out += [lv95_to_wgs84(e0 + (e1 - e0) * i / k, n0 + (n1 - n0) * i / k) for i in range(k)]
    return out + [lv95_to_wgs84(*points[-1])]


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    lakes = [f for f in identify("ch.swisstopo.swisstlm3d-gewaessernetz") if f["properties"].get("name") == "Oeschinensee"]
    assert len(lakes) == 1, f"expected one Oeschinensee polygon, got {len(lakes)}"
    lake = {"type": "Feature", "properties": {"name": "Oeschinensee"},
            "geometry": {"type": lakes[0]["geometry"]["type"], "coordinates": to_wgs84(lakes[0]["geometry"]["coordinates"])}}

    names, seen = [], set()
    for f in identify("ch.swisstopo.swissnames3d"):
        p = f["properties"]
        if p["objektart"] not in KINDS or not p.get("name") or (p["name"], p["objektart"]) in seen:
            continue
        seen.add((p["name"], p["objektart"]))
        e, n = centroid(f["geometry"])[:2]
        if inside_aoi(e, n):
            names.append({"type": "Feature", "properties": {"name": p["name"], "kind": p["objektart"]},
                          "geometry": {"type": "Point", "coordinates": lv95_to_wgs84(e, n)}})

    grid = [{"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": line([(e, N0), (e, N1)])}}
            for e in range(E0 + GRID, E1, GRID)]
    grid += [{"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": line([(E0, n), (E1, n)])}}
             for n in range(N0 + GRID, N1, GRID)]
    # Clip 20 m inside the block: the terrain asset drops to 0 m just outside it, and the last strip of
    # triangles slopes down to that edge ("waterfalls"). The viewer's box walls stand on this same ring.
    i = CLIP_INSET
    ring = [(E0 + i, N0 + i), (E1 - i, N0 + i), (E1 - i, N1 - i), (E0 + i, N1 - i), (E0 + i, N0 + i)]
    aoi = {"type": "Feature", "properties": {"role": "clip", "lv95": [E0 + i, N0 + i, E1 - i, N1 - i]},
           "geometry": {"type": "Polygon", "coordinates": [line(ring)]}}
    frame = {"type": "Feature", "properties": {"role": "frame"},
             "geometry": {"type": "LineString", "coordinates": line(ring, step=25)}}

    for name, data in {"lake": fc([lake]), "names": fc(names), "grid": fc(grid), "aoi": fc([aoi, frame])}.items():
        (OUT / f"{name}.geojson").write_text(json.dumps(data, ensure_ascii=False))
        print(f"{name}.geojson: {len(data['features'])} features")


if __name__ == "__main__":
    main()
