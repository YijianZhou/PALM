""" Data i/o interface for MFT (CPU ver)
"""
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import torch
from torch.utils.data import Dataset, DataLoader
import obspy
from obspy import read
import numpy as np
from scipy.signal import resample_poly
import config
from template_store import TemplateDataset, read_ftemp
from rolling_waveform import merge_cached_tail, raw_tail

# import config
cfg = config.Config()
get_data_dict = cfg.get_data_dict
num_workers = cfg.num_workers
samp_rate = cfg.samp_rate
phase_samp_rate = cfg.phase_samp_rate
if phase_samp_rate < samp_rate:
    raise ValueError("phase_samp_rate must be at least samp_rate")
freq_band = cfg.freq_band
taper_max_length_sec = float(cfg.taper_max_length_sec)
temp_win_det = cfg.temp_win_det
temp_win_p = cfg.temp_win_p
temp_win_s = cfg.temp_win_s
temp_det_npts = int(sum(temp_win_det) * samp_rate)
min_sta = cfg.min_sta
max_sta = cfg.max_sta


def load_raw_tail_cache(date, data_dir, sta_dict, buffer_seconds=0.0):
    """Read only the final raw tail needed to seed a sequential run."""
    if buffer_seconds <= 0:
        return {}
    day_end = date + 86400
    paths_by_station = get_data_dict(date, data_dir)
    items = [
        (net_sta, paths)
        for net_sta, paths in sorted(paths_by_station.items())
        if net_sta in sta_dict
    ]

    def load(item):
        net_sta, paths = item
        stream = read_stream(
            paths,
            sta_dict[net_sta][3],
            start_time=day_end - 2.0 * buffer_seconds,
            end_time=day_end,
        )
        return net_sta, raw_tail(stream, day_end, buffer_seconds)

    if num_workers > 1 and len(items) > 1:
        with ThreadPoolExecutor(
            max_workers=min(num_workers, len(items))
        ) as executor:
            results = executor.map(load, items)
            return {net_sta: tail for net_sta, tail in results if tail}
    return {net_sta: tail for net_sta, tail in map(load, items) if tail}


def read_data(
    date, data_dir, sta_dict, buffer_seconds=0.0,
    previous_raw_tails=None,
):
    """ Read data (continuous waveform)
    Input
      data_dict = {net_sta: stream_paths}
    Output
      data_dict = {net_sta: [detection_data, detection_norm, phase_data]}
      next_raw_tails = {net_sta: unfiltered final 2*buffer stream}
    """
    t=time.time()
    print('reading continuous data')
    data_dict = get_data_dict(date, data_dir)
    to_del = [net_sta for net_sta in data_dict.keys() if net_sta not in sta_dict]
    for net_sta in to_del: data_dict.pop(net_sta)
    start_time = date - 2.0 * buffer_seconds
    end_time = date + 86400
    data_dataset = Data(
        data_dict, sta_dict, date, start_time, end_time,
        buffer_seconds, previous_raw_tails or {},
    )
    if num_workers > 1 and len(data_dataset) > 1:
        executor = ThreadPoolExecutor(
            max_workers=min(num_workers, len(data_dataset))
        )
        results = executor.map(data_dataset.__getitem__, range(len(data_dataset)))
    else:
        executor = None
        results = map(data_dataset.__getitem__, range(len(data_dataset)))
    todel = []
    next_raw_tails = {}
    try:
        for net_sta, data_i, next_tail in results:
            if next_tail:
                next_raw_tails[net_sta] = next_tail
            if len(data_i)==0:
                todel.append(net_sta)
                continue
            data_dict[net_sta] = [torch.from_numpy(value) for value in data_i]
            print('read {} | time {:.1f}s'.format(net_sta, time.time()-t))
    finally:
        if executor is not None:
            executor.shutdown()
    for net_sta in todel: data_dict.pop(net_sta)
    return data_dict, next_raw_tails


