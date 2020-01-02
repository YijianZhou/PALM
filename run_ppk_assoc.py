""" Run picker and associator
    raw waveforms --> picks --> events
"""
import os, glob
import argparse
import numpy as np
import obspy
from obspy import read, UTCDateTime
import config

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_dir', type=str,
                        default='/data2/ZSY_SAC/*/KMI')
    parser.add_argument('--time_range', type=str,
                        default='20170224,20170225')
    parser.add_argument('--out_ctlg', type=str,
                        default='./output/catalog.tmp')
    parser.add_argument('--out_pha', type=str,
                        default='./output/phase.tmp')
    parser.add_argument('--out_ppk_dir', type=str,
                        default='./output/picks')
    args = parser.parse_args()


# define func
cfg = config.Config()
get_data = cfg.get_data
num_proc = cfg.num_proc
picker = cfg.picker
associator = cfg.associator

# i/o paths
out_root = os.path.split(args.out_pha)[0]
if not os.path.exists(out_root): os.makedirs(out_root)
out_ctlg = open(args.out_ctlg,'w')
out_pha = open(args.out_pha,'w')
if not os.path.exists(args.out_ppk_dir): os.makedirs(args.out_ppk_dir)

# get time range
start_date, end_date = [UTCDateTime(date) for date in args.time_range.split(',')]
print('run ppk & assoc: raw_waveform --> picks --> events')
print('time range: {} to {}'.format(start_date, end_date))

# for all days
num_day = (end_date.date - start_date.date).days
for day_idx in range(num_day):

    # get data
    date = start_date + day_idx*86400
    data_dict = get_data(args.data_dir, date, num_proc)
    if data_dict=={}: continue

    # 1. phase picking: waveform --> picks
    fpath = os.path.join(args.out_ppk_dir, str(date.date)+'.ppk')
    out_ppk = open(fpath,'w')
    for i,net_sta in enumerate(data_dict):
        stream = data_dict[net_sta]
        picksi = picker.pick(stream, out_ppk)
        if i==0: picks = picksi
        else:    picks = np.append(picks, picksi)
    out_ppk.close()

    # 2. associate picks: picks --> event_picks & event_loc
    associator.associate(picks, out_ctlg, out_pha)

# finish making catalog
out_pha.close()
out_ctlg.close()
