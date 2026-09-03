""" Make event.dat for grids
"""
import os
import numpy as np
from obspy import UTCDateTime
import config
import warnings
warnings.filterwarnings("ignore")

# i/o paths
cfg = config.Config()
fevent = 'input/event.dat'
ot_min, ot_max = [UTCDateTime(date) for date in cfg.time_range.split('-')]
lat_min, lat_max = cfg.lat_range
lon_min, lon_max = cfg.lon_range
num_grids = cfg.num_grids
xy_pad = cfg.xy_pad
dx = (lon_max - lon_min) / num_grids[0]
dy = (lat_max - lat_min) / num_grids[1]
# phase file for each grid
evid_lists, fouts = [], []
for i in range(num_grids[0]):
  evid_lists.append([])
  for j in range(num_grids[1]):
    evid_lists[i].append([])
    fouts.append(open('input/event_%s-%s.dat'%(i,j),'w'))

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

f=open(fevent); lines=f.readlines(); f.close()
for line in lines:
    codes = line.split()
    ot = UTCDateTime(codes[0]+codes[1][0:6])
    lat, lon = [float(code) for code in codes[2:4]]
    evid = int(codes[-1])
    evid_idx, fout_idx = get_fout_idx(lat, lon)
    if len(evid_idx)!=0: evid_lists[evid_idx[0]][evid_idx[1]].append(evid)
    if len(fout_idx)==0: continue
    if not ot_min<ot<ot_max: continue
    for idx in fout_idx: fouts[idx].write(line)

np.save('input/evid_lists.npy', np.array(evid_lists, dtype=object))
for fout in fouts: fout.close()
