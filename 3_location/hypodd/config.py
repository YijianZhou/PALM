""" Configure file for hypoDD interface
    Download hypoDD at https://www.ldeo.columbia.edu/~felixw/hypoDD.html
"""
import os, sys
from pathlib import Path

PALM_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PALM_ROOT / 'PAL_src'))
import data_pipeline as dp

class Config(object):
  def __init__(self):

    self.hypo_root = os.path.expanduser('~/bin')
    self.ctlg_code = 'eg_mft_cc'
    # 1. mk_sta
    self.fsta = 'input/example_pal_format1.sta'
    # 2. mk_dt
    self.temp_pha = 'input/eg_pal_ct_full.pha'    # reloc template phase file
    self.det_pha = 'input/eg_mft.pha'    # mft output phase file
    self.time_range = '20190704-20190707'
    self.num_workers = 3
    self.evid_stride = 100000
    self.dep_corr = 5    # avoid air quake
    self.ot_dev = 2.    # ot diff for det assoc
    self.cc_thres = [0.3,0.4]    # CC thres for det & pick
    self.dt_thres = [0.6,1.]    # max dt_p & dt_s
    self.nbr_thres = [2,30]    # min & max num of neighbor event
    self.min_sta = 4
    self.sta_dict = dp.get_sta_dict(self.fsta)
    # 3. reloc2csv
    self.lat_range = [35.4,36.1]
    self.lon_range = [-117.85,-117.25]
    self.xy_pad = [0.046,0.037]    # degree
    self.num_grids = [1,1]    # x,y (lon, lat)
    self.keep_grids = False
