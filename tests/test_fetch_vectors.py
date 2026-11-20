import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from fetch_vectors import centroid, line, lv95_to_wgs84


def test_lv95_to_wgs84_bern_reference():
    # swisstopo's worked example: Bern old observatory origin, E 2600000 / N 1200000
    lon, lat = lv95_to_wgs84(2600000, 1200000)
    assert abs(lon - 7.43863) < 1e-4 and abs(lat - 46.95108) < 1e-4


def test_line_densifies_and_keeps_endpoints():
    pts = line([(2619000, 1148000), (2619000, 1149000)], step=250)
    assert len(pts) == 5
    assert pts[0] == lv95_to_wgs84(2619000, 1148000) and pts[-1] == lv95_to_wgs84(2619000, 1149000)


def test_centroid_of_square():
    sq = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2]]]}
    assert centroid(sq) == [1, 1]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
