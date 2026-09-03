""" Download Example SCSN data by STP
STP can be downloaded from https://scedc.caltech.edu/data/stp/index.html
"""
import os, shutil, glob
from obspy import UTCDateTime
import subprocess
import multiprocessing as mp

# i/o files
num_workers = 4
fsta = 'input/example_pal_format1.sta'
time_range = '20190704-20190707'
out_root = '/data/Example_data'
if not os.path.exists(out_root): os.makedirs(out_root)
start_time, end_time = [UTCDateTime(date) for date in time_range.split('-')]
num_days = (end_time.date - start_time.date).days

def down_stp_data(net, sta, date):
    # make out_dir
    year = str(date.year)
    mon = str(date.month).zfill(2)
    day = str(date.day).zfill(2)
    date = year + mon + day
    out_dir = os.path.join(out_root, date) 
    if not os.path.exists(out_dir): os.makedirs(out_dir)
    # check download
    out_paths = glob.glob(os.path.join(out_dir, '%s.%s.*'%(net, sta)))
    if len(out_paths)==3: return
    # download & move
    p = subprocess.Popen(['stp'], stdin=subprocess.PIPE)
    #s = "GAIN ON \n"
    s+= "WIN {} {} HH% {}/{}/{},00:00:00 +1d \n".format(net, sta, year, mon, day)
    s+= "quit \n"
    p.communicate(s.encode())
    # rename & move to out dir
    fnames = glob.glob('%s%s%s*.%s.%s.*'%(year, mon, day, net, sta))
    for fname in fnames: 
        chn = fname.split('.')[-2]
        fname_new = '%s.%s.%s.%s.SAC'%(net,sta,date,chn)
        os.rename(fname, fname_new)
        shutil.move(fname_new, out_dir)

# download data
pool = mp.Pool(num_workers)
f=open(fsta); lines=f.readlines(); f.close()
for line in lines:
    net, sta = line.split(',')[0].split('.')
    for i in range(num_days):
        date = start_time + i*86400
        #down_stp_data(net, sta, date) # use single process for debugging
        pool.apply_async(down_stp_data, args=(net, sta, date,))
pool.close()
pool.join()
