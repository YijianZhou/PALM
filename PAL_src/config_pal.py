""" Configure file
"""
import data_pipeline as dp
import numpy as np

class Config(object):
  def __init__(self):

    # 1. picker params
    self.win_sta    = [0.8,0.4,1.]   # win for STA: det, p, s
    self.win_lta    = [6.,2.,2.]     # win for LTA: det, p, s
    self.win_kurt   = [5.,1.]        # win for kurtosis: long & short
    self.trig_thres = 12.            # threshold to trig picker (by energy)
    self.p_win      = [.5,1.]        # search win for P 
    self.s_win      = 10.            # search win for S 
    self.pca_win    = 1.             # win_len for PCA filter
    self.pca_range  = [0.,2.]        # time range to apply PCA filter
    self.amp_ratio_thres = [6,10,3]  # max amp ratio for Peak, P/P_tail, & P/S
    self.amp_win    = [1.,5.]        # time win to get S amplitude
    self.det_gap    = 5.             # time gap between detections
    self.to_prep    = True           # whether to preprocess the raw data
    self.freq_band  = [1,20]         # frequency band
    self.data_buffer_sec = 0.0       # PAL training uses current-day data only
    self.station_log_interval = 50
    self.picker_verbose = False
    self.taper_max_length_sec = 10.0 # taper cap and unusable edge
    self.normalize_to_three_channels = True  # cycle/truncate available channels into E/N/Z
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
    }
    self.merge_origin_time_tol_sec = 2.5
    self.merge_epicenter_tol_km = 5.0
    self.merge_depth_tol_km = 10.0
    self.merge_min_shared_phase_stations = 4
    self.merge_phase_pick_time_tol_sec = 1.0
    self.merge_time_format_digits = 6
    # 3. data pipeline
    self.get_data_dict = dp.get_data_dict
    self.get_buffered_data_dict = dp.get_buffered_data_dict
    self.get_sta_dict = dp.get_sta_dict
    self.get_picks = dp.get_pal_picks
    self.read_data = dp.read_data