def read_temp(temp_pha, temp_root):
    """ Read templates
    Input
      temp_pha (txt): template phase file
        event line: ot, lat, lon, dep, mag
        phase line: net.sta, tp, ts, s_amp, p_snr, s_snr
      temp_root: root dir for template data
        temp_root/temp_name/net.sta.chn
        *note: temp_name == ot in yyyymmddhhmmss.ss
    Output
      temp_list = [temp_name, temp_loc, temp_pick_dict]
      , where temp_pick_dict[net_sta] = [temp, norm_temp, dt_list]
          temp = [temp_det, temp_p, temp_s, temp_det_phase]
          norm_temp = [norm_det, norm_p, norm_s, norm_det_phase]
          dt_list = [detection-origin offset, P offset, S offset]
          Detection values use samp_rate; P/S values use phase_samp_rate.
    """
    # 1. read phase file
    print('reading template phase file')
    temp_list = read_ftemp(temp_pha)
    # 2. read temp data
    print('reading templates')
    t=time.time()
    todel = []
    temp_dataset = TemplateDataset(
        temp_list, temp_root, max_sta, samp_rate, phase_samp_rate,
        temp_win_det, temp_win_p, temp_win_s, freq_band,
    )
    temp_loader = DataLoader(temp_dataset, num_workers=num_workers, batch_size=None, pin_memory=True)
    for i, [temp_name, temp_loc, temp_pick_dict] in enumerate(temp_loader):
        if len(temp_pick_dict)<min_sta: todel.append(i)
        temp_list[i] = [temp_name, temp_loc, temp_pick_dict]
        if i%100==0: print('{}th template | time {:.1f}s'.format(i, time.time()-t))
    temp_list = [temp_list[i] for i in range(len(temp_list)) if i not in todel]
    return temp_list


class Data(Dataset):
  """ Dataset for reading data (continuous waveform)
  """
  def __init__(
      self, data_dict, sta_dict, day_start, start_time, end_time,
      buffer_seconds, previous_raw_tails,
  ):
    self.data_dict = data_dict
    self.sta_list = sorted(list(data_dict.keys()))
    self.sta_dict = sta_dict
    self.day_start = day_start
    self.start_time = start_time
    self.end_time = end_time
    self.buffer_seconds = buffer_seconds
    self.previous_raw_tails = previous_raw_tails

  def __getitem__(self, index):
    # read stream
    net_sta = self.sta_list[index]
    st_paths = self.data_dict[net_sta]
    gain = self.sta_dict[net_sta][3]
    stream = read_stream(
        st_paths, gain, start_time=self.day_start, end_time=self.end_time
    )
    if len(stream)!=3: return net_sta, [], None
    next_tail = raw_tail(stream, self.end_time, self.buffer_seconds)
    stream = merge_cached_tail(
        stream,
        self.previous_raw_tails.get(net_sta),
        self.start_time,
        self.end_time,
    )
    if len(stream)!=3: return net_sta, [], next_tail
    phase_stream = preprocess(stream, phase_samp_rate)
    if len(phase_stream)!=3: return net_sta, [], next_tail
    phase_stream = trim_stream(
        phase_stream, self.start_time, self.end_time,
        allow_left_padding=net_sta not in self.previous_raw_tails,
    )
    if len(phase_stream) != 3: return net_sta, [], next_tail
    detection_stream = resample_stream(phase_stream, samp_rate)
    duration = self.end_time - self.start_time
    phase_npts = int(round(duration * phase_samp_rate))
    detection_npts = int(round(duration * samp_rate))
    phase_data = st2np(phase_stream)[:, :phase_npts]
    detection_data = st2np(detection_stream)[:, :detection_npts]
    # calc norm data (for calc_cc)
    data_cum = [
        np.concatenate(([0.0], np.cumsum(di ** 2)))
        for di in detection_data
    ]
    norm_data = np.array([
        np.sqrt(di[temp_det_npts:] - di[:-temp_det_npts])
        for di in data_cum
    ])
    return net_sta, [
        detection_data.astype(np.float32),
        norm_data.astype(np.float32),
        phase_data.astype(np.float32),
    ], next_tail

  def __len__(self):
    return len(self.sta_list)


def preprocess(stream, target_sample_rate=None):
    if target_sample_rate is None:
        target_sample_rate = samp_rate
    # time alignment
    start_time = max([trace.stats.starttime for trace in stream])
    end_time = min([trace.stats.endtime for trace in stream])
    if start_time>end_time: print('bad data!'); return []
    st = stream.slice(start_time, end_time)
    st = st.detrend('demean').detrend('linear').taper(
        max_percentage=0.05, max_length=taper_max_length_sec
    )
    # resample data
    minimum_rate = min(trace.stats.sampling_rate for trace in st)
    if minimum_rate < target_sample_rate * (1.0 - 1e-6):
        print('data rate is below requested {} Hz'.format(target_sample_rate))
        return []
    st = resample_stream(st, target_sample_rate)
    for ii in range(3):
        st[ii].data[np.isnan(st[ii].data)] = 0
        st[ii].data[np.isinf(st[ii].data)] = 0
    # filter
    freq_min, freq_max = freq_band
    if freq_min and freq_max:
        return st.filter('bandpass', freqmin=freq_min, freqmax=freq_max)
    elif not freq_max and freq_min:
        return st.filter('highpass', freq=freq_min)
    elif not freq_min and freq_max:
        return st.filter('lowpass', freq=freq_max)
    else:
        print('filter type not supported!'); return []

