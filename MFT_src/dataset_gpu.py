""" Data i/o interface for MFT (GPU ver)
"""
import os, glob, time
import torch
from torch.utils.data import Dataset, DataLoader
import obspy
from obspy import read, UTCDateTime
import numpy as np
import config

# import config
cfg = config.Config()
get_data_dict = cfg.get_data_dict
num_workers = cfg.num_workers
samp_rate = cfg.samp_rate
freq_band = cfg.freq_band
taper_max_length_sec = float(cfg.taper_max_length_sec)
temp_win_det = cfg.temp_win_det
temp_win_p = cfg.temp_win_p
temp_win_s = cfg.temp_win_s
temp_win_npts = [int(sum(win)*samp_rate) for win in [temp_win_det, temp_win_p, temp_win_s]]
min_sta = cfg.min_sta
max_sta = cfg.max_sta


def buffered_data_paths(date, data_dir, buffer_seconds):
    data_dict = get_data_dict(date, data_dir)
    if buffer_seconds <= 0:
        return data_dict
    for day_offset in (-1, 1):
        nearby = get_data_dict(date + day_offset * 86400, data_dir)
        for net_sta in data_dict:
            data_dict[net_sta].extend(nearby.get(net_sta, []))
    return data_dict


def read_data(date, data_dir, sta_dict, buffer_seconds=0.0):
    """ Read data (continuous waveform)
    Input
      data_dict = {net_sta: stream_paths}
    Output
      data_dict = {net_sta: [[data_cuda, norm_data_cuda], [data, norm_data]]} 
    """
    t=time.time()
    print('reading continuous data')
    data_dict = buffered_data_paths(date, data_dir, buffer_seconds)
    to_del = [net_sta for net_sta in data_dict.keys() if net_sta not in sta_dict]
    for net_sta in to_del: data_dict.pop(net_sta)
    start_time = date - buffer_seconds
    end_time = date + 86400 + buffer_seconds
    data_dataset = Data(data_dict, sta_dict, start_time, end_time)
    data_loader = DataLoader(data_dataset, num_workers=num_workers, batch_size=None, pin_memory=True)
    todel = []
    for (net_sta, data_i) in data_loader:
        if len(data_i)==0: todel.append(net_sta); continue
        data_dict[net_sta] = [data_i[0]] + [cpu2cuda(di) for di in data_i]
        print('read {} | time {:.1f}s'.format(net_sta, time.time()-t))
    for net_sta in todel: data_dict.pop(net_sta)
    return data_dict


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
          temp = [temp_det, temp_p, temp_s]
          norm_temp = [norm_det, norm_p, norm_s]
          dt_list = [ttp, tts, dt_ot]
    """
    # 1. read phase file
    print('reading template phase file')
    temp_list = read_ftemp(temp_pha)
    # 2. read temp data
    print('reading templates')
    t=time.time()
    todel = []
    temp_dataset = Templates(temp_list, temp_root)
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
  def __init__(self, data_dict, sta_dict, start_time, end_time):
    self.data_dict = data_dict
    self.sta_list = sorted(list(data_dict.keys()))
    self.sta_dict = sta_dict
    self.start_time = start_time
    self.end_time = end_time

  def __getitem__(self, index):
    # read stream
    net_sta = self.sta_list[index]
    st_paths = self.data_dict[net_sta]
    gain = self.sta_dict[net_sta][3]
    stream = read_stream(
        st_paths, gain, start_time=self.start_time, end_time=self.end_time
    )
    if len(stream)!=3: return net_sta, []
    stream = preprocess(stream)
    if len(stream)!=3: return net_sta, []
    stream = trim_stream(stream, self.start_time, self.end_time)
    expected_npts = int(round((self.end_time-self.start_time)*samp_rate))
    data_np = st2np(stream)[:, 0:expected_npts]
    # calc norm data (for calc_cc)
    data_cum = [np.cumsum(di**2) for di in data_np]
    norm_data = np.array([np.sqrt(di[temp_win_npts[0]:] - di[:-temp_win_npts[0]]) for di in data_cum])
    return net_sta, [data_np.astype(np.float32), norm_data.astype(np.float32)]

  def __len__(self):
    return len(self.sta_list)


class Templates(Dataset):
  """ Dataset for reading templates
  """
  def __init__(self, temp_list, temp_root):
    self.temp_list = temp_list
    self.temp_root = temp_root

  def __getitem__(self, index):
    # read one template
    temp_name, temp_loc, pick_dict_picks = self.temp_list[index]
    temp_dir = os.path.join(self.temp_root, temp_name.split('_')[1])
    ot = temp_loc[0]
    # select by tp (epicentral distance)
    dtype = [('net_sta','O'),('tp','O')]
    pick_list = np.array([(net_sta, tp) for net_sta, [tp,_] in pick_dict_picks.items()], dtype=dtype)
    sta_list = list(np.sort(pick_list, order='tp')[0:max_sta]['net_sta'])
    # read data
    pick_dict_data = {}
    for net_sta, [tp,ts] in pick_dict_picks.items():
        if net_sta not in sta_list: continue
        # read template date
        st_paths = sorted(glob.glob(os.path.join(temp_dir, '%s.*'%net_sta)))
        if len(st_paths)!=3: continue
        st = read_stream(st_paths, None)
        if len(st)!=3: continue
        # cut template data
        temp_det = trim_stream(st, tp-temp_win_det[0], tp+temp_win_det[1])
        temp_p = trim_stream(st, tp-temp_win_p[0], tp+temp_win_p[1])
        temp_s = trim_stream(st, ts-temp_win_s[0], ts+temp_win_s[1])
        temp = [st2np(st_i).astype(np.float32) for st_i in [temp_det, temp_p, temp_s]]
        temp = [temp[i][:,0:temp_win_npts[i]] for i in range(3)]
        # calc norm
        norm_det = np.array([sum(tr**2)**0.5 for tr in temp[0]])
        norm_p = np.array([sum(tr**2)**0.5 for tr in temp[1]])
        norm_s = np.array([sum(tr**2)**0.5 for tr in temp[2]])
        norm_temp = [norm_det, norm_p, norm_s]
        # get time shift (dt)
        dt_list = [int(dt*samp_rate) for dt in [ot-tp+temp_win_det[0], tp-ot, ts-ot]]
        pick_dict_data[net_sta] = [temp, norm_temp, dt_list]
    return temp_name, temp_loc, pick_dict_data

  def __len__(self):
    return len(self.temp_list)


# read template phase file
def read_ftemp(ftemp):
    f=open(ftemp); lines=f.readlines(); f.close()
    temp_list = []
    for line in lines:
        codes = line.split(',')
        if len(codes[0])>=14:
            id_name = codes[0]
            ot = UTCDateTime(codes[1])
            lat, lon, dep, mag = [float(code) for code in codes[2:]]
            event_loc = [ot, lat, lon, dep, mag]
            temp_list.append([id_name, event_loc, {}])
        else:
            net_sta = codes[0]
            tp, ts = [UTCDateTime(code) for code in codes[1:3]]
            temp_list[-1][-1][net_sta] = [tp, ts]
    return temp_list

def preprocess(stream):
    # time alignment
    start_time = max([trace.stats.starttime for trace in stream])
    end_time = min([trace.stats.endtime for trace in stream])
    if start_time>end_time: print('bad data!'); return []
    st = stream.slice(start_time, end_time)
    st = st.detrend('demean').detrend('linear').taper(
        max_percentage=0.05, max_length=taper_max_length_sec
    )
    # resample data
    org_rate = st[0].stats.sampling_rate
    if org_rate!=samp_rate: st.resample(samp_rate)
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
    # read data
    read_kwargs = {}
    if start_time is not None: read_kwargs['starttime'] = start_time
    if end_time is not None: read_kwargs['endtime'] = end_time
    try:
        unique_paths = list(dict.fromkeys(st_paths))
        st = read(unique_paths[0], **read_kwargs)
        for path in unique_paths[1:]:
            st += read(path, **read_kwargs)
        st.merge(fill_value=0)
    except:
        print('bad data'); return []
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

def trim_stream(stream, start_time, end_time):
    return stream.copy().trim(start_time, end_time, pad=True, fill_value=0.)

def cpu2cuda(data):
    return data.float().cuda(non_blocking=True)

def st2np(stream):
    npts = min([len(trace) for trace in stream])
    return np.array([trace.data[0:npts] for trace in stream], dtype=np.float64)

def dtime2str(dtime):
    date = ''.join(str(dtime).split('T')[0].split('-'))
    time = ''.join(str(dtime).split('T')[1].split(':'))[0:9]
    return date + time
