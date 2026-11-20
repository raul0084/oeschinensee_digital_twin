"""Upload the swisstopo tiles in data/raw/ to Cesium ion as terrain, imagery and point cloud assets.

Stdlib + curl. Needs a Cesium ion token with assets:read, assets:write and assets:list scopes,
in env var CESIUM_ION_TOKEN or a line `CESIUM_ION_TOKEN=...` in .env (gitignored).
Created asset ids go to ion_assets.json; products already listed there are skipped.

    python3 scripts/upload_ion.py                 # upload all three products
    python3 scripts/upload_ion.py swissalti3d     # just one
    python3 scripts/upload_ion.py --status        # ion processing status of uploaded assets
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
IDS = ROOT / "ion_assets.json"
API = "https://api.cesium.com/v1/assets"
ATTRIBUTION = "© [swisstopo](https://www.swisstopo.admin.ch/)"

# product dir -> (glob, POST /v1/assets body without name/attribution). Options per ion's openapi.yaml.
PRODUCTS = {
    "swissalti3d": ("*.tif", {
        "type": "TERRAIN",
        # GeoTIFFs carry no vertical datum, so ion would default to the ellipsoid (~50 m off).
        # shortcut: EGM96 stands in for LN02 (within ~2 m here); upgrade by converting to
        # ellipsoidal heights with the swisstopo geoid (CHGeo2004) and using WGS84.
        # baseTerrainId 1 = Cesium World Terrain fills the area outside the AOI (else 0 m cliffs at the edge)
        "options": {"sourceType": "RASTER_TERRAIN", "heightReference": "MEAN_SEA_LEVEL", "baseTerrainId": 1},
    }),
    "swissimage-dop10": ("*.tif", {
        "type": "IMAGERY",
        "options": {"sourceType": "RASTER_IMAGERY"},
    }),
    "swisssurface3d": ("*.las.zip", {
        "type": "3DTILES",
        "options": {"sourceType": "POINT_CLOUD", "inputCrs": "EPSG:2056"},
    }),
}


def token():
    if t := os.environ.get("CESIUM_ION_TOKEN"):
        return t
    env = ROOT / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith("CESIUM_ION_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("Set CESIUM_ION_TOKEN (env var or .env)")


def curl(url, config, *args):
    # secrets go through `-K -` (stdin) so they never show up in the process list
    r = subprocess.run(["curl", "-sS", "--fail-with-body", "--retry", "5", "-K", "-", *args, url],
                       input=config, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"curl failed ({r.returncode}) for {url}: {r.stderr.strip()} {r.stdout[:500]}")
    return r.stdout


def api(url, tok, method="GET", body=None):
    cfg = f'header = "Authorization: Bearer {tok}"\n'
    args = ["-X", method] + (["--retry-all-errors"] if method == "GET" else [])  # never re-POST a create
    if body is not None:
        args += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
    return json.loads(curl(url, cfg, *args) or "null")


def upload(product, tok):
    pattern, body = PRODUCTS[product]
    files = sorted((RAW / product).glob(pattern))
    if not files:
        sys.exit(f"no {pattern} files in {RAW / product}; run scripts/download_swisstopo.py first")
    res = api(API, tok, "POST", {"name": f"Oeschinensee {product}", "attribution": ATTRIBUTION,
                                **body, "description": f"swisstopo {product} tiles, Oeschinensee AOI (LV95 E 2619-2624 km, N 1148-1152 km)"})
    asset_id, loc, done = res["assetMetadata"]["id"], res["uploadLocation"], res["onComplete"]
    print(f"{product}: created asset {asset_id}, uploading {len(files)} files", flush=True)
    s3 = (f'user = "{loc["accessKey"]}:{loc["secretAccessKey"]}"\n'
          f'header = "x-amz-security-token: {loc["sessionToken"]}"\n')
    endpoint = loc["endpoint"].rstrip("/")
    region = re.search(r"s3\.([a-z0-9-]+)\.amazonaws\.com", endpoint).group(1)  # signing region must match
    for f in files:
        # path-style URL: the bucket name contains dots, which breaks virtual-host TLS
        curl(f'{endpoint}/{loc["bucket"]}/{loc["prefix"]}{f.name}', s3,
             "--retry-all-errors", "--aws-sigv4", f"aws:amz:{region}:s3", "-H", "x-amz-content-sha256: UNSIGNED-PAYLOAD",
             "-T", str(f))
        print(f"  up {f.name}", flush=True)
    api(done["url"], tok, done["method"], done["fields"])
    return asset_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("products", nargs="*", default=list(PRODUCTS), help=", ".join(PRODUCTS))
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if bad := set(args.products) - set(PRODUCTS):
        ap.error(f"unknown product(s): {', '.join(sorted(bad))}")
    tok = token()
    ids = json.loads(IDS.read_text()) if IDS.exists() else {}
    if args.status:
        for product, asset_id in ids.items():
            a = api(f"{API}/{asset_id}", tok)
            print(f"{product:18} {asset_id:>8} {a['status']:12} {a.get('percentComplete', 0)}%")
        return
    for product in args.products:
        if product in ids:
            print(f"{product}: already asset {ids[product]}, skipping")
            continue
        # shortcut: a failed upload leaves an AWAITING_FILES asset in ion; delete it there and rerun
        ids[product] = upload(product, tok)
        IDS.write_text(json.dumps(ids, indent=2) + "\n")
    print("done; check processing with --status")


if __name__ == "__main__":
    main()