def resample_stream(stream, target_sample_rate):
    st = stream.copy()
    for trace in st:
        source_rate = float(trace.stats.sampling_rate)
        if not np.isclose(source_rate, target_sample_rate):
            ratio = Fraction(float(target_sample_rate) / source_rate).limit_denominator(10000)
            effective_rate = source_rate * ratio.numerator / ratio.denominator
            if not np.isclose(effective_rate, target_sample_rate, rtol=1e-8):
                raise ValueError(
                    'cannot represent sample-rate conversion {} -> {}'.format(
                        source_rate, target_sample_rate
                    )
                )
            trace.data = resample_poly(
                np.asarray(trace.data), ratio.numerator, ratio.denominator,
                padtype='line',
            )
            trace.stats.sampling_rate = float(target_sample_rate)
        trace.data[~np.isfinite(trace.data)] = 0
    return st

def normalize_stream_channels(stream):
    stream.sort(keys=["channel"])
    traces = list(stream)
    if not traces:
        return stream
    normalized = obspy.Stream(
        traces=[traces[index % len(traces)].copy() for index in range(3)]
    )
    return normalized


def read_stream(st_paths, gain=None, start_time=None, end_time=None):
    # Daily files are already cleaned. Only stitch adjacent published days.
    read_kwargs = {}
    if start_time is not None: read_kwargs['starttime'] = start_time
    if end_time is not None: read_kwargs['endtime'] = end_time
    try:
        unique_paths = list(dict.fromkeys(st_paths))
        st = obspy.Stream()
        for path in unique_paths:
            daily = read(path, **read_kwargs)
            if len(daily) != 1:
                raise ValueError('expected one cleaned trace in {}'.format(path))
            st += daily
        if len(st) > 3:
            st.merge(method=0, fill_value=None)
        if any(
            np.ma.isMaskedArray(trace.data)
            and np.any(np.ma.getmaskarray(trace.data))
            for trace in st
        ):
            raise ValueError('gap or conflicting overlap across daily files')
    except Exception as exc:
        print('bad archived data: {}'.format(exc)); return []
    if len(st) != 3:
        st = normalize_stream_channels(st)
    if not gain: return st
    # remove gain
    start_time = max([tr.stats.starttime for tr in st])
    end_time = min([tr.stats.endtime for tr in st])
    st_time = start_time + (end_time-start_time)/2
    # if format 1: same gain for 3-chn & time invariant
    if type(gain)==float:
        for ii in range(3): st[ii].data = st[ii].data / gain
    # if format 2: different gain for 3-chn & time invariant
    elif type(gain[0])==float:
        for ii in range(3): st[ii].data = st[ii].data / gain[ii]
    # format 3: different gain for 3-chn & time variant
    elif type(gain[0])==list:
        for [ge,gn,gz,t0,t1] in gain:
            if t0<st_time<t1: break
        for ii in range(3): st[ii].data = st[ii].data / [ge,gn,gz][ii]
    return st

def trim_stream(stream, start_time, end_time, allow_left_padding=False):
    tolerance = 0.5 / min(trace.stats.sampling_rate for trace in stream)
    if any(
        (not allow_left_padding and trace.stats.starttime > start_time + tolerance)
        or trace.stats.endtime < end_time - tolerance
        for trace in stream
    ):
        return []
    return stream.copy().trim(start_time, end_time, pad=True, fill_value=0.)

def st2np(stream):
    npts = min([len(trace) for trace in stream])
    return np.array([trace.data[0:npts] for trace in stream], dtype=np.float64)

def dtime2str(dtime):
    date = ''.join(str(dtime).split('T')[0].split('-'))
    time = ''.join(str(dtime).split('T')[1].split(':'))[0:9]
    return date + time
