""" Configure file for hypoDD interface
    Download hypoDD at https://www.ldeo.columbia.edu/~felixw/hypoDD.html
"""
import os
import numpy as np

class Config(object):
  def __init__(self):

    self.hypo_root = os.path.expanduser('~/bin')
    self.ctlg_code = 'eg_pal_ct'
    self.fsta = 'input/example_pal.sta'
    self.fpha = 'input/eg_pal_hyp_full.pha'
    self.dep_corr = 5 # avoid air quake
    self.ot_range = '20190704-20190707'
    self.lat_range = [35.45,36.05]
    self.lon_range = [-117.8,-117.25]
    self.num_grids = [1,1] # x,y (lon, lat)
    self.xy_pad = [0.06,0.05] # degree
    self.num_workers = 5
    self.keep_grids = False
