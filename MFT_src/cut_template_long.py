""" Cut template waveform for long-term data
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
import config
from dataset_gpu import read_ftemp, preprocess
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

def sac_ch_time(st):
    for tr in st:
        if not 'sac' in tr.stats: continue
        t0 = tr.stats.starttime
        tr.stats.sac.nzyear = t0.year
        tr.stats.sac.nzjday = t0.julday
        tr.stats.sac.nzhour = t0.hour
        tr.stats.sac.nzmin = t0.minute
        tr.stats.sac.nzsec = t0.second
        tr.stats.sac.nzmsec = int(t0.microsecond / 1e3)
    return st

def cut_event_window(stream_paths, tp, ts, out_paths):
    t0 = tp - win_len[0] - sum(win_len)/2
    t1 = t0 + sum(win_len)*2
    st  = read(stream_paths[0], starttime=t0, endtime=t1)
    st += read(stream_paths[1], starttime=t0, endtime=t1)
    st += read(stream_paths[2], starttime=t0, endtime=t1)
    if len(st)!=3: return False
    st = sac_ch_time(preprocess(st).slice(tp-win_len[0], tp+win_len[1]))
    if len(st)!=3: return False
    # select with P SNR
    if min_snr:
        data_p = st.slice(tp-win_sta_lta[0]-win_snr[0], tp+win_sta_lta[1]+win_snr[1])[2].data
        snr_p = calc_sta_lta(data_p**2, win_sta_lta_npts[0], win_sta_lta_npts[1])
        if np.amax(snr_p)<min_snr: return False
    for ii, tr in enumerate(st):
        tr.write(out_paths[ii], format='sac')
        tr = read(out_paths[ii])[0]
        tr.stats.sac.t0, tr.stats.sac.t1 = win_len[0], win_len[0]+(ts-tp)
        tr.write(out_paths[ii], format='sac')
    return True


class Cut_Templates(Dataset):
  """ Dataset for cutting templates
  """
  def __init__(self, temp_list):
    self.temp_list = temp_list
    self.data_dir = args.data_dir
    self.out_root = args.out_root

  def __getitem__(self, index):
    data_paths_i = []
    # get event info
    id_name, event_loc, pick_dict = self.temp_list[index]
    event_name = id_name.split('_')[1]
    ot, lat, lon, dep, mag = event_loc
    ot = UTCDateTime(ot)
    data_dict = get_data_dict(ot, self.data_dir)
    event_dir = os.path.join(self.out_root, event_name)
    if not os.path.exists(event_dir): os.makedirs(event_dir)
    # cut event
    for net_sta, [tp, ts] in pick_dict.items():
        if net_sta not in data_dict: continue
        data_paths = data_dict[net_sta]
        out_paths = [os.path.join(event_dir,'%s.%s'%(net_sta,ii)) for ii in range(3)]
        is_cut = cut_event_window(data_paths, tp, ts, out_paths)
        if not is_cut: continue
        data_paths_i.append(out_paths)
    return data_paths_i

  def __len__(self):
    return len(self.temp_list)


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
    # i/o files
    if not os.path.exists(args.out_root): os.makedirs(args.out_root)
    temp_list = read_ftemp(args.temp_pha)
    # for sta-date pairs
    data_paths  = []
    dataset = Cut_Templates(temp_list)
    dataloader = DataLoader(dataset, num_workers=num_workers, batch_size=None)
    for i, data_paths_i in enumerate(dataloader):
        data_paths += data_paths_i
        if i%10==0: print('%s/%s events done/total'%(i,len(dataset)))
    fout_data_paths = os.path.join(args.out_root,'data_paths.npy')
    np.save(fout_data_paths, data_paths)

