from datetime import timedelta
import numpy as np


def format_assoc_time(value):
  dt = value.datetime + timedelta(microseconds=5000)
  dt = dt.replace(microsecond=(dt.microsecond // 10000) * 10000)
  return dt.isoformat(timespec='milliseconds')[:-1] + 'Z'

class PS_Pair_Assoc(object):
  """ Associate P- & S-pick Pairs by searching ot and loc clustering
  Inputs
    sta_dict: station location dict
    xy_margin: ratio of lateral (x-y) margin relative to the station range
    xy_grid: grid width for x-y axis (in degree)
    z_grids: grids for z axis (in km)
    ot_dev: max time dev for ot assoc
    max_res: threshold for P travel time res
    max_drop: each pick can only be dropped for max_drop times before being associated 
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
               vp        = 5.9,
               ot_dev    = 1.4,
               max_res   = 1.2,
               max_drop  = 1, 
               min_sta   = 4):
    self.sta_dict = sta_dict
    self.xy_margin = xy_margin
    self.xy_grid = xy_grid
    self.z_grids = z_grids
    self.vp = vp
    self.ot_dev = ot_dev
    self.max_res = max_res
    self.max_drop = max_drop
    self.min_sta = min_sta
    self.tt_dict = self.calc_tt()

  def associate(
      self, picks, out_ctlg=None, out_pha=None, verbose=True,
      unique_stations=False,
  ):
    events_loc, events_pick = [], []
    num_picks = len(picks)
    if num_picks==0: return 
    picks = np.sort(picks, order='sta_ot')
    # calc num of ot neighbors 
    num_nbr = np.zeros(num_picks) 
    num_drop = np.zeros(num_picks) 
    for ii in range(num_picks):
        neighbor_mask = abs(picks['sta_ot']-picks['sta_ot'][ii]) < self.ot_dev
        num_nbr[ii] = (
            len(set(picks['net_sta'][neighbor_mask]))
            if unique_stations else np.sum(neighbor_mask)
        )
    # assoc each cluster
    if verbose: print('-'*40+'\n'+'detected events:')
    for _ in range(num_picks):
        if len(picks) == 0: break
        if np.amax(num_nbr) < self.min_sta: break 
        # 1. ot assoc
        ots = picks['sta_ot']
        ot_i = ots[np.argmax(num_nbr)]
        to_assoc_idx = np.where(abs(ots-ot_i) < self.ot_dev)[0]
        # 2. loc assoc
        event_loc, event_pick, assoc_idx, drop_idx = self.assoc_loc(
            picks[to_assoc_idx], unique_stations=unique_stations
        )
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
              print('{} {} {} {:>2} {} | res {}s'.format(ot, lat, lon, dep, mag, res))
            # write catalog and phase
            if out_ctlg: self.write_catalog(event_loc_mag, out_ctlg)
            if out_pha: self.write_phase(event_loc_mag, event_pick, out_pha)
            events_loc.append(event_loc_mag)
            events_pick.append(event_pick)
        # del picks that are associated or dropped to many times
        drop_idx = np.array(drop_idx, dtype=np.int32) + to_assoc_idx[0]
        assoc_idx = np.array(assoc_idx, dtype=np.int32) + to_assoc_idx[0]
        num_drop[drop_idx] += 1
        to_del = np.unique(np.concatenate([assoc_idx, np.where(num_drop > self.max_drop)[0]]))
        # update picks, num_nbr, and num_drop
        picks = np.delete(picks, to_del)
        num_drop = np.delete(num_drop, to_del)
        if unique_stations:
            num_nbr = np.zeros(len(picks), dtype=float)
            for idx in range(len(picks)):
                neighbor_mask = (
                    abs(picks['sta_ot'] - picks['sta_ot'][idx]) < self.ot_dev
                )
                num_nbr[idx] = len(set(picks['net_sta'][neighbor_mask]))
        else:
            num_nbr = np.delete(num_nbr, to_del)
            to_renew_idx = np.where(abs(picks['sta_ot']-ot_i) < 2*self.ot_dev)[0]
            for idx in to_renew_idx:
                num_nbr[idx] = sum(
                    abs(picks['sta_ot']-picks['sta_ot'][idx]) < self.ot_dev
                )
    if not out_ctlg or not out_pha: return events_loc, events_pick 
    else: return

  def assoc_loc(self, picks, unique_stations=False):
    grid_shape = next(iter(self.tt_dict.values())).shape
    res_ttp_mat = np.zeros(grid_shape, dtype=np.float64)
    # A boolean detection mask is consumed immediately. Retaining one full
    # float grid per pick can consume gigabytes for dense realtime clusters.
    num_sta_mat = np.zeros(grid_shape, dtype=np.uint32)
    ot = picks['sta_ot'][len(picks)//2]
    if unique_stations:
        for net_sta in sorted(set(picks['net_sta'])):
            station_picks = picks[picks['net_sta'] == net_sta]
            station_residual = None
            for pick in station_picks:
                ttp_obs = pick['tp'] - ot
                residual = abs(self.tt_dict[net_sta] - ttp_obs)
                station_residual = (
                    residual if station_residual is None
                    else np.minimum(station_residual, residual)
                )
            is_det = station_residual < self.max_res
            res_ttp_mat += np.where(is_det, station_residual, 0.0)
            num_sta_mat += is_det
    else:
        for pick in picks:
            net_sta = pick['net_sta']
            ttp_obs = pick['tp'] - ot # pick time to travel time
            ttp_pred = self.tt_dict[net_sta]
            res_i = abs(ttp_pred - ttp_obs)
            is_det = res_i < self.max_res
            res_i[res_i >= self.max_res] = 0.
            # Update aggregate grids only. Station membership is recomputed at the
            # selected cell below, avoiding a full-grid mask for every pick.
            res_ttp_mat += res_i
            num_sta_mat += is_det
    # find loc of min res (grid search location)
    num_sta = np.amax(num_sta_mat)
    if num_sta < self.min_sta: return [],[],[],list(range(len(picks)))
    res_ttp_mat /= num_sta
    res_ttp_mat [num_sta_mat < num_sta] = np.inf
    res = np.amin(res_ttp_mat)
    zi, xi, yi = np.unravel_index(np.argmin(res_ttp_mat), res_ttp_mat.shape)
    lon = self.lon_min + xi * self.xy_grid
    lat = self.lat_min + yi * self.xy_grid
    dep = self.z_grids[zi]
    # find associated phase & index
    event_pick, assoc_idx, drop_idx = [], [], []
    if unique_stations:
        for net_sta in sorted(set(picks['net_sta'])):
            indices = np.where(picks['net_sta'] == net_sta)[0]
            residuals = [
                abs(
                    self.tt_dict[net_sta][zi, xi, yi]
                    - (picks[ii]['tp'] - ot)
                )
                for ii in indices
            ]
            best_pos = int(np.argmin(residuals))
            best_idx = int(indices[best_pos])
            for ii in indices:
                if int(ii) == best_idx and residuals[best_pos] < self.max_res:
                    event_pick.append(picks[ii])
                    assoc_idx.append(int(ii))
                else:
                    drop_idx.append(int(ii))
    else:
        for ii,pick in enumerate(picks):
            ttp_obs = pick['tp'] - ot
            ttp_pred = self.tt_dict[pick['net_sta']][zi, xi, yi]
            if abs(ttp_pred - ttp_obs) < self.max_res:
                event_pick.append(pick)
                assoc_idx.append(ii)
            else: drop_idx.append(ii)
    # output as dict
    event_loc = {'evt_ot' : ot, 
                 'evt_lon': round(lon,2), 
                 'evt_lat': round(lat,2),
                 'evt_dep': round(dep,0),
                 'res': round(res,1)}
    return event_loc, event_pick, assoc_idx, drop_idx

  # calc P travel time table
  def calc_tt(self):
    print('making time table')
    tt_dict = {}
    # get x-y range: sta range + margin
    sta_loc = self.sta_dict.values()
    lat = [sta_loc[0] for sta_loc in self.sta_dict.values()]
    lon = [sta_loc[1] for sta_loc in self.sta_dict.values()]
    lon_margin = self.xy_margin * (np.amax(lon) - np.amin(lon))
    lat_margin = self.xy_margin * (np.amax(lat) - np.amin(lat))
    lon_min, lon_max = np.amin(lon)-lon_margin, np.amax(lon)+lon_margin
    lat_min, lat_max = np.amin(lat)-lat_margin, np.amax(lat)+lat_margin
    # set x-y grid
    x_num = int((lon_max-lon_min) / self.xy_grid)
    y_num = int((lat_max-lat_min) / self.xy_grid)
    # calc P travel time table. Array dimensions remain [z, x, y].
    grid_lon = lon_min + np.arange(x_num) * self.xy_grid
    grid_lat = lat_min + np.arange(y_num) * self.xy_grid
    depth = np.asarray(self.z_grids, dtype=float)[:, None, None]
    for net_sta, [sta_lat,sta_lon,sta_ele,_] in self.sta_dict.items():
        cos_lat = np.cos(sta_lat * np.pi/180)
        dx = 111 * (grid_lon - sta_lon) * cos_lat
        dy = 111 * (grid_lat - sta_lat)
        dz = depth + sta_ele/1000.
        ttp = np.sqrt(
            dz**2 + dx[None, :, None]**2 + dy[None, None, :]**2
        ) / self.vp
        tt_dict[net_sta] = ttp
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
        if np.isfinite(station_mag): mag.append(station_mag)
    mag = np.asarray(mag, dtype=float)
    # Preserve the PAL one-outlier rejection when enough valid amplitudes exist.
    if len(mag) >= 3:
        mag_dev = abs(mag - np.median(mag))
        mag = np.delete(mag, np.argmax(mag_dev))
    event_loc['mag'] = round(float(np.median(mag)),2) if len(mag) else -1.0
    return event_loc

  def write_catalog(self, event_loc, out_ctlg):
    ot  = event_loc['evt_ot']
    lon = event_loc['evt_lon']
    lat = event_loc['evt_lat']
    dep = event_loc['evt_dep']
    mag = event_loc['mag'] if 'mag' in event_loc else -1
    out_ctlg.write('{},{:.5f},{:.5f},{:.1f},{:.2f}\n'.format(format_assoc_time(ot), lat, lon, dep, mag))

  def write_phase(self, event_loc, event_pick, out_pha):
    ot  = event_loc['evt_ot']
    lon = event_loc['evt_lon']
    lat = event_loc['evt_lat']
    dep = event_loc['evt_dep']
    mag = event_loc['mag']
    out_pha.write('{},{:.5f},{:.5f},{:.1f},{:.2f}\n'.format(format_assoc_time(ot), lat, lon, dep, mag))
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
