import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("osgeo.gdal")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from terrain_change import colourize, nmad  # noqa: E402


def test_nmad_of_constant_plus_outlier():
    median, spread = nmad(np.array([1.0, 1.0, 1.0, 1.0, 50.0]))
    assert median == 1.0 and spread == 0.0


def test_colourize_hides_small_and_nan_and_colours_sign():
    rgba = colourize(np.array([[np.nan, 0.2, -5.0, 5.0]]), lod=0.5)
    assert list(rgba[0, :, 3][:2]) == [0, 0]          # NaN and below LOD transparent
    assert rgba[0, 2, 0] == 255 and rgba[0, 3, 2] == 255  # loss red, gain blue
    assert rgba[0, 2, 3] > 0 and rgba[0, 3, 3] > 0
