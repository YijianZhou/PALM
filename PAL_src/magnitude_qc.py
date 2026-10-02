"""PAL magnitude aggregation and missing-value convention."""
from numbers import Integral
from types import SimpleNamespace
import numpy as np


def magnitude_parameters(cfg):
    count = getattr(cfg, 'mag_min_stations', 3)
    spread = float(getattr(cfg, 'mag_max_std', 1.0))
    if isinstance(count, bool) or not isinstance(count, Integral) or count < 1:
        raise ValueError('mag_min_stations must be a positive integer')
    if not np.isfinite(spread) or spread < 0:
        raise ValueError('mag_max_std must be finite and nonnegative')
    return dict(mag_min_stations=int(count), mag_max_std=spread)


def station_magnitude_summary(station_values, mag_min_stations=3, mag_max_std=1.0):
    params = magnitude_parameters(SimpleNamespace(
        mag_min_stations=mag_min_stations, mag_max_std=mag_max_std))
    groups = {}
    for station, value in station_values:
        if np.isfinite(value):
            base = '.'.join(str(station).split('.')[:2])
            groups.setdefault(base, []).append(float(value))
    values = np.asarray([np.median(v) for v in groups.values()], dtype=float)
    std = float(np.std(values, ddof=0)) if len(values) else float('nan')
    valid = len(values) >= params['mag_min_stations'] and std <= params['mag_max_std']
    return dict(mag=round(float(np.median(values)), 2) if valid else -1.0,
                mag_num_stations=len(values), mag_std=std)


def median_event_magnitude(values):
    # -1 is reserved for unavailable magnitudes; other negative values are valid.
    valid = [float(v) for v in values if np.isfinite(float(v)) and float(v) != -1.0]
    return float(np.median(valid)) if valid else -1.0
