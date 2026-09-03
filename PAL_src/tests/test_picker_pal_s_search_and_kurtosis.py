import sys
from pathlib import Path

import numpy as np


PAL_SRC = Path(__file__).resolve().parents[1]
if str(PAL_SRC) not in sys.path:
    sys.path.insert(0, str(PAL_SRC))

from picker_pal import STA_LTA_Kurtosis


def naive_fisher_kurtosis(data, window):
    values = []
    for start in range(len(data) - window + 1):
        item = np.asarray(data[start:start + window], dtype=np.float64)
        centered = item - np.mean(item)
        moment2 = np.mean(centered**2)
        values.append(np.mean(centered**4) / moment2**2 - 3.0)
    return np.asarray(values)


def test_rolling_kurtosis_matches_window_definition():
    rng = np.random.default_rng(20260901)
    data = rng.normal(size=2000) + 1234.5
    window = 101
    picker = STA_LTA_Kurtosis()

    actual = picker.calc_kurtosis(data, window)
    expected = naive_fisher_kurtosis(data, window)

    np.testing.assert_allclose(actual, expected, rtol=1e-9, atol=1e-9)


def test_s_sta_search_uses_half_distance_for_near_source_peak():
    start = STA_LTA_Kurtosis.get_s_sta_search_start_npts(300, 200)

    assert start == 150


def test_s_sta_search_uses_pca_end_for_remote_station_peak():
    start = STA_LTA_Kurtosis.get_s_sta_search_start_npts(2000, 200)

    assert start == 200
