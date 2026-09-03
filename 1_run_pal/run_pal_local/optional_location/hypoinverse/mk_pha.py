""" Make input phase file for hypoInverse (COP 3 format)
"""
import numpy as np
from obspy import UTCDateTime
import config

# i/o paths
cfg = config.Config()
fout = open('input/phase.dat','w')
lat_code = cfg.lat_code
lon_code = cfg.lon_code
p_wht = cfg.p_wht
s_wht = cfg.s_wht

def split_datetime(dtime):
    date = '{:0>4}{:0>2}{:0>2}'.format(dtime.year, dtime.month, dtime.day)
    time = '{:0>2}{:0>2}{:0>2}{:0>2}'.format(dtime.hour, dtime.minute, dtime.second, int(dtime.microsecond/1e4))
    return date, time

evid = 0
f=open(cfg.fpha); lines=f.readlines(); f.close()
for i,line in enumerate(lines):
  codes = line.split(',')
  if len(codes[0])>10:
    # write head line
    ot, lat, lon = codes[0:3]
    ot = UTCDateTime(ot)
    date, time = split_datetime(ot)
    mag = 0  # output mag is directly passed from input
    lat = abs(float(lat))
    lon = abs(float(lon))
    lon_deg = int(lon)
    lon_min = int(100*60*(lon-int(lon)))
    lat_deg = int(lat)
    lat_min = int(100*60*(lat-int(lat)))
    lat = '{:0>2}{}{:0>4}'.format(lat_deg, lat_code, lat_min)
    lon = '{:0>3}{}{:0>4}'.format(lon_deg, lon_code, lon_min)
    if i!=0: fout.write('\n')
    fout.write('{}{}{} {}L{:3.2f}{}{:>10}L\n'\
        .format(date+time, lat, lon, ' '*90, mag, ' '*9, evid))
    evid += 1
  else:
    # write sta line
    net_sta, tp, ts = codes[0:3]
    net, sta = net_sta.split('.')
    tp = UTCDateTime(tp) if tp!='-1' else -1
    ts = UTCDateTime(ts) if ts!='-1' else -1
    date = split_datetime(tp)[0] if tp!=-1 else split_datetime(ts)[0]
    hhmm = split_datetime(tp)[1][0:4] if tp!=-1 else split_datetime(ts)[1][0:4]
    tp_sec = split_datetime(tp)[1][4:] if tp!=-1 else ' '*4
    ts_sec = int(100*(ts - UTCDateTime(date + hhmm))) if ts!=-1 else ' '*4
    tp_code = 'IP {}{} {}'.format(p_wht, date+hhmm, tp_sec)
    ts_code = '{:4}ES {}'.format(ts_sec, s_wht)
    fout.write('{:<5}{}  HHZ {}{} {} \n'.format(sta, net, tp_code,' '*7, ts_code))
fout.write('\n')
fout.close()
