"""PAL picker and associator model parameters for an example SCEDC AWS run."""

import numpy as np
import data_pipeline_aws as dp


class Config(object):
  def __init__(self):
    # 1. picker params
    self.win_sta    = [0.8,0.4,1.]
    self.win_lta    = [6.,2.,2.]
    self.win_kurt   = [5.,1.]
    self.trig_thres = 12.
    self.p_win      = [.5,1.]
    self.s_win      = 10.
    self.pca_win    = 1.
    self.pca_range  = [0.,2.]
    self.amp_ratio_thres = [6,10,3]
    self.amp_win    = [1.,5.]
    self.det_gap    = 5.
    self.to_prep    = True
    self.freq_band  = [1,20]
    # AWS picking reads only the current daily object to avoid triple S3 I/O.
    self.data_buffer_sec = 0.0
    self.station_log_interval = 50
    self.picker_verbose = False
    self.taper_max_length_sec = 10.0
    self.picker_vp   = 5.9
    self.picker_vs   = 3.45

    # 2. associator params
    self.subnet_assoc_params = {
        "default": {
            "min_sta": 4, "ot_dev": 1.4, "max_res": 1.2, "max_drop": 1,
            "xy_margin": 0.1, "xy_grid": 0.02,
            "z_grids": np.arange(2, 25, 3), "vp": 5.9,
        },
        "full": {"min_sta": 4, "ot_dev": 1.4, "max_res": 1.2},
        "r1": {"min_sta": 4, "ot_dev": 1.4, "max_res": 1.2},
        "r2": {"min_sta": 4, "ot_dev": 1.4, "max_res": 1.2},
        "r3": {"min_sta": 4, "ot_dev": 1.4, "max_res": 1.2},
        "r4": {"min_sta": 4, "ot_dev": 1.4, "max_res": 1.2},
        "r5": {"min_sta": 4, "ot_dev": 1.4, "max_res": 1.2},
        "r6": {"min_sta": 4, "ot_dev": 1.4, "max_res": 1.2},
    }

    # Duplicate-event merge params across subnetworks and, when enabled, days.
    self.merge_origin_time_tol_sec = 2.5
    self.merge_epicenter_tol_km = 5.0
    self.merge_depth_tol_km = 10.0
    self.merge_min_shared_phase_stations = 4
    self.merge_phase_pick_time_tol_sec = 1.0
    self.merge_time_format_digits = 6

    # 3. data pipeline
    self.get_data_dict = dp.get_data_dict_aws
    self.get_sta_dict  = dp.get_sta_dict_aws
    self.get_picks     = dp.get_pal_picks
    self.read_data     = dp.read_data_aws
