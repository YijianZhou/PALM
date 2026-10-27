from datetime import timedelta
from numbers import Integral
import time
import numpy as np
from magnitude_qc import magnitude_parameters, station_magnitude_summary
from runtime_console import debug


def format_assoc_time(value):
  dt = value.datetime + timedelta(microseconds=5000)
  dt = dt.replace(microsecond=(dt.microsecond // 10000) * 10000)
  return dt.isoformat(timespec='milliseconds')[:-1] + 'Z'

class _FailedNeighborhoods:
  """Exact pick memberships, invalidated only when a member is removed."""
  def __init__(self):
    self.signatures = set()
    self.by_pick = {}

  def __contains__(self, signature):
    return signature in self.signatures

  def add(self, signature):
    if signature in self.signatures:
      return
    self.signatures.add(signature)
    for index in signature:
      self.by_pick.setdefault(index, set()).add(signature)

  def remove(self, indices):
    affected = set()
    for index in indices:
      affected.update(self.by_pick.get(int(index), ()))
    for signature in affected:
      self.signatures.remove(signature)
      for index in signature:
        entries = self.by_pick[index]
        entries.remove(signature)
        if not entries:
          del self.by_pick[index]


class _OriginNeighborhoods:
  """Fixed sorted indices, strict time windows, and active station counts."""
  def __init__(self, picks, radius, unique_stations):
    times = picks['sta_ot']
    self.stations = picks['net_sta']
    self.unique_stations = unique_stations
    size = len(picks)
    self.active = np.ones(size, dtype=bool)
    self.counts = np.zeros(size, dtype=np.int64)
    self.left = np.empty(size, dtype=np.int64)
    self.right = np.empty(size, dtype=np.int64)
    left = right = 0
    for center, time in enumerate(times):
        # Use the original time subtraction, including UTCDateTime precision,
        # rather than changing strict boundaries by converting epoch floats.
        while left < size and time - times[left] >= radius:
            left += 1
        right = max(right, left)
        while right < size and abs(times[right] - time) < radius:
            right += 1
        self.left[center], self.right[center] = left, right
    self._refresh(0, size)

  def _refresh(self, start, end):
    if start >= end:
        return
    frequencies = {}
    pair_count = 0
    left = right = int(self.left[start])
    def change(index, delta):
        nonlocal pair_count
        if not self.active[index]:
            return
        pair_count += delta
        if self.unique_stations:
            station = self.stations[index]
            value = frequencies.get(station, 0) + delta
            if value:
                frequencies[station] = value
            else:
                frequencies.pop(station, None)
    for center in range(start, end):
        while right < self.right[center]:
            change(right, 1)
            right += 1
        while left < self.left[center]:
            change(left, -1)
            left += 1
        self.counts[center] = (
            (len(frequencies) if self.unique_stations else pair_count)
            if self.active[center] else 0
        )

  def members(self, center):
    left, right = int(self.left[center]), int(self.right[center])
    return np.flatnonzero(self.active[left:right]) + left

  def remove(self, indices):
    if len(indices) == 0:
        return
    self.active[indices] = False
    self.counts[indices] = 0
    # A center changes iff its fixed window contains a removed index. All
    # removed picks came from one candidate window; refresh only this span.
    start = int(np.searchsorted(self.right, int(np.min(indices)), side='right'))
    end = int(np.searchsorted(self.left, int(np.max(indices)), side='right'))
    self._refresh(start, end)


class PS_Pair_Assoc(object):
  """ Associate P- & S-pick Pairs by searching ot and loc clustering
  Inputs
    sta_dict: station location dict
    xy_margin: ratio of lateral (x-y) margin relative to the station range
    lat_range: optional explicit [minimum, maximum] latitude search range
    lon_range: optional explicit [minimum, maximum] longitude search range
    xy_grid: grid width for x-y axis (in degree)
    z_grids: grids for z axis (in km)
    ot_grid_step_sec: temporal grid spacing (seconds; default 0.1)
    ot_grid_max_width_sec: maximum first-to-last temporal span (default 1.0)
    ot_grid_max_points: maximum temporal candidates (default 10)
    ot_dev: max time dev for ot assoc
    velo_mod: homogeneous [Vp, Vs] km/s; finite Vp > Vs > 0
    travel_time_table: None/numeric NaN or single-file .paltt; overrides spatial grid and velo_mod
    P/S travel-time arrays use float32 storage; geometry and absolute times retain their precision.
    max_res: strict P/S residual limits in seconds (default [1.2, 1.2]); scalar applies to both
    max_drop: allowed rejections from successful detections before removing a pick
    unique_stations: count distinct station identifiers (default True); keep all candidate pairs
    min_sta: min number of station to alert a detection
    *note: lateral distance (x-y) in degree; depth in km; elevation in m
  Usage
    import associator_pal
    associator = associator_pal.PS_Pair_Assoc(sta_dict)
    associator.associate(picks, out_ctlg, out_pha)
  """
  def __init__(self,
               sta_dict,
               xy_margin = 0.2,
               xy_grid   = 0.02,
               z_grids   = [5],
               velo_mod  = (5.9, 3.5),
               ot_dev    = 1.4,
               max_res   = (1.2, 1.2),
               max_drop  = 1, 
               min_sta   = 4,
               lat_range = None,
               lon_range = None,
               mag_min_stations = 3,
               mag_max_std = 1.0,
               ot_grid_step_sec = 0.1,
               ot_grid_max_width_sec = 1.0,
               ot_grid_max_points = 10,
               unique_stations = True,
               travel_time_table = None):
    if not isinstance(unique_stations, bool):
      raise ValueError("unique_stations must be a boolean")
    if isinstance(max_drop, bool) or not isinstance(max_drop, Integral) or max_drop < 0:
      raise ValueError("max_drop must be a nonnegative integer")
    self.unique_stations = unique_stations
    self._validate_temporal_grid(ot_grid_step_sec, ot_grid_max_width_sec, ot_grid_max_points)
    self.ot_grid_step_sec = ot_grid_step_sec
    self.ot_grid_max_width_sec = ot_grid_max_width_sec
    self.ot_grid_max_points = ot_grid_max_points
    self.mag_min_stations = mag_min_stations
    self.mag_max_std = mag_max_std
    magnitude_parameters(self)
    if isinstance(travel_time_table, (float, np.floating)) and np.isnan(travel_time_table):
      travel_time_table = None
    self.travel_time_table = travel_time_table
    self.sta_dict = sta_dict
    self.xy_margin = xy_margin
    self.lat_range = self._validate_range("lat_range", lat_range)
    self.lon_range = self._validate_range("lon_range", lon_range)
    self.xy_grid = xy_grid
    if travel_time_table is None and (not np.isfinite(self.xy_grid) or self.xy_grid <= 0):
      raise ValueError("xy_grid must be positive")
    self.z_grids = z_grids
    from os import PathLike
    if travel_time_table is None and isinstance(velo_mod, (str, PathLike)):
      raise ValueError("velo_mod must be [Vp, Vs] in km/s; build a .paltt file separately and set travel_time_table")
    self.velo_mod = (self._validate_phase_values("velo_mod", velo_mod)
                     if travel_time_table is None else None)
    if self.velo_mod is not None and self.velo_mod[0] <= self.velo_mod[1]:
      raise ValueError("velo_mod requires Vp > Vs > 0 (km/s)")
    self.ot_dev = ot_dev
    self.max_res = self._validate_phase_values("max_res", max_res)
    self.max_drop = max_drop
    self.min_sta = min_sta
    self.tt_dict = self.calc_tt()

  @staticmethod
  def _validate_phase_values(name, values):
    if name == "max_res" and np.isscalar(values):
      values = [values, values]
    try:
      items = list(values)
      valid = len(items) == 2 and all(
          not isinstance(value, (bool, np.bool_, str)) for value in items)
      array = np.asarray(items, dtype=float)
      valid = valid and array.shape == (2,) and np.all(np.isfinite(array)) and np.all(array > 0)
    except (TypeError, ValueError):
      valid = False
    if not valid:
      raise ValueError("{} must contain two finite positive numbers [P, S]".format(name))
    return tuple(array)

  @staticmethod
  def _validate_range(name, value):
    if value is None:
      return None
    try:
      valid_length = len(value) == 2
    except TypeError:
      valid_length = False
    if not valid_length:
      raise ValueError("{} must contain [minimum, maximum]".format(name))
    bounds = tuple(float(item) for item in value)
    if not np.all(np.isfinite(bounds)) or bounds[0] >= bounds[1]:
      raise ValueError("{} must be finite and increasing".format(name))
    return bounds

  def associate(
      self, picks, out_ctlg=None, out_pha=None, verbose=True,
      unique_stations=None,
  ):
    if unique_stations is None:
        unique_stations = getattr(self, 'unique_stations', True)
    events_loc, events_pick = [], []
    num_picks = len(picks)
    if num_picks==0: return 
    picks = np.sort(picks, order='sta_ot')
    neighborhoods = _OriginNeighborhoods(picks, self.ot_dev, unique_stations)
    num_nbr = neighborhoods.counts
    num_drop = np.zeros(num_picks, dtype=np.int64)
    center_order = None
    # assoc each cluster
    if verbose: print('-'*40+'\n'+'detected events:')
    failed_neighborhoods = _FailedNeighborhoods()
    started = time.monotonic()
    attempts = failures = 0
    cache_hits = 0
    location_seconds = 0.0
    # Up to N different neighborhoods per unchanged pick set. Successful
    # attempts remove picks; include the configured rejection allowance in
    # the defensive bound rather than capping attempts at the input count.
    max_iterations = num_picks * num_picks * (self.max_drop + 1)
    if verbose:
        print('[assoc] picks={} max_attempts={}'.format(num_picks, max_iterations), flush=True)
    for _ in range(max_iterations):
        if np.amax(num_nbr) < self.min_sta: break 
        # 1. ot assoc
        to_assoc_idx = None
        if center_order is None:
            center_order = iter(np.argsort(-num_nbr, kind='stable'))
        for center in center_order:
            if num_nbr[center] < self.min_sta:
                break
            candidate = neighborhoods.members(center)
            signature = tuple(int(index) for index in candidate)
            if signature not in failed_neighborhoods:
                to_assoc_idx = candidate
                break
            cache_hits += 1
        if to_assoc_idx is None:
            break  # Every eligible neighborhood failed on this unchanged set.
        # 2. loc assoc
        attempt_started = time.monotonic()
        event_loc, event_pick, assoc_idx, drop_idx = self.assoc_loc(
            picks[to_assoc_idx], unique_stations=unique_stations
        )
        attempts += 1
        location_seconds += time.monotonic() - attempt_started
        # Log exponentially spaced attempts, keeping even long failed searches visible.
        if verbose and attempts & (attempts - 1) == 0:
            owner = getattr(self.tt_dict, 'owner', None)
            arrays = getattr(owner, 'cached', {})
            print('[assoc] attempts={} failures={} events={} cluster_pairs={} '
                  'elapsed_s={:.1f} location_s={:.1f} table_load_s={:.1f} '
                  'cached_stations={} table_MiB={:.1f} failure_cache_hits={}'.format(
                      attempts, failures, len(events_loc), len(to_assoc_idx),
                      time.monotonic() - started, location_seconds,
                      getattr(owner, 'load_seconds', 0.0), len(arrays),
                      sum(a.nbytes for pair in arrays.values() for a in pair) / 2**20,
                      cache_hits),
                  flush=True)
        if not event_loc:
            failures += 1
            # Retain every pair for alternative centers. A failed hypothesis
            # is not evidence against an individual pick; do not exhaust its
            # drop allowance before other neighborhoods have been considered.
            failed_neighborhoods.add(signature)
            continue
        if len(event_loc)>0: 
            # 3. calc mag
            event_loc_mag = self.calc_mag(event_pick, event_loc)
            # screen output
            ot  = event_loc_mag['evt_ot']
            lon = event_loc_mag['evt_lon']
            lat = event_loc_mag['evt_lat']
            dep = event_loc_mag['evt_dep']
            mag = event_loc_mag['mag']
            res = event_loc_mag['res']
            if verbose:
              print('{} {:.4f} {:.4f} {:.1f} {} | res {}s'.format(ot, lat, lon, dep, mag, res), flush=True)
            # write catalog and phase
            if out_ctlg: self.write_catalog(event_loc_mag, out_ctlg)
            if out_pha: self.write_phase(event_loc_mag, event_pick, out_pha)
            events_loc.append(event_loc_mag)
            events_pick.append(event_pick)
        # del picks that are associated or dropped to many times
        drop_idx = to_assoc_idx[np.array(drop_idx, dtype=np.int32)]
        assoc_idx = to_assoc_idx[np.array(assoc_idx, dtype=np.int32)]
        num_drop[drop_idx] += 1
        to_del = np.unique(np.concatenate([
            assoc_idx, drop_idx[num_drop[drop_idx] > self.max_drop],
        ]))
        neighborhoods.remove(to_del)
        failed_neighborhoods.remove(to_del)
        center_order = None
    if verbose:
        print('[assoc] finished attempts={} failures={} events={} elapsed_s={:.1f} '
              'location_s={:.1f} failure_cache_hits={}'.format(
                  attempts, failures, len(events_loc),
                  time.monotonic() - started, location_seconds, cache_hits), flush=True)
    if not out_ctlg or not out_pha: return events_loc, events_pick 
    else: return

  @staticmethod
  def _validate_temporal_grid(step, width, points):
    for name, value in (("ot_grid_step_sec", step), ("ot_grid_max_width_sec", width)):
        if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
            raise ValueError("{} must be finite and positive".format(name))
    if isinstance(points, bool) or not isinstance(points, Integral) or points < 1:
        raise ValueError("ot_grid_max_points must be a positive integer")

  @staticmethod
  def origin_time_grid(picks, ot_grid_step_sec=0.1,
                       ot_grid_max_width_sec=1.0, ot_grid_max_points=10):
    """Median-anchored candidates inside P5--P95, capped by span and count."""
    PS_Pair_Assoc._validate_temporal_grid(
        ot_grid_step_sec, ot_grid_max_width_sec, ot_grid_max_points)
    step = ot_grid_step_sec
    cap = min(ot_grid_max_points, int(np.floor(ot_grid_max_width_sec / step + 1e-9)) + 1)
    if len(picks) == 0:
        return []
    anchor = picks['sta_ot'][0]
    offsets = np.asarray([float(value - anchor) for value in picks['sta_ot']])
    lower, median, upper = np.percentile(offsets, [5, 50, 95])
    first = int(np.ceil((lower - median) / step - 1e-9))
    last = int(np.floor((upper - median) / step + 1e-9))
    # With an even cap, prefer the earlier of equally centered windows.
    if last - first + 1 > cap:
        first = max(first, min(-(cap // 2), last - cap + 1))
        last = first + cap - 1
    return [anchor + float(median + index * step)
            for index in range(first, last + 1)]

  def _pair_residual(self, tp_pred, ts_pred, tp_obs, ts_obs):
    """Mean absolute P/S error, or infinity when either phase fails."""
    rp, rs = abs(tp_pred - tp_obs), abs(ts_pred - ts_obs)
    return np.where((rp < self.max_res[0]) & (rs < self.max_res[1]),
                    (rp + rs) / 2.0, np.inf)

  def _group_residuals(self, prepared_groups, observed, cells=None):
    """Yield the best qualifying *pair* score per station (or per pair)."""
    for indices, tp, ts in prepared_groups:
        if cells is not None:
            tp, ts = tp[cells], ts[cells]
        residual = None
        for index in indices:
            current = self._pair_residual(tp, ts, observed[index, 0], observed[index, 1])
            residual = current if residual is None else np.minimum(residual, current)
        yield residual

  def assoc_loc(self, picks, unique_stations=None):
    if unique_stations is None:
        unique_stations = getattr(self, 'unique_stations', True)
    if len(picks) == 0:
        return [], [], [], []
    grid_shape = next(iter(self.tt_dict.values())).shape
    station_indices = {}
    for index, station in enumerate(picks['net_sta']):
        station_indices.setdefault(station, []).append(index)
    groups = ([station_indices[station] for station in sorted(station_indices)]
              if unique_stations else [[index] for index in range(len(picks))])
    prepared_groups = [(indices, self.tt_dict[picks[indices[0]]['net_sta']].reshape(-1),
                        self.tt_s_dict[picks[indices[0]]['net_sta']].reshape(-1))
                       for indices in groups]
    best = None
    for candidate_ot in self.origin_time_grid(
        picks, getattr(self, 'ot_grid_step_sec', 0.1),
        getattr(self, 'ot_grid_max_width_sec', 1.0),
        getattr(self, 'ot_grid_max_points', 10),
    ):
        # Preserve the original per-arrival subtraction semantics (notably
        # UTCDateTime rounding), but evaluate it only once per temporal trial.
        observed = np.asarray([[float(pick['tp'] - candidate_ot),
                                float(pick['ts'] - candidate_ot)] for pick in picks])
        counts = np.zeros(int(np.prod(grid_shape)), dtype=np.uint32)
        for residual in self._group_residuals(prepared_groups, observed):
            counts += np.isfinite(residual)
        count = int(counts.max())
        if count < self.min_sta or (best is not None and count < best[0]):
            continue
        cells = np.flatnonzero(counts == count)
        # Only count-maximizing cells need medians. Bound the temporary matrix
        # to about one million doubles instead of retaining every pick's grid.
        batch_size = max(1, 1_000_000 // len(groups))
        for start in range(0, len(cells), batch_size):
            batch = cells[start:start + batch_size]
            residuals = np.empty((len(groups), len(batch)), dtype=float)
            for row, residual in enumerate(
                self._group_residuals(prepared_groups, observed, batch)
            ):
                residuals[row] = residual
            middle = ((count - 1) // 2, count // 2)
            residuals.partition(middle, axis=0)
            medians = (residuals[middle[0]] + residuals[middle[1]]) / 2.0
            index = int(np.argmin(medians))
            median_res = float(medians[index])
            if best is None or count > best[0] or median_res < best[1]:
                best = (count, median_res, candidate_ot, int(batch[index]))
    if best is None:
        return [], [], [], list(range(len(picks)))
    _, res, ot, cell = best
    zi, xi, yi = np.unravel_index(cell, grid_shape)
    lon = self.lon_min + xi * self.xy_grid
    lat = self.lat_min + yi * self.xy_grid
    dep = self.z_grids[zi]
    # find associated phase & index
    event_pick, assoc_idx, drop_idx = [], [], []
    if unique_stations:
        for net_sta in sorted(station_indices):
            indices = station_indices[net_sta]
            residuals = [float(self._pair_residual(
                self.tt_dict[net_sta][zi, xi, yi],
                self.tt_s_dict[net_sta][zi, xi, yi],
                float(picks[ii]['tp'] - ot), float(picks[ii]['ts'] - ot)))
                for ii in indices]
            best_pos = int(np.argmin(residuals))
            best_idx = int(indices[best_pos])
            for ii in indices:
                if int(ii) == best_idx and np.isfinite(residuals[best_pos]):
                    event_pick.append(picks[ii])
                    assoc_idx.append(int(ii))
                else:
                    drop_idx.append(int(ii))
    else:
        for ii,pick in enumerate(picks):
            station = pick['net_sta']
            residual = self._pair_residual(
                self.tt_dict[station][zi, xi, yi], self.tt_s_dict[station][zi, xi, yi],
                float(pick['tp'] - ot), float(pick['ts'] - ot))
            if np.isfinite(residual):
                event_pick.append(pick)
                assoc_idx.append(ii)
            else: drop_idx.append(ii)
    # output as dict
    event_loc = {'evt_ot' : ot, 
                 'evt_lon': float(lon),
                 'evt_lat': float(lat),
                 'evt_dep': float(dep),
                 'res': round(res,1)}
    return event_loc, event_pick, assoc_idx, drop_idx

  # calc P/S travel time tables
  def calc_tt(self):
    if self.travel_time_table is not None:
        from travel_time_table_io import TravelTimeTable, StationPhaseTables
        reader = TravelTimeTable(self.travel_time_table)
        if not reader.is_archive:
            raise ValueError('travel_time_table must name a single .paltt archive')
        if reader.manifest['config']['datum']['source_depth_reference'] != 'mean_sea_level':
            raise ValueError('PAL requires table source depths below mean sea level')
        lat, lon = reader.grid['latitude_deg'], reader.grid['longitude_deg']
        steps = [np.diff(a) for a in (lat, lon) if len(a) > 1]
        if steps and any(not np.allclose(d, steps[0][0], rtol=0, atol=1e-9) for d in steps):
            raise ValueError('PAL requires a regular grid with equal latitude/longitude spacing')
        self.xy_grid = float(steps[0][0]) if steps else 1.0
        self.lat_min, self.lon_min = float(lat[0]), float(lon[0])
        self.z_grids = reader.grid['source_depth_msl_km']
        self._table_reader = reader
        self._station_tables = StationPhaseTables(reader, self.sta_dict)
        self.tt_s_dict = self._station_tables.view(1)
        debug('using saved P/S travel table: {}'.format(self.travel_time_table))
        return self._station_tables.view(0)
    debug('making time table')
    tt_dict = {}
    self.tt_s_dict = {}
    # get x-y range: sta range + margin
    sta_loc = self.sta_dict.values()
    lat = [sta_loc[0] for sta_loc in self.sta_dict.values()]
    lon = [sta_loc[1] for sta_loc in self.sta_dict.values()]
    lon_margin = self.xy_margin * (np.amax(lon) - np.amin(lon))
    lat_margin = self.xy_margin * (np.amax(lat) - np.amin(lat))
    lon_min, lon_max = (
      self.lon_range
      if self.lon_range is not None
      else (np.amin(lon)-lon_margin, np.amax(lon)+lon_margin)
    )
    lat_min, lat_max = (
      self.lat_range
      if self.lat_range is not None
      else (np.amin(lat)-lat_margin, np.amax(lat)+lat_margin)
    )
    # set x-y grid
    x_span = (lon_max-lon_min) / self.xy_grid
    y_span = (lat_max-lat_min) / self.xy_grid
    x_num = max(
      1,
      int(np.floor(x_span + 1e-10)) + 1
    )
    y_num = max(
      1,
      int(np.floor(y_span + 1e-10)) + 1
    )
    # calc P/S travel time tables. Array dimensions remain [z, x, y].
    grid_lon = lon_min + np.arange(x_num) * self.xy_grid
    grid_lat = lat_min + np.arange(y_num) * self.xy_grid
    depth = np.asarray(self.z_grids, dtype=float)[:, None, None]
    for net_sta, [sta_lat,sta_lon,sta_ele,_] in self.sta_dict.items():
        cos_lat = np.cos(sta_lat * np.pi/180)
        dx = 111 * (grid_lon - sta_lon) * cos_lat
        dy = 111 * (grid_lat - sta_lat)
        dz = depth + sta_ele/1000.
        distance = np.sqrt(
            dz**2 + dx[None, :, None]**2 + dy[None, None, :]**2
        )
        tt_dict[net_sta] = (distance / self.velo_mod[0]).astype(np.float32)
        self.tt_s_dict[net_sta] = (distance / self.velo_mod[1]).astype(np.float32)
    self.lat_min, self.lon_min = lat_min, lon_min
    return tt_dict

  def calc_mag(self, event_pick, event_loc):
    mag = []
    for pick in event_pick:
        sta_lat, sta_lon, sta_ele = self.sta_dict[pick['net_sta']][0:3]
        # get S amp
        if 's_amp' not in pick.dtype.names: continue
        amp = pick['s_amp'] * 1e6 # m to miu m
        if not np.isfinite(amp) or amp <= 0: continue
        # calc epi dist
        dist_lat = 111*(sta_lat - event_loc['evt_lat'])
        dist_lon = 111*(sta_lon - event_loc['evt_lon']) * np.cos(sta_lat*np.pi/180)
        dist_dep = event_loc['evt_dep'] + sta_ele/1e3
        dist = np.sqrt(dist_lon**2 + dist_lat**2 + dist_dep**2)
        if not np.isfinite(dist) or dist <= 0: continue
        station_mag = np.log10(amp) + np.log10(dist) + 1
        if np.isfinite(station_mag): mag.append((pick['net_sta'], station_mag))
    event_loc.update(station_magnitude_summary(mag, self.mag_min_stations, self.mag_max_std))
    return event_loc

  def write_catalog(self, event_loc, out_ctlg):
    ot  = event_loc['evt_ot']
    lon = event_loc['evt_lon']
    lat = event_loc['evt_lat']
    dep = event_loc['evt_dep']
    mag = event_loc['mag'] if 'mag' in event_loc else float("nan")
    out_ctlg.write('{},{:.4f},{:.4f},{:.1f},{:.2f}\n'.format(format_assoc_time(ot), lat, lon, dep, mag))

  def write_phase(self, event_loc, event_pick, out_pha):
    ot  = event_loc['evt_ot']
    lon = event_loc['evt_lon']
    lat = event_loc['evt_lat']
    dep = event_loc['evt_dep']
    mag = event_loc['mag']
    out_pha.write('{},{:.4f},{:.4f},{:.1f},{:.2f}\n'.format(format_assoc_time(ot), lat, lon, dep, mag))
    for pick in event_pick:
        net_sta = pick['net_sta']
        tp = pick['tp']
        ts = pick['ts']
        s_amp = pick['s_amp'] if 's_amp' in pick.dtype.names else -1
        p_prob = pick['p_prob'] if 'p_prob' in pick.dtype.names else -1
        s_prob = pick['s_prob'] if 's_prob' in pick.dtype.names else -1
        if 'p_prob' in pick.dtype.names and 's_prob' in pick.dtype.names:
            tp_std = pick['tp_std'] if 'tp_std' in pick.dtype.names else 0
            ts_std = pick['ts_std'] if 'ts_std' in pick.dtype.names else 0
            p_prob_std = pick['p_prob_std'] if 'p_prob_std' in pick.dtype.names else 0
            s_prob_std = pick['s_prob_std'] if 's_prob_std' in pick.dtype.names else 0
            num_support = pick['num_support'] if 'num_support' in pick.dtype.names else 1
            sources = pick['sources'] if 'sources' in pick.dtype.names else ''
            picker_cluster_sizes = (
                pick['picker_cluster_sizes']
                if 'picker_cluster_sizes' in pick.dtype.names else ''
            )
            out_pha.write(
                '{},{},{},{},{:.4f},{:.4f},{:.4f},{:.4f},'
                '{:.4f},{:.4f},{},{},{}\n'.format(
                    net_sta, format_assoc_time(tp), format_assoc_time(ts), s_amp,
                    p_prob, s_prob, tp_std, ts_std, p_prob_std, s_prob_std,
                    num_support, sources, picker_cluster_sizes,
                )
            )
        else:
            out_pha.write('{},{},{},{}\n'.format(net_sta, format_assoc_time(tp), format_assoc_time(ts), s_amp))
