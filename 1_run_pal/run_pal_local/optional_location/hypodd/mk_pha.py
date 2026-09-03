""" Make input phase file for ph2dt
"""
import os
import numpy as np
from obspy import UTCDateTime
import config
import warnings
warnings.filterwarnings("ignore")

# i/o paths
cfg = config.Config()
dep_corr = cfg.dep_corr
ot_min, ot_max = [UTCDateTime(date) for date in cfg.ot_range.split('-')]
lat_min, lat_max = cfg.lat_range
lon_min, lon_max = cfg.lon_range
num_grids = cfg.num_grids
xy_pad = cfg.xy_pad
# phase file for each grid
fouts, evid_lists = [], []
for i in range(num_grids[0]):
  evid_lists.append([])
  for j in range(num_grids[1]):
    evid_lists[i].append([])
    fouts.append(open('input/phase_%s-%s.dat'%(i,j),'w'))
# lat-lon range for each grid
dx = (lon_max - lon_min) / num_grids[0]
dy = (lat_max - lat_min) / num_grids[1]

def get_fout_idx(lat, lon):
    evid_idx, fout_idx = [], []
    for i in range(num_grids[0]):
      for j in range(num_grids[1]):
        # which phase files to write
        if lon_min+i*dx-xy_pad[0]<lon<=lon_min+(i+1)*dx+xy_pad[0] \
        and lat_min+j*dy-xy_pad[1]<lat<=lat_min+(j+1)*dy+xy_pad[1]: 
            fout_idx.append(i*num_grids[1]+j)
        # belong to which grid
        if lon_min+i*dx<lon<=lon_min+(i+1)*dx \
        and lat_min+j*dy<lat<=lat_min+(j+1)*dy:
            evid_idx = [i,j]
    return evid_idx, fout_idx

f=open(cfg.fpha); lines=f.readlines(); f.close()
for line in lines:
  codes = line.split(',')
  if len(codes[0])>=14:
    # write head line
    ot = UTCDateTime(codes[0])
    lat, lon, dep, mag = [float(code) for code in codes[1:5]]
    dep += dep_corr
    evid = int(codes[-1])
    evid_idx, fout_idx = get_fout_idx(lat, lon)
    if len(evid_idx)!=0: evid_lists[evid_idx[0]][evid_idx[1]].append(evid)
    if len(fout_idx)==0: continue
    if not ot_min<ot<ot_max: continue
    # format time info
    date = '{:4} {:2} {:2}'.format(ot.year, ot.month, ot.day)
    time = '{:2} {:2} {:5.2f}'.format(ot.hour, ot.minute, ot.second + ot.microsecond/1e6)
    # format loc info
    loc = '{:7.4f} {:9.4f}  {:6.2f} {:4.2f}'.format(lat, lon, dep, mag)
    for idx in fout_idx: fouts[idx].write('# {} {}  {}  0.00  0.00  0.00  {:>9}\n'.format(date, time, loc, evid))
  else:
    if len(fout_idx)==0: continue
    if not ot_min<ot<ot_max: continue
    # write sta pick lines
    sta = codes[0].split('.')[1]
    wp, ws = 1., 1.
    if codes[1]!='-1': 
        tp = UTCDateTime(codes[1])
        ttp = tp - ot
        for idx in fout_idx: fouts[idx].write('{:<5}{}{:6.3f}  {:6.3f}   P\n'.format(sta, ' '*6, ttp, wp))
    if codes[2]!='-1':
        ts = UTCDateTime(codes[2])
        tts = ts - ot
        for idx in fout_idx: fouts[idx].write('{:<5}{}{:6.3f}  {:6.3f}   S\n'.format(sta, ' '*6, tts, ws))

np.save('input/evid_lists.npy', np.array(evid_lists, dtype=object))
for fout in fouts: fout.close()
