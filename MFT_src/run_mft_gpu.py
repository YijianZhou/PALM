""" Run MFT (main function)
"""
import os, sys, glob
import argparse
import numpy as np
from obspy import UTCDateTime
import torch.multiprocessing as mp
import torch
from dataset_gpu import read_temp, read_data
from mft_lib_gpu import mft_det, cc_pick, write_ctlg, write_pha
import config
import warnings
warnings.filterwarnings("ignore")
mp.set_sharing_strategy('file_system')

if __name__ == '__main__':
  mp.set_start_method('spawn', force=True) # 'spawn' or 'forkserver'
  # args parser
  parser = argparse.ArgumentParser()
  parser.add_argument('--gpu_idx', type=int, default=0)
  parser.add_argument('--data_dir', type=str,
                      default='/data/Example_data')
  parser.add_argument('--time_range', type=str,
                      default='20170927-20170928')
  parser.add_argument('--sta_file', type=str,
                        default='input/example.sta')
  parser.add_argument('--temp_root', type=str,
                      default='output/Example_templates')
  parser.add_argument('--temp_pha', type=str,
                      default='output/example.temp')
  parser.add_argument('--out_ctlg', type=str,
                      default='output/example.ctlg')
  parser.add_argument('--out_pha', type=str,
                      default='output/example.pha')
  args = parser.parse_args()
  torch.cuda.set_device(int(args.gpu_idx)) 

  # MFT params
  cfg = config.Config()
  min_sta = cfg.min_sta
  data_buffer_sec = float(getattr(cfg, 'data_buffer_sec', 30.0))
  if data_buffer_sec < 0:
    raise ValueError('data_buffer_sec must be nonnegative')
  # i/o paths
  out_root = os.path.split(args.out_pha)[0]
  if not os.path.exists(out_root): os.makedirs(out_root)
  out_ctlg = open(args.out_ctlg,'w')
  out_pha = open(args.out_pha,'w')
  sta_dict = cfg.get_sta_dict(args.sta_file)
  # read templates
  temp_list = read_temp(args.temp_pha, args.temp_root)
  # get time range
  start_date, end_date = [UTCDateTime(date) for date in args.time_range.split('-')]
  print('run MFT (GPU version)')
  print('time range: {} to {}'.format(start_date.date, end_date.date))
  # for all days
  num_day = (end_date.date - start_date.date).days
  for day_idx in range(num_day):
    # read data
    torch.cuda.empty_cache()
    date = start_date + day_idx*86400
    print('-'*40)
    print('detecting %s'%date.date)
    data_start = date - data_buffer_sec
    data_dict = read_data(
        date, args.data_dir, sta_dict, buffer_seconds=data_buffer_sec
    )
    if len(data_dict)<min_sta: continue
    # for all templates
    for [temp_name, temp_loc, temp_pick_dict] in temp_list:
        # mft det
        print('template {}'.format(temp_name))
        dets = mft_det(temp_pick_dict, data_dict)
        # cc pick
        for [det_ot, det_cc] in dets:
            absolute_ot = data_start + det_ot
            if not date <= absolute_ot < date + 86400:
                continue
            picks = cc_pick(det_ot, temp_pick_dict, data_dict)
            det_ot = absolute_ot
            print('det_ot {}, det_cc {:.2f}'.format(det_ot, det_cc))
            for i in range(len(picks)):
                picks[i][1:3] = [data_start + t_rel for t_rel in picks[i][1:3]]
                print('{0[0]:<7} | dt_p {0[3]:<5.2f}s, dt_s {0[4]:<5.2f}s | cc_p {0[6]:.3f}, cc_s {0[7]:.3f}'.format(picks[i]))
            write_ctlg(det_ot, det_cc, temp_name, temp_loc, out_ctlg)
            write_pha(det_ot, det_cc, temp_name, temp_loc, picks, out_pha)
    del data_dict
  out_ctlg.close()
  out_pha.close()
