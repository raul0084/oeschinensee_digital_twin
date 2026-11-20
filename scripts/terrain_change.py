"""Terrain change map: swissALTI3D 2025 minus 2019 (2 m), for the AOI.

Needs GDAL's Python bindings + numpy (conda env `holocene`):

    python3 scripts/download_swisstopo.py alti2m_2019 alti2m_2025
    conda run -n holocene python scripts/terrain_change.py

Outputs
- data/derived/dod_2019_2025.tif   change in metres, LV95, NaN = no data (for QGIS / analysis)
- viewer/data/dod.png              coloured overlay (WGS84), changes below the detection level transparent
- viewer/data/dod.bin.gz           int16 centimetres on the same WGS84 grid, for click readout
- viewer/data/dod.json             grid bounds/size, detection level and summary stats

Uncertainty: the two versions may come from different survey methods (older alpine swissALTI3D is partly
photogrammetric, newer is LiDAR). The noise level (NMAD) is measured on gentle, stable-looking ground
and changes smaller than LOD = max(0.5 m, 3 * NMAD) are not shown.
"""
import gzip
import json
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()
ROOT = Path(__file__).resolve().parent.parent
RAW, DERIVED, WEB = ROOT / "data" / "raw", ROOT / "data" / "derived", ROOT / "viewer" / "data"
OLD, NEW = 2019, 2025
CLIP = 10.0  # m; colours saturate at ±CLIP
SPITZE_STEI = (2620229, 1151489)  # swissNAMES3D "Bim Spitze Stei", LV95


def mosaic(year):
    files = sorted(str(p) for p in (RAW / f"alti2m_{year}").glob("*.tif"))
    assert len(files) == 20, f"alti2m_{year}: {len(files)} tiles, run the download first"
    return gdal.BuildVRT(f"/vsimem/alti_{year}.vrt", files)


def read(ds):
    band = ds.GetRasterBand(1)
    a = band.ReadAsArray().astype(np.float32)
    nd = band.GetNoDataValue()
    if nd is not None:
        a[a == nd] = np.nan
    return a


