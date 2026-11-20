import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from download_swisstopo import latest_per_tile, sha256_of_multihash, tile_of


def test_tile_of():
    assert tile_of("swissimage-dop10_2021_2621-1149") == (2021, 2621, 1149)


def test_latest_per_tile_keeps_newest_and_drops_outside_aoi():
    items = [{"id": i} for i in (
        "x_2018_2621-1149", "x_2021_2621-1149", "x_2019_2621-1149",  # newest wins
        "x_2021_2630-1149",  # outside AOI
    )]
    assert [i["id"] for i in latest_per_tile(items)] == ["x_2021_2621-1149"]


def test_latest_per_tile_fixed_year():
    items = [{"id": i} for i in ("x_2019_2621-1149", "x_2025_2621-1149")]
    assert [i["id"] for i in latest_per_tile(items, 2019)] == ["x_2019_2621-1149"]


def test_multihash():
    assert sha256_of_multihash("1220ABCD") == "abcd"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
