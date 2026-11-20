"""Download swisstopo tiles (swissALTI3D, swissSURFACE3D, SWISSIMAGE 10 cm) covering Oeschinensee.

Stdlib + curl. Resumable: files already present with a matching sha256 are skipped.
Data: © swisstopo (open licence, attribution required).

    python3 scripts/download_swisstopo.py                    # the three base products (newest year)
    python3 scripts/download_swisstopo.py alti2m_2019 alti2m_2025   # 2 m DEMs for the terrain change map
    python3 scripts/download_swisstopo.py --dry-run          # list what would be fetched
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

STAC = "https://data.geo.admin.ch/api/stac/v0.9/collections/{}/items?bbox={}&limit=20"

# AOI as 1 km LV95 tiles (EPSG:2056), tile id = SW corner in km. Covers the lake
# (E 2621116–2622936, N 1149229–1150212, swisstopo gazetteer) plus >=1 km margin
# and "Bim Spitze Stei" (2620229 / 1151489).
TILES_E = range(2619, 2624)  # 2619..2623 -> E 2619000–2624000
TILES_N = range(1148, 1152)  # 1148..1151 -> N 1148000–1152000
QUERY_BBOX_WGS84 = "7.66,46.47,7.78,46.54"  # loose; filtered by tile id below

# output dir -> (collection, regex picking one asset per item, year or None = newest)
PRODUCTS = {
    "swissalti3d": ("ch.swisstopo.swissalti3d", r"_0\.5_2056_5728\.tif$", None),
    "swisssurface3d": ("ch.swisstopo.swisssurface3d", r"\.las\.zip$", None),
    "swissimage-dop10": ("ch.swisstopo.swissimage-dop10", r"_0\.1_2056\.tif$", None),
    "alti2m_2019": ("ch.swisstopo.swissalti3d", r"_2_2056_5728\.tif$", 2019),
    "alti2m_2025": ("ch.swisstopo.swissalti3d", r"_2_2056_5728\.tif$", 2025),
}
DEFAULT = ["swissalti3d", "swisssurface3d", "swissimage-dop10"]

ROOT = Path(__file__).resolve().parent.parent / "data" / "raw"


def tile_of(item_id):
    """'swissalti3d_2019_2621-1149' -> (2019, 2621, 1149)."""
    year, e, n = re.search(r"_(\d{4})_(\d{4})-(\d{4})$", item_id).groups()
    return int(year), int(e), int(n)


def latest_per_tile(items, only_year=None):
    """Keep one item per tile inside the AOI: the newest, or the one from `only_year`."""
    best = {}
    for it in items:
        year, e, n = tile_of(it["id"])
        if e not in TILES_E or n not in TILES_N or (only_year and year != only_year):
            continue
        if (e, n) not in best or year > tile_of(best[(e, n)]["id"])[0]:
            best[(e, n)] = it
    return [best[k] for k in sorted(best)]


def sha256_of_multihash(mh):
    assert mh.upper().startswith("1220"), f"not a sha2-256 multihash: {mh}"
    return mh[4:].lower()


def curl(url, *args):
    # urllib silently truncated large responses here; curl is byte-exact and resumes (-C -)
    return subprocess.run(["curl", "-fsSL", "--retry", "5", "--retry-all-errors", *args, url],
                          check=True, capture_output=True).stdout


def fetch_items(collection):
    url, items = STAC.format(collection, QUERY_BBOX_WGS84), []
    while url:
        page = json.loads(curl(url))
        items += page["features"]
        url = next((l["href"] for l in page.get("links", []) if l["rel"] == "next"), None)
    return items


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(href, dest, sha):
    if dest.exists() and file_sha256(dest) == sha:
        return "skip"
    tmp = dest.with_suffix(dest.suffix + ".part")
    curl(href, "-C", "-", "-o", str(tmp))
    if file_sha256(tmp) != sha:
        tmp.unlink()
        raise IOError(f"checksum mismatch: {href}")
    tmp.replace(dest)
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("products", nargs="*", default=DEFAULT, help=", ".join(PRODUCTS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if bad := set(args.products) - set(PRODUCTS):
        ap.error(f"unknown product(s): {', '.join(sorted(bad))}")
    expected = len(TILES_E) * len(TILES_N)
    cache = {}
    for product in args.products:
        collection, pattern, year = PRODUCTS[product]
        if collection not in cache:
            cache[collection] = fetch_items(collection)
        items = latest_per_tile(cache[collection], year)
        if len(items) != expected:
            sys.exit(f"{product}: found {len(items)} tiles, expected {expected}")
        outdir = ROOT / product
        outdir.mkdir(parents=True, exist_ok=True)
        for it in items:
            name, asset = next((k, a) for k, a in it["assets"].items() if re.search(pattern, k))
            sha = sha256_of_multihash(asset["checksum:multihash"])
            status = "dry" if args.dry_run else download(asset["href"], outdir / name, sha)
            print(f"{status:4} {product}/{name}", flush=True)


if __name__ == "__main__":
    main()
