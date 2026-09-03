import numpy as np

class STA_LTA_Kurtosis(object):
  """ STA/LTA & kurtosis-based P&S Picker
    trigger picker: Z chn STA/LTA reach trig_thres
    --> pick P: find STA/LTA peak within p_win
    --> pick S: find kurtosis peak winthin s_win
  Inputs
    stream: obspy.stream obj (3 chn, [e, n, z])
    win_sta, win_lta: win for sta/lta (det, p, s)
    trig_thres: threshold to trig picker
    p_win, s_win: win len for searching P & S
    pca_win: time win for calc pca filter
    pca_range: time range for pca filter
    win_kurt: win for calc kurtosis
    amp_ratio_thres: max value of amp ratio, peak_rm, P/P_tail, & P/S
    amp_win: time win to get S amplitude
    det_gap: time gap between detections
    to_prep: whether preprocess stream
    freq_band: frequency band for phase picking
    *note: all time-related params are in sec
  Outputs
    output to file or picks (struct np.array)
  Usage
    import picker_pal
    picker = picker_pal.STA_LTA_Kurtosis()
    picks = picker.pick(stream)
  """
  def __init__(self, 
               win_sta         = [.8, 0.4, 1.],
               win_lta         = [6., 2., 2.],
               trig_thres      = 12.,
               p_win           = [.5, 1.],
               s_win           = 10,
               pca_win         = 1.,
               pca_range       = [0., 2],
               win_kurt        = [5.,1.],
               amp_ratio_thres = [6,10,2], 
               amp_win         = [1.,5.],
               det_gap         = 5.,
               to_prep         = True,
               freq_band       = [1., 40],
               taper_max_length_sec = 10.0,
               vp              = 6.0,
               vs              = 3.45,
               verbose         = False):
    self.win_sta = win_sta
    self.win_lta = win_lta
    self.trig_thres = trig_thres
    self.p_win = p_win
    self.s_win = s_win
    self.pca_win = pca_win
    self.pca_range = pca_range
    self.win_kurt = win_kurt
    self.amp_ratio_thres = amp_ratio_thres
    self.amp_win = amp_win
    self.det_gap = det_gap
    self.to_prep = to_prep
    self.freq_band = freq_band
    self.taper_max_length_sec = float(taper_max_length_sec)
    self.vp = vp
    self.vs = vs
    self.verbose = bool(verbose)

  def _log(self, message):
    if self.verbose:
      print(message)

  @staticmethod
  def get_s_sta_search_start_npts(dt_peak_npts, pca_end_npts):
    """Return the S STA/LTA search start offset relative to the P pick."""
    return min(int(pca_end_npts), int(dt_peak_npts)//2)

  def pick(self, stream, out_file=None, pick_start_time=None, pick_end_time=None,
           return_trigger_count=False):
    # set output format for picks
    dtype = [('net_sta','O'),
             ('sta_ot','O'),
             ('tp','O'),
             ('ts','O'),
             ('s_amp','O')]
    def result(rows, trigger_count):
      picks_array = np.array(rows, dtype=dtype)
      if return_trigger_count:
        return picks_array, trigger_count
      return picks_array

    # preprocess & extract data
    if len(stream)!=3: return result([], 0)
    if self.to_prep: stream = self.preprocess(stream, self.freq_band)
    if len(stream)==3:
      usable_start = stream[0].stats.starttime + self.taper_max_length_sec
      usable_end = stream[0].stats.endtime - self.taper_max_length_sec
      if usable_end <= usable_start: return result([], 0)
      stream = stream.slice(usable_start, usable_end, nearest_sample=True)
    if len(stream)!=3: return result([], 0)
    min_npts = min([len(trace) for trace in stream])
    st_data = np.array([trace.data[0:min_npts] for trace in stream])
    # get header
    head = stream[0].stats
    net_sta = '.'.join([head.network, head.station])
    samp_rate = head.sampling_rate
    start_time, end_time = head.starttime, head.endtime
    # sec to points
    win_sta_npts   = [int(samp_rate * win) for win in self.win_sta]
    win_lta_npts   = [int(samp_rate * win) for win in self.win_lta]
    p_win_npts     = [int(samp_rate * win) for win in self.p_win]
    s_win_npts     =  int(samp_rate * self.s_win)
    pca_win_npts   =  int(samp_rate * self.pca_win)
    pca_range_npts = [int(samp_rate * win) for win in self.pca_range]
    win_kurt_npts  = [int(samp_rate * win) for win in self.win_kurt]
    amp_win_npts   = [int(samp_rate * win) for win in self.amp_win]
    det_gap_npts   =  int(samp_rate * self.det_gap)
    # pick P and S
    picks = []
    # 1. trig picker
    self._log('1. triggering phase picker')
    cf_trig = self.calc_sta_lta(st_data[2]**2, win_lta_npts[0], win_sta_npts[0])
    trig_index = np.where(cf_trig > self.trig_thres)[0]
    slide_idx = 0
    # trig_index contains every above-threshold sample. Count only candidates
    # selected by the picker's det_gap progression, before waveform QC.
    num_triggers = 0
    # 2. phase picking
    self._log('2. picking phase:')
    for _ in trig_index:
        trig_idx = trig_index[slide_idx]
        if trig_idx < p_win_npts[0] + max(win_lta_npts):
            slide_idx += 1; continue
        # 2.1 pick P with STA/LTA
        p_idx0 = trig_idx - p_win_npts[0] - win_lta_npts[1]
        p_idx1 = trig_idx + p_win_npts[1] + win_sta_npts[1]
        data_p = st_data[2,p_idx0:p_idx1]**2
        cf_p = self.calc_sta_lta(data_p, win_lta_npts[1], win_sta_npts[1])
        tp0_idx = np.argmax(cf_p) + p_idx0
        # refine initial pick on waveform
        tp_idx = tp0_idx - self.find_second_peak(data_p[0:tp0_idx-p_idx0][::-1])
        tp = start_time + tp_idx/samp_rate
        trigger_time = start_time + trig_idx/samp_rate
        if (
            (pick_start_time is None or trigger_time >= pick_start_time)
            and (pick_end_time is None or trigger_time < pick_end_time)
        ):
            num_triggers += 1
        # 2.2 pick S 
        # 2.2.1 pca for amp_peak
        if len(st_data[0]) < tp_idx + s_win_npts: break
        s_idx0 = tp_idx - pca_range_npts[0]
        s_idx1 = max(tp_idx + s_win_npts, tp_idx + pca_range_npts[1])
        data_s = np.sum(st_data[0:2, s_idx0:s_idx1]**2, axis=0)**0.5
        pca_filter = self.calc_pca_filter(st_data, tp_idx, pca_range_npts, pca_win_npts)
        data_s[0:len(pca_filter)] *= pca_filter
        dt_peak = max(np.argmax(data_s)+1, pca_win_npts+1)
        # 2.2.2 S STA/LTA --> earliest S boundary. Search from the
        # earlier of the PCA interval end and half the P-to-S-peak time.
        sta_search_start = self.get_s_sta_search_start_npts(
            dt_peak, pca_range_npts[1]
        )
        sta_search_span = dt_peak - sta_search_start
        s_idx0 = tp_idx + sta_search_start - win_lta_npts[2]
        s_idx1 = tp_idx + dt_peak + win_sta_npts[2]
        data_s_sta = np.sum(st_data[0:2, s_idx0:s_idx1]**2, axis=0)
        cf_s = self.calc_sta_lta(
            data_s_sta, win_lta_npts[2], win_sta_npts[2]
        )[win_lta_npts[2]:win_lta_npts[2]+sta_search_span+1]
        dt_min_relative = np.argmax(cf_s)
        dt_min = sta_search_start + dt_min_relative

        # 2.2.3 Long-window kurtosis --> latest S boundary. Its output is
        # calculated only from the STA/LTA peak through the S-amplitude peak.
        s_idx0 = tp_idx + dt_min - win_kurt_npts[0]
        s_idx1 = tp_idx + dt_peak
        data_s = np.sum(st_data[0:2, s_idx0:s_idx1]**2, axis=0)
        data_s /= np.amax(data_s)
        kurt_long = self.calc_kurtosis(data_s, win_kurt_npts[0])
        dt_max_relative = np.argmax(kurt_long)
        dt_max_relative -= self.find_first_peak(
            kurt_long[0:dt_max_relative+1][::-1]
        )
        dt_max = dt_min + dt_max_relative

        # 2.2.4 Pick S on short-window kurtosis.
        # if kurt_long not stable, use STA/LTA
        if dt_min>=dt_max: 
            ts0_idx = tp_idx + dt_min
            sta_candidate_end = win_lta_npts[2] + dt_min_relative
            ts_idx = ts0_idx - self.find_second_peak(
                data_s_sta[0:sta_candidate_end][::-1]
            )
        # else, pick peak of kurt_short
        else:
            s_idx0 = tp_idx + dt_min - win_kurt_npts[1]
            s_idx1 = tp_idx + dt_max
            data_s = np.sum(st_data[0:2, s_idx0:s_idx1]**2, axis=0)
            data_s /= np.amax(data_s)
            kurt_short = self.calc_kurtosis(data_s, win_kurt_npts[1])
            kurt_max = np.argmax(kurt_short)
            if kurt_max == 0:
                kurt_max = dt_max-dt_min
            ts0_idx = tp_idx + dt_min + kurt_max
            ts_idx = ts0_idx - self.find_second_peak(
                data_s[0:win_kurt_npts[1]+kurt_max][::-1]
            )
        ts = start_time + ts_idx/samp_rate if ts_idx>tp_idx else start_time + ts0_idx/samp_rate
        # 3. get related S amplitude
        data_amp = st_data[:, tp_idx-amp_win_npts[0] : ts_idx+amp_win_npts[1]].copy()
        s_amp = self.get_s_amp(data_amp, samp_rate)
        # 4. get p_snr
        p_snr = np.amax(cf_trig[p_idx0:p_idx1])
        # 5. quality control with amplitude ratios
        p_amp_ratio = self.calc_peak_amp_ratio(stream.slice(tp, tp+self.pca_win*3), pca_win_npts)
        s_amp_ratio = self.calc_peak_amp_ratio(stream.slice(ts, ts+self.pca_win*3), pca_win_npts)
        amp_ratio = max(min(p_amp_ratio), min(s_amp_ratio))
        A1 = np.array([np.amax(tr.data)-np.amin(tr.data) for tr in stream.slice(tp, tp+(ts-tp)/2)])
        A2 = np.array([np.amax(tr.data)-np.amin(tr.data) for tr in stream.slice(tp+(ts-tp)/2, ts)])
        A3 = np.array([np.amax(tr.data)-np.amin(tr.data) for tr in stream.slice(ts, ts+(ts-tp)/2)])
        A12 = min([A1[ii]/A2[ii] for ii in range(3)])
        A13 = min([A1[ii]/A3[ii] for ii in range(3)])
        # output picks
        in_target_day = (
            (pick_start_time is None or tp >= pick_start_time)
            and (pick_end_time is None or tp < pick_end_time)
        )
        if in_target_day and amp_ratio<self.amp_ratio_thres[0] and A12<self.amp_ratio_thres[1] and A13<self.amp_ratio_thres[2]:
            self._log('{}, {}, {}'.format(net_sta, tp, ts))
            sta_ot = self.calc_ot(tp, ts)
            picks.append((net_sta, sta_ot, tp, ts, s_amp))
            if out_file: 
                qual_code = '{:.1f},{:.1f},{:.1f},{:.1f}'.format(p_snr, amp_ratio, A12, A13)
                out_file.write('{},{},{},{},{},{}\n'.format(net_sta, sta_ot, tp, ts, s_amp, qual_code))
        # next detected phase
        rest_det = np.where(trig_index > max(trig_idx,ts_idx,tp_idx) + det_gap_npts)[0]
        if len(rest_det)==0: break
        slide_idx = rest_det[0]
    # convert to structed np.array
    return result(picks, num_triggers)

  # calc STA/LTA for a trace of data (abs or square)
  def calc_sta_lta(self, data, win_lta_npts, win_sta_npts):
    npts = len(data)
    if npts < win_lta_npts + win_sta_npts:
        self._log('input data too short!')
        return np.zeros(1)
    sta = np.zeros(npts)
    lta = np.ones(npts)
    data_cum = np.cumsum(data)
    sta[:-win_sta_npts] = data_cum[win_sta_npts:] - data_cum[:-win_sta_npts]
    sta /= win_sta_npts
    lta[win_lta_npts:]  = data_cum[win_lta_npts:] - data_cum[:-win_lta_npts]
    lta /= win_lta_npts
    sta_lta = sta/lta
    sta_lta[0:win_lta_npts] = 0.
    sta_lta[np.isinf(sta_lta)] = 0.
    sta_lta[np.isnan(sta_lta)] = 0.
    return sta_lta

  # calc P wave filter
  def calc_pca_filter(self, data, idx_p, pca_range_npts, pca_win_npts):
    p_mat = data[:, idx_p : idx_p + pca_win_npts]
    p_r, p_v = self.calc_pol(p_mat)
    idx_range = range(idx_p - pca_range_npts[0],
                      idx_p + pca_range_npts[1])
    pca_filter = np.zeros(len(idx_range))
    for i, idx in enumerate(idx_range):
        s_mat = data[:, idx : idx + pca_win_npts]
        s_r, s_v = self.calc_pol(s_mat)
        abs_cos = abs(np.dot(p_v, s_v))
        pca_filter[i] = 1 - s_r * abs_cos
    return pca_filter

  # calc pol_rate & pol_vec
  def calc_pol(self, mat):
    cov = np.cov(mat)
    eig_val, eig_vec = np.linalg.eig(cov)
    lam1  = abs(np.amax(eig_val))
    lam23 = abs(np.sum(eig_val) - lam1)
    pol_rate = 1 - (0.5 * lam23 / lam1)
    pol_vec = eig_vec.T[np.argmax(eig_val)]
    return pol_rate, pol_vec

  # calculate origin time
  def calc_ot(self, tp, ts):
    dist = (ts-tp) / (1/self.vs - 1/self.vp)
    tt_p = dist / self.vp
    return tp - tt_p

  # get S amplitide
  def get_s_amp(self, velo, samp_rate):
    # remove mean
    velo -= np.reshape(np.mean(velo, axis=1), [velo.shape[0],1])
    # velocity to displacement
    disp = np.cumsum(velo, axis=1)
    disp /= samp_rate
    return np.amax(abs(np.sum(disp**2, axis=0)))**0.5

  # calc kurtosis trace
  def calc_kurtosis(self, data, win_kurt_npts):
    data = np.asarray(data, dtype=np.float64)
    win_kurt_npts = int(win_kurt_npts)
    if win_kurt_npts <= 0:
      raise ValueError('win_kurt_npts must be positive')

    npts = len(data) - win_kurt_npts + 1
    if npts <= 0:
      return np.zeros(0, dtype=np.float64)

    # Compute every rolling window from cumulative raw moments. Centering the
    # full trace first improves stability without changing window kurtosis.
    data = data - np.mean(data)

    def rolling_sum(values):
      cumulative = np.concatenate((
          np.zeros(1, dtype=np.float64),
          np.cumsum(values, dtype=np.float64),
      ))
      return cumulative[win_kurt_npts:] - cumulative[:-win_kurt_npts]

    count = float(win_kurt_npts)
    sum1 = rolling_sum(data)
    squared = data * data
    sum2 = rolling_sum(squared)
    sum3 = rolling_sum(squared * data)
    sum4 = rolling_sum(squared * squared)
    mean = sum1 / count
    moment2 = sum2 / count - mean * mean
    moment4 = (
        sum4 / count
        - 4.0 * mean * sum3 / count
        + 6.0 * mean * mean * sum2 / count
        - 3.0 * mean**4
    )

    kurt = np.full(npts, np.nan, dtype=np.float64)
    valid = moment2 > 0.0
    kurt[valid] = moment4[valid] / moment2[valid]**2 - 3.0
    return kurt

  def calc_peak_amp_ratio(self, st, win_peak_npts):
    # find peak idx
    peak_data = np.array([abs(tr.data[0:win_peak_npts]) for tr in st])
    chn_idx = np.unravel_index(np.argmax(peak_data), peak_data.shape)[0]
    idx0 = np.argmax(abs(st[chn_idx].data[0:win_peak_npts]))
    idx1 = idx0 + self.find_first_peak(st[chn_idx].data[idx0:])
    idx0 -= self.find_second_peak(st[chn_idx].data[0:idx0][::-1])
    idx1 += self.find_second_peak(st[chn_idx].data[idx1:])+1
    idx0 = max(0,idx0)
    # calc peak amp ratio 
    amp_ratio = []
    for tr in st:
        amp_peak = np.amax(tr.data[idx0:idx1]) - np.amin(tr.data[idx0:idx1])
        amp_tail = np.amax(tr.data[idx1:2*idx1-idx0]) - np.amin(tr.data[idx1:2*idx1-idx0])
        amp_ratio.append(amp_peak/amp_tail)
    return amp_ratio

  def find_first_peak(self, data):
    npts = len(data)
    if npts<2: return 0
    delta_d = data[1:npts] - data[0:npts-1]
    if min(delta_d)>=0 or max(delta_d)<=0: return 0
    neg_idx = np.where(delta_d<0)[0]
    pos_idx = np.where(delta_d>=0)[0]
    return max(neg_idx[0], pos_idx[0])

  def find_second_peak(self, data):
    npts = len(data)
    if npts<2: return 0
    delta_d = data[1:npts] - data[0:npts-1]
    if min(delta_d)>=0 or max(delta_d)<=0: return 0
    neg_idx = np.where(delta_d<0)[0]
    pos_idx = np.where(delta_d>=0)[0]
    if len(neg_idx)==0 or len(pos_idx)==0: return 0
    first_peak = max(neg_idx[0], pos_idx[0])
    neg_peak = neg_idx[neg_idx>first_peak]
    pos_peak = pos_idx[pos_idx>first_peak]
    if len(neg_peak)==0 or len(pos_peak)==0: return first_peak
    return max(neg_peak[0], pos_peak[0])

  def preprocess(self, stream, freq_band, max_gap=5.):
    # time alignment
    start_time = max([trace.stats.starttime for trace in stream])
    end_time = min([trace.stats.endtime for trace in stream])
    if start_time > end_time: return []
    stream = stream.slice(start_time, end_time, nearest_sample=True)
    # remove nan & inf
    for trace in stream:
        trace.data[np.isnan(trace.data)] = 0
        trace.data[np.isinf(trace.data)] = 0
    # check missed chn
    if max(stream.max())==0: return []
    if 0 in stream.max():
        is_miss = np.array(stream.max())==0
        for ii in np.where(is_miss)[0]: stream[ii] = stream[np.where(~is_miss)[0][-1]].copy()
    # fill data gap
    max_gap_npts = int(max_gap*stream[0].stats.sampling_rate)
    for trace in stream:
        data = trace.data
        npts = len(data)
        data_diff = np.diff(data)
        gap_idx = np.where(data_diff==0)[0]
        gap_list = np.split(gap_idx, np.where(np.diff(gap_idx)!=1)[0] + 1)
        gap_list = [gap for gap in gap_list if len(gap)>=3]
        num_gap = len(gap_list)
        for ii,gap in enumerate(gap_list):
            idx0, idx1 = max(0, gap[0]-1), min(npts-1, gap[-1]+1)
            if ii<num_gap-1: idx2 = min(idx1+(idx1-idx0), idx1+max_gap_npts, gap_list[ii+1][0])
            else: idx2 = min(idx1+(idx1-idx0), idx1+max_gap_npts, npts-1)
            if idx1==idx2: continue
            if idx2==idx1+(idx1-idx0): data[idx0:idx1] = data[idx1:idx2]
            else:
                num_tile = int(np.ceil((idx1-idx0)/(idx2-idx1)))
                data[idx0:idx1] = np.tile(data[idx1:idx2], num_tile)[0:idx1-idx0]
        trace.data = data
    # filter
    stream.detrend('demean').detrend('linear').taper(max_percentage=0.05, max_length=self.taper_max_length_sec)
    freq_min, freq_max = freq_band
    nyquist = 0.5 * min(float(trace.stats.sampling_rate) for trace in stream)
    if freq_min and float(freq_min) >= nyquist:
        raise ValueError(
            "lower filter corner {} Hz is not below Nyquist {} Hz".format(
                freq_min, nyquist
            )
        )
    safe_freq_max = None
    if freq_max:
        safe_freq_max = min(float(freq_max), nyquist * 0.95)
        if safe_freq_max < float(freq_max):
            self._log(
                "adjust filter upper corner from {} to {:.6g} Hz for "
                "Nyquist {:.6g} Hz".format(freq_max, safe_freq_max, nyquist)
            )
    if freq_min and safe_freq_max:
        return stream.filter(
            'bandpass', freqmin=freq_min, freqmax=safe_freq_max
        )
    elif not freq_max and freq_min:
        return stream.filter('highpass', freq=freq_min)
    elif not freq_min and safe_freq_max:
        return stream.filter('lowpass', freq=safe_freq_max)
    else:
        self._log('filter type not supported!'); return []
