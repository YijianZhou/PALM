""" Format hypoInverse output: sum file to csv files
"""
import glob, os
import numpy as np
import config

# i/o paths
cfg = config.Config()
ctlg_code = cfg.ctlg_code
grd_ele = cfg.grd_ele # typical station elevation
lat_code = cfg.lat_code
lon_code = cfg.lon_code
out_ctlg = open(cfg.out_ctlg,'w')
out_sum = open(cfg.out_sum,'w')
out_bad = open(cfg.out_bad,'w')
out_good = open(cfg.out_good,'w')
out_pha = open(cfg.out_pha,'w')
out_pha_full = open(cfg.out_pha_full,'w')


def write_csv(fout, line, evid, is_full=False):
    codes = line.split()
    date, hrmn, sec = codes[0:3]
    dtime = date + hrmn + sec.zfill(5)
    lat_deg = float(line[20:22])
    lat_min = float(line[23:28])
    lat = lat_deg + lat_min/60 if lat_code=='N' else -lat_deg - lat_min/60
    lon_deg = float(line[29:32])
    lon_min = float(line[33:38])
    lon = lon_deg + lon_min/60 if lon_code=='E' else -lon_deg - lon_min/60
    dep = float(line[38:44])
    mag = mag_dict[evid]
    if is_full: fout.write('{},{:.4f},{:.4f},{:.1f},{:.2f},{}\n'.format(dtime, lat, lon, dep+grd_ele, mag, evid))
    else: fout.write('{},{:.4f},{:.4f},{:.1f},{:.2f}\n'.format(dtime, lat, lon, dep+grd_ele, mag))

# read sum files
sum_dict = {}
for fsum in glob.glob('output/%s-*.sum'%ctlg_code):
    f=open(fsum); sum_lines=f.readlines(); f.close()
    for sum_line in sum_lines:
        evid = sum_line.split()[-1]
        if evid not in sum_dict: sum_dict[evid] = [sum_line]
        else: sum_dict[evid].append(sum_line)

# read PAL pha
pha_dict, mag_dict = {}, {}
evid=0
f=open(cfg.fpha); lines=f.readlines(); f.close()
for line in lines:
    codes = line.split(',')
    if len(codes[0])>10:
        pha_dict[str(evid)] = []
        mag_dict[str(evid)] = float(codes[4])
        evid += 1
    else: pha_dict[str(evid-1)].append(line)

# merge sum lines
for evid, sum_lines in sum_dict.items():
    sum_list = []
    dtype = [('line','O'),('is_loc','O'),('qua','O'),('azm','O'),('npha','O'),('rms','O')]
    for sum_line in sum_lines:
        codes = sum_line.split()
        is_loc = 1 # whether loc reliable
        if '-' in codes or '#' in codes: is_loc = 0
        qua = sum_line[80:81]
        npha = 1 / float(sum_line[52:55])
        azm  = float(sum_line[56:59])
        rms  = float(sum_line[64:69])
        sum_list.append((sum_line, is_loc, qua, azm, npha, rms))
    sum_list = np.array(sum_list, dtype=dtype)
    sum_list = np.sort(sum_list, order=['qua','azm','npha','rms'])
    sum_list_loc = sum_list[sum_list['is_loc']==1]
    num_loc = len(sum_list_loc)
    # if no reliable loc
    if num_loc==0:
        sum_list_loc = sum_list
        write_csv(out_bad, sum_list_loc[0]['line'], evid)
    else:
        write_csv(out_good, sum_list_loc[0]['line'], evid)
    write_csv(out_ctlg, sum_list_loc[0]['line'], evid)
    out_sum.write(sum_list_loc[0]['line'])
    write_csv(out_pha, sum_list_loc[0]['line'], evid)
    write_csv(out_pha_full, sum_list_loc[0]['line'], evid, True)
    pha_lines = pha_dict[evid]
    for pha_line in pha_lines: 
        out_pha.write(pha_line)
        out_pha_full.write(pha_line)

out_ctlg.close()
out_sum.close()
out_bad.close()
out_good.close()
out_pha.close()
out_pha_full.close()
