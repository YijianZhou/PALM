"""Preparation-only CSV/CRE readers; no changes to runtime association settings."""
import csv
from pathlib import Path
import numpy as np


def read_csv(path):
    """Depth, Vp, Vs in km and km/s; optional header and fourth Vp/Vs column."""
    rows = []
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        records = csv.reader(line for line in stream if line.strip() and not line.lstrip().startswith('#'))
        for number, fields in enumerate(records, 1):
            fields = [field.strip() for field in fields]
            if number == 1 and fields[0].lower() in ('top_depth_km', 'depth_km'):
                allowed = [('top_depth_km', 'vp_kmps', 'vs_kmps'),
                           ('depth_km', 'vp_kmps', 'vs_kmps'),
                           ('top_depth_km', 'vp', 'vs'), ('depth_km', 'vp', 'vs')]
                if tuple(f.lower() for f in fields[:3]) not in allowed or (
                        len(fields) != 3 and not (len(fields) == 4 and fields[3].lower() == 'vp_vs')):
                    raise ValueError('CSV header must be top_depth_km,vp_kmps,vs_kmps[,vp_vs]')
                continue
            if len(fields) not in (3, 4):
                raise ValueError(f'CSV row {number}: expected depth,Vp,Vs[,Vp/Vs]')
            try:
                values = np.asarray(fields, dtype=float)
            except ValueError as exc:
                raise ValueError(f'CSV row {number}: nonnumeric velocity/depth') from exc
            if not np.all(np.isfinite(values)) or not values[1] > values[2] > 0:
                raise ValueError(f'CSV row {number}: requires finite depth and Vp > Vs > 0')
            if len(values) == 4 and not np.isclose(values[3], values[1]/values[2], rtol=0, atol=0.001):
                raise ValueError(f'CSV row {number}: Vp/Vs column disagrees with Vp and Vs')
            rows.append(values[:3])
    if not rows:
        raise ValueError('CSV model contains no layers')
    return np.asarray(rows).T


def read_cre(path):
    """One CRE phase: optional title then velocity and layer-top depth (F5.2)."""
    rows = []
    title_seen = False
    with Path(path).open(encoding='utf-8-sig') as stream:
        for number, line in enumerate(stream, 1):
            line = line.rstrip()
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            fields = line.split()
            try:
                if len(fields) == 2:
                    velocity, depth = map(float, fields)
                elif len(line) == 10:
                    velocity, depth = float(line[:5]), float(line[5:10])
                else:
                    raise ValueError('Expected two columns or two fixed-width F5.2 fields')
            except ValueError as exc:
                if not rows and not title_seen and line.lstrip()[0].isalpha():
                    title_seen = True
                    continue
                raise ValueError(f'CRE row {number}: expected velocity_kmps depth_km') from exc
            if not np.isfinite(velocity) or velocity <= 0 or not np.isfinite(depth):
                raise ValueError(f'CRE row {number}: requires finite depth and positive velocity')
            rows.append((depth, velocity))
    if not rows:
        raise ValueError('CRE model contains no layers')
    return np.asarray(rows).T


def validate_tops(tops):
    if tops[0] != 0 or np.any(np.diff(tops) <= 0):
        raise ValueError('Layer-top depths must start at zero and be strictly increasing')


def layer_arrays(fmt, path, s_path, bottom, bottom_policy):
    if fmt == 'csv':
        if s_path is not None:
            raise ValueError('CSV contains Vp and Vs; model_s_file must be None')
        tops, vp, vs = read_csv(path)
        validate_tops(tops)
    else:
        if s_path is None:
            raise ValueError('CRE requires model_file for P and model_s_file for S')
        ptops, pvel = read_cre(path)
        stops, svel = read_cre(s_path)
        validate_tops(ptops)
        validate_tops(stops)
        # Preserve each phase profile even when their interfaces differ.
        tops = np.union1d(ptops, stops)
        vp = pvel[np.searchsorted(ptops, tops, side='right')-1]
        vs = svel[np.searchsorted(stops, tops, side='right')-1]
    if bottom is None:
        if bottom_policy != 'halfspace':
            raise ValueError('CSV/CRE finite bottom_policy requires model_bottom_depth_km')
        bottom = np.inf
    elif (isinstance(bottom, bool) or not isinstance(bottom, (int, float)) or
          not np.isfinite(bottom) or bottom <= tops[-1]):
        raise ValueError('model_bottom_depth_km must be finite and exceed the deepest layer top')
    return tops, np.r_[tops[1:], bottom], vp, vs
