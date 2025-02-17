""" Data Pipeline: interface for data i/o
"""
import os, glob
import numpy as np
import obspy
from obspy import read, UTCDateTime

# get data path dict
def get_data_dict(date, data_dir):
    # get data paths
    data_dict = {}
    date_code = '{:0>4}{:0>2}{:0>2}'.format(date.year, date.month, date.day)
    st_paths = sorted(glob.glob(os.path.join(data_dir, date_code, '*')))
    for st_path in st_paths:
        fname = os.path.basename(st_path)
        net_sta = '.'.join(fname.split('.')[0:2])
        if net_sta in data_dict: data_dict[net_sta].append(st_path)
        else: data_dict[net_sta] = [st_path]
    # drop bad sta
    todel = [net_sta for net_sta in data_dict if len(data_dict[net_sta])!=3]
    for net_sta in todel: data_dict.pop(net_sta)
    return data_dict

# if your data contains single-channel stations
def get_1chn_data(date, data_dir):
    # get data paths
    data_dict = {}
    date_code = '{:0>4}{:0>2}{:0>2}'.format(date.year, date.month, date.day)
    st_paths = sorted(glob.glob(os.path.join(data_dir, date_code, '*')))
    for st_path in st_paths:
        fname = os.path.basename(st_path)
        net_sta = '.'.join(fname.split('.')[0:2])
        if net_sta in data_dict: data_dict[net_sta].append(st_path)
        else: data_dict[net_sta] = [st_path]
    # expand single channel station
    for net_sta in data_dict:
        if len(data_dict[net_sta])<3: data_dict[net_sta] = [data_dict[net_sta][-1]]*3
    return data_dict

# read stream data
def read_data(st_paths, sta_dict):
    # read data
    print('reading stream: {}'.format(st_paths[0]))
    try:
        st  = read(st_paths[0])
        st += read(st_paths[1])
        st += read(st_paths[2])
    except: 
        print('bad data!'); return []
    # change header
    net, sta = os.path.basename(st_paths[0]).split('.')[0:2]
    net_sta = '%s.%s'%(net,sta)
    gain = sta_dict[net_sta][3]
    start_time = max([tr.stats.starttime for tr in st])
    end_time = min([tr.stats.endtime for tr in st])
    st_time = start_time + (end_time-start_time)/2
    for ii in range(3): st[ii].stats.network, st[ii].stats.station = net, sta
    # if format 1: same gain for 3-chn & time invariant
    if type(gain)==float:
        for ii in range(3): st[ii].data = st[ii].data / gain
    # if format 2: different gain for 3-chn & time invariant
    elif type(gain[0])==float:
        for ii in range(3): st[ii].data = st[ii].data / gain[ii]
    # if format 3: different gain for 3-chn & time variant
    elif type(gain[0])==list:
        for [ge,gn,gz,t0,t1] in gain:
            if t0<st_time<t1: break
        for ii in range(3): st[ii].data = st[ii].data / [ge,gn,gz][ii]
    return st

# get station loc & gain dict
def get_sta_dict(sta_file):
    sta_dict = {}
    f=open(sta_file); lines=f.readlines(); f.close()
    for line in lines:
        codes = line.split(',')
        net_sta = codes[0]
        lat, lon, ele = [float(code) for code in codes[1:4]]
        # format 1: same gain for 3-chn & time invariant
        if len(codes[4:])==1: gain = float(codes[4])
        # format 2: different gain for 3-chn & time invariant
        elif len(codes[4:])==3: gain = [float(code) for code in codes[4:]]
        # format 3: different gain for 3-chn & time variant
        elif len(codes[4:])==5: 
            gain = [float(code) for code in codes[4:7]]
            gain += [UTCDateTime(code) for code in codes[7:9]]
            gain = [gain]
        else: print('false sta_file format!'); continue
        if net_sta not in sta_dict: 
            sta_dict[net_sta] = [lat,lon,ele,gain]
        else: 
            sta_dict[net_sta][-1].append(gain[0]) # if format 3
    return sta_dict

# get PAL picks (for assoc)
def get_pal_picks(date, pick_dir):
    picks = []
    dtype = [('net_sta','O'),
             ('sta_ot','O'),
             ('tp','O'),
             ('ts','O'),
             ('s_amp','O')]
    fname = str(date.date) + '.pick'
    pick_path = os.path.join(pick_dir, fname)
    if not os.path.exists(pick_path): return np.array([], dtype=dtype)
    f=open(pick_path); lines=f.readlines(); f.close()
    for line in lines:
        codes = line.split(',')
        net_sta = codes[0]
        sta_ot, tp, ts = [UTCDateTime(code) for code in codes[1:4]]
        s_amp = float(codes[4])
        picks.append((net_sta, sta_ot, tp, ts, s_amp))
    return np.array(picks, dtype=dtype)

# get picks (for assoc)
def get_picks(date, pick_dir):
    picks = []
    dtype = [('net_sta','O'),
             ('sta_ot','O'),
             ('tp','O'),
             ('ts','O'),
             ('s_amp','O')]
    fname = str(date.date) + '.pick'
    pick_path = os.path.join(pick_dir, fname)
    f=open(pick_path); lines=f.readlines(); f.close()
    for line in lines:
        codes = line.split(',')
        net_sta = codes[0]
        tp, ts = [UTCDateTime(code) for code in codes[1:3]]
        sta_ot = calc_ot(tp, ts)
        s_amp = float(codes[3])
        picks.append((net_sta, sta_ot, tp, ts, s_amp))
    return np.array(picks, dtype=dtype)

def calc_ot(tp, ts):
    vp, vs = 6., 3.45
    dist = (ts-tp) / (1/vs - 1/vp)
    tt_p = dist / vp
    return tp - tt_p

""" customized data_pipelines
"""
