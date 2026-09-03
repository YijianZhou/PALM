""" Cut template waveform for intense sequence
  Inputs
    data_dir: dir of continuous data
    temp_pha: template phase file
    out_root: root dir for template data
  Outputs
    temp_root/temp_name/net.sta.chn
    Note: temp_name == ot (yyyymmddhhmmss.ss)
"""
import os, glob, shutil
import argparse
import numpy as np
import torch.multiprocessing as mp
from torch.utils.data import Dataset, DataLoader
from obspy import read, UTCDateTime
from dataset_gpu import read_ftemp, preprocess
import config
import warnings
warnings.filterwarnings("ignore")

# cut params
cfg = config.Config()
num_workers = cfg.num_workers
win_len = cfg.win_len 
win_snr = cfg.win_snr
win_sta_lta = cfg.win_sta_lta
win_sta_lta_npts = [int(win*cfg.samp_rate) for win in win_sta_lta]
min_snr = cfg.min_snr
get_data_dict = cfg.get_data_dict


def get_sta_date(event_list):
    sta_date_dict = {}
    for i, [id_name, event_loc, pick_dict] in enumerate(event_list):
        if i%1e3==0: print('%s/%s events done/total'%(i, len(event_list)))
        # 1. get event info
        event_name = id_name.split('_')[1]
        event_dir = os.path.join(args.out_root, event_name)
        ot = event_loc[0]
        if not os.path.exists(event_dir): os.makedirs(event_dir)
        for net_sta, [tp, ts] in pick_dict.items():
            date = str(tp.date)
            sta_date = '%s_%s'%(net_sta, date) # for one day's stream data
            if sta_date not in sta_date_dict:
                sta_date_dict[sta_date] = [[event_dir, tp, ts]]
            else: sta_date_dict[sta_date].append([event_dir, tp, ts])
    return sta_date_dict

# slice sac file with obspy
def obspy_slice(stream, t0, t1):
    st = stream.slice(t0, t1)
    for tr in st:
        if not 'sac' in tr.stats: continue
        tr.stats.sac.nzyear = t0.year
        tr.stats.sac.nzjday = t0.julday
        tr.stats.sac.nzhour = t0.hour
        tr.stats.sac.nzmin = t0.minute
        tr.stats.sac.nzsec = t0.second
        tr.stats.sac.nzmsec = int(t0.microsecond / 1e3)
    return st

def calc_sta_lta(data, win_lta_npts, win_sta_npts):
    npts = len(data)
    if npts < win_lta_npts + win_sta_npts:
        print('input data too short!')
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


class Cut_Templates(Dataset):
  """ Dataset for cutting templates
  """
  def __init__(self, sta_date_items):
    self.sta_date_items = sta_date_items
    self.data_dir = args.data_dir

  def __getitem__(self, index):
    data_paths_i = []
    # get one sta-date
    sta_date, samples = self.sta_date_items[index]
    net_sta, date = sta_date.split('_')
    net, sta = net_sta.split('.')
    date = UTCDateTime(date)
    # read & prep one day's data
    print('reading %s %s'%(net_sta, date.date))
    data_dict = get_data_dict(date, self.data_dir)
    if net_sta not in data_dict: return data_paths_i
    st_paths = data_dict[net_sta]
    try:
        stream  = read(st_paths[0])
        stream += read(st_paths[1])
        stream += read(st_paths[2])
    except: return data_paths_i
    stream = preprocess(stream)
    if len(stream)!=3: return data_paths_i
    for [event_dir, tp, ts] in samples:
        # time shift & prep
        start_time = tp - win_len[0]
        end_time = tp + win_len[1]
        st = obspy_slice(stream, start_time, end_time)
        if len(st)!=3: continue
        st = st.detrend('demean')  # note: no detrend here
        # select with P SNR
        if min_snr:
            data_p = st.slice(tp-win_sta_lta[0]-win_snr[0], tp+win_sta_lta[1]+win_snr[1])[2].data
            snr_p = calc_sta_lta(data_p**2, win_sta_lta_npts[0], win_sta_lta_npts[1])
            if np.amax(snr_p)<min_snr: continue
        # write & record out_paths
        data_paths_i.append([])
        for ii,tr in enumerate(st):
            out_path = os.path.join(event_dir,'%s.%s'%(net_sta,ii))
            if not 'sac' in tr.stats:
                tr.write(out_path, format='sac')
                tr = read(out_path)[0]
            tr.stats.sac.t0, tr.stats.sac.t1 = tp-start_time, ts-start_time
            tr.write(out_path, format='sac')
            data_paths_i[-1].append(out_path)
    return data_paths_i

  def __len__(self):
    return len(self.sta_date_items)


if __name__ == '__main__':
    mp.set_start_method('spawn', force=True) # 'spawn' or 'forkserver'
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_dir', type=str,
                        default='/data/Example_data')
    parser.add_argument('--temp_pha', type=str,
                        default='input/example.temp')
    parser.add_argument('--out_root', type=str,
                        default='output/example_templates')
    args = parser.parse_args()
    # read fpha
    temp_list = read_ftemp(args.temp_pha)
    sta_date_dict = get_sta_date(temp_list)
    sta_date_items = list(sta_date_dict.items())
    # for sta-date pairs
    data_paths  = []
    dataset = Cut_Templates(sta_date_items)
    dataloader = DataLoader(dataset, num_workers=num_workers, batch_size=None)
    for i, data_paths_i in enumerate(dataloader): 
        data_paths += data_paths_i
        if i%10==0: print('%s/%s sta-date pairs done/total'%(i+1,len(dataset)))
    fout_data_paths = os.path.join(args.out_root,'data_paths.npy')
    np.save(fout_data_paths, data_paths)

