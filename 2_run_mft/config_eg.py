import data_pipeline as dp

class Config(object):
  def __init__(self):

    # template cut
    self.win_len = [10,20]         # cut template length
    self.win_snr = [1,1]           # sec pre-post P for SNR calc 
    self.win_sta_lta = [8,1]       # STA/LTA measurement for SNR
    self.min_snr = 12              # defined by energy
    # MFT params
    self.min_sta = 4               # min sta num for template event
    self.max_sta = 15              # max sta num for template event
    self.temp_win_det = [1.,9.]    # temp win for detection, pre & post P
    self.temp_win_p = [0.5,1.5]    # temp win for p pick, pre & post P
    self.temp_win_s = [0.5,2.5]    # temp win for s pick, pre & post S
    self.trig_thres = 0.3          # cc thres for det & peak expansion
    self.expand_len = 1.           # win len for cc peak expansion
    self.det_gap = 5.              # gap sec for detection
    self.pick_win_p = [1.0,1.0]    # search win for P pick
    self.pick_win_s = [1.6,1.6]    # search win for S pick
    self.chn_p = [2]               # chn for P pick
    self.chn_s = [0,1]             # chn for S pick
    self.amp_win = [1,5]           # win for amp measurement
    # data process
    self.data_buffer_sec = 30.0     # adjacent-day context on each side
    self.taper_max_length_sec = 5.0 # taper cap at each buffered outer edge
    self.samp_rate = 50
    self.freq_band = [1.,20.]
    self.num_workers = 10
    self.get_data_dict = dp.get_data_dict
    self.get_sta_dict = dp.get_sta_dict
