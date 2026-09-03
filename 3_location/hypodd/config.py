"""Configure the hypoDD relocation stage for associated MFT events."""
import os
from pathlib import Path

PALM_ROOT = Path(__file__).resolve().parents[2]

class Config(object):
  def __init__(self):

    self.hypo_root = os.path.expanduser('~/bin')
    self.ctlg_code = 'eg_mft_cc'
    self.mft_output_root = PALM_ROOT / '2_run_mft' / 'output' / 'eg'
    self.fsta = 'input/example_pal_format1.sta'
    self.time_range = '20190704-20190707'
    self.num_workers = 3
    self.hypodd_depth_offset_km = 5.0
    self.lat_range = [35.4,36.1]
    self.lon_range = [-117.85,-117.25]
    self.xy_pad = [0.046,0.037]    # degree
    self.num_grids = [1,1]    # x,y (lon, lat)
    self.keep_grids = False