def lake_mask(like):
    """Rasterize the swissTLM3D lake outline (viewer/data/lake.geojson, WGS84) onto the LV95 grid."""
    path = "/vsimem/lake_lv95.geojson"
    gdal.VectorTranslate(path, str(WEB / "lake.geojson"), dstSRS="EPSG:2056", format="GeoJSON")
    # close-then-reopen by path: the dataset returned above isn't flushed yet, so rasterizing it burned nothing
    mem = gdal.GetDriverByName("MEM").Create("", like.RasterXSize, like.RasterYSize, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(like.GetGeoTransform())
    mem.SetProjection(like.GetProjection())
    gdal.Rasterize(mem, path, burnValues=[1], allTouched=True)
    mask = mem.GetRasterBand(1).ReadAsArray().astype(bool)
    area_km2 = mask.sum() * abs(like.GetGeoTransform()[1] * like.GetGeoTransform()[5]) / 1e6
    assert 0.8 < area_km2 < 1.5, f"lake mask covers {area_km2:.2f} km², expected ~1.1 (Oeschinensee)"
    return mask


def nmad(x):
    m = np.median(x)
    return float(m), float(1.4826 * np.median(np.abs(x - m)))


def colourize(d, lod):
    """RGBA: red = surface lowered, violet-blue = raised; alpha 0 below the detection level."""
    show = np.isfinite(d) & (np.abs(d) >= lod)
    d = np.nan_to_num(d)  # NaN pixels end up transparent; avoid NaN -> uint8 casts
    t = np.clip(np.abs(d) / CLIP, 0, 1)
    rgba = np.zeros(d.shape + (4,), np.uint8)
    loss, gain = d < 0, d > 0
    # red for lowered, violet-blue for raised: distinct from the cyan hologram and the orange selection.
    # The legend in viewer/index.html uses the same colours.
    rgba[..., 0] = np.where(loss, 255, 60 + 60 * (1 - t))
    rgba[..., 1] = np.where(loss, 30 + 60 * (1 - t), 40 + 60 * (1 - t))
    rgba[..., 2] = np.where(gain, 255, 30 + 60 * (1 - t))
    rgba[..., 3] = np.where(show, 140 + 110 * t, 0)
    return rgba


def write_png(rgba, path):
    h, w, _ = rgba.shape
    mem = gdal.GetDriverByName("MEM").Create("", w, h, 4, gdal.GDT_Byte)
    for i in range(4):
        mem.GetRasterBand(i + 1).WriteArray(rgba[..., i])
    gdal.GetDriverByName("PNG").CreateCopy(str(path), mem)


def main():
    DERIVED.mkdir(parents=True, exist_ok=True)
    old_ds, new_ds = mosaic(OLD), mosaic(NEW)
    assert old_ds.GetGeoTransform() == new_ds.GetGeoTransform(), "the two years are not on the same grid"
    gt = new_ds.GetGeoTransform()
    d = read(new_ds) - read(old_ds)

    lake = lake_mask(new_ds)
    d[lake] = np.nan  # lake surface is interpolated at whatever level the water had: not terrain change

    slope_ds = gdal.DEMProcessing("/vsimem/slope.tif", new_ds, "slope")
    slope = slope_ds.GetRasterBand(1).ReadAsArray()
    stable = np.isfinite(d) & (slope < 20)
    median, spread = nmad(d[stable])
    lod = max(0.5, 3 * spread)

    # Volumes above the detection level (pixel = 2 m x 2 m)
    px = abs(gt[1] * gt[5])
    sig = np.isfinite(d) & (np.abs(d) >= lod)
    loss, gain = float(-d[sig & (d < 0)].sum() * px), float(d[sig & (d > 0)].sum() * px)

    # Same, within 500 m of Spitze Stei
    ys, xs = np.mgrid[0:d.shape[0], 0:d.shape[1]]
    e, n = gt[0] + (xs + 0.5) * gt[1], gt[3] + (ys + 0.5) * gt[5]
    near = (e - SPITZE_STEI[0]) ** 2 + (n - SPITZE_STEI[1]) ** 2 < 500 ** 2
    ss_loss = float(-d[sig & near & (d < 0)].sum() * px)
    ss_gain = float(d[sig & near & (d > 0)].sum() * px)

    # Analysis raster (LV95)
    out = gdal.GetDriverByName("GTiff").Create(str(DERIVED / "dod_2019_2025.tif"), d.shape[1], d.shape[0], 1,
                                               gdal.GDT_Float32, options=["COMPRESS=DEFLATE", "PREDICTOR=3"])
    out.SetGeoTransform(gt)
    out.SetProjection(new_ds.GetProjection())
    out.GetRasterBand(1).WriteArray(d)
    out.GetRasterBand(1).SetNoDataValue(float("nan"))
    out = None

    # Web products on a WGS84 grid (so the browser can drape and index them by lon/lat)
    w = gdal.Warp("/vsimem/dod_4326.tif", str(DERIVED / "dod_2019_2025.tif"), dstSRS="EPSG:4326",
                  resampleAlg="bilinear", dstNodata=float("nan"))
    dw = w.GetRasterBand(1).ReadAsArray()
    wgt = w.GetGeoTransform()
    west, north = wgt[0], wgt[3]
    east, south = west + wgt[1] * w.RasterXSize, north + wgt[5] * w.RasterYSize

    write_png(colourize(dw, lod), WEB / "dod.png")
    cm = np.where(np.isfinite(dw), np.clip(np.round(dw * 100), -32767, 32767), -32768).astype("<i2")
    (WEB / "dod.bin.gz").write_bytes(gzip.compress(cm.tobytes(), 9))

    meta = {
        "years": [OLD, NEW], "width": w.RasterXSize, "height": w.RasterYSize,
        "west": west, "south": south, "east": east, "north": north,
        "scale": 0.01, "nodata": -32768, "clip_m": CLIP,
        "lod_m": round(lod, 2), "stable_median_m": round(median, 3), "stable_nmad_m": round(spread, 3),
        "loss_m3": round(loss), "gain_m3": round(gain),
        "spitze_stei_500m": {"loss_m3": round(ss_loss), "gain_m3": round(ss_gain)},
    }
    (WEB / "dod.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(meta, indent=2))
    if abs(median) > 0.2:
        print(f"WARNING: stable ground differs by {median:+.2f} m on average: the versions may be offset "
              "(co-registration needed before trusting small changes)")


if __name__ == "__main__":
    main()
