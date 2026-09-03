""" Basic modules of MFT
"""
import os, time
from scipy.signal import correlate
import numpy as np
import config

# MFT params
cfg = config.Config()
min_sta = cfg.min_sta
freq_band = cfg.freq_band
samp_rate = cfg.samp_rate
phase_samp_rate = cfg.phase_samp_rate
if phase_samp_rate < samp_rate:
    raise ValueError("phase_samp_rate must be at least samp_rate")
temp_win_det_phase = [
    int(phase_samp_rate * win) for win in cfg.temp_win_det
]
temp_win_p = [int(phase_samp_rate * win) for win in cfg.temp_win_p]
temp_win_s = [int(phase_samp_rate * win) for win in cfg.temp_win_s]
pick_win_p = [int(phase_samp_rate * win) for win in cfg.pick_win_p]
pick_win_s = [int(phase_samp_rate * win) for win in cfg.pick_win_s]
amp_win = [int(phase_samp_rate * win) for win in cfg.amp_win]
expand_len = int(samp_rate * cfg.expand_len)
det_gap = int(samp_rate * cfg.det_gap)
chn_p = cfg.chn_p
chn_s = cfg.chn_s
trig_thres = cfg.trig_thres


def mft_det(temp_pick_dict, data_dict):
    """ MFT detection (main)
    Input
      temp, norm_temp, dt_list = temp_pick_dict[net_sta]
      data, norm_data = data_dict[net_sta]
    Output
      dets = [det_ot, det_cc]
      *note: det_ot is in relative sec
    """
    t=time.time()
    # get batch input
    data_list, temp_list, dt_ot_list = [], [], []
    for net_sta, [temp, norm_temp, dt_list] in temp_pick_dict.items():
        if net_sta not in data_dict: continue
        data, norm_data = data_dict[net_sta][:2]
        data_list.append([data.numpy(), norm_data.numpy()])
        temp_list.append([temp[0].numpy(), norm_temp[0].numpy()])
        dt_ot_list.append(dt_list[0])
    dt_ot_list = np.array(dt_ot_list)
    num_sta = len(data_list)
    if num_sta<min_sta: return []
    # 1. match
    cc_mat = match_filter(data_list, temp_list)
    cc_cond = np.amax(cc_mat, axis=1) > trig_thres
    cc_mat = cc_mat[cc_cond]
    dt_ot_list = dt_ot_list[cc_cond]
    num_sta = len(cc_mat)
    if num_sta<min_sta: return []
    # 2. expand
    cc_expand = [expand_cc(cc_i) for cc_i in cc_mat]
    # 3. shift
    data_npts = data_list[0][0].shape[-1]
    cc_holder = np.zeros([num_sta, data_npts])
    cc_shift = shift_ot(cc_expand, dt_ot_list, cc_holder)
    # 4. stack & detect
    cc_stack = np.mean(cc_shift, axis=0)
    dets = det_cc_stack(cc_stack)
    print('{} detections, {} stations, {:.1f}s'.format(len(dets), num_sta, time.time()-t))
    return dets


def cc_pick(det_ot, temp_pick_dict, data_dict):
    """ Cross-correlation phase picking (main)
    Input
      det_ot: detected orgin time (relative sec to date)
      temp_pick_dict
      data_dict
    Output
      picks: [net_sta, tp, ts, dt_p, dt_s, s_amp, cc_p, cc_s,
              cc_det_phase]
    """
    det_ot = int(round(det_ot * phase_samp_rate)) # sec to phase-data idx
    picks = []
    for net_sta, [temp, norm_temp, dt_list] in temp_pick_dict.items():
        # get np data & temp
        if net_sta not in data_dict: continue
        data_np = data_dict[net_sta][2].numpy()
        temp = [di.numpy() for di in temp]
        norm_temp = [di.numpy() for di in norm_temp]
        # slice p&s data
        det_range = [
            temp_win_det_phase[0] + pick_win_p[0],
            temp_win_det_phase[1] + pick_win_p[1],
        ]
        p_range = [temp_win_p[0] + pick_win_p[0], temp_win_p[1] + pick_win_p[1]]
        s_range = [temp_win_s[0] + pick_win_s[0], temp_win_s[1] + pick_win_s[1]]
        tp0, ts0 = int(det_ot + dt_list[1]), int(det_ot + dt_list[2])
        if tp0 < max(det_range[0], p_range[0]) or ts0 < s_range[0] \
        or tp0 + max(det_range[1], p_range[1]) > data_np.shape[-1] \
        or ts0 + s_range[1] > data_np.shape[-1]: continue
        data_det = data_np[:, tp0 - det_range[0] : tp0 + det_range[1]]
        data_p = data_np[:, tp0 - p_range[0] : tp0 + p_range[1]]
        data_s = data_np[:, ts0 - s_range[0] : ts0 + s_range[1]]
        # 1. pick by cc
        cc_p = [calc_cc(data_p[i], temp[1][i], norm_temp=norm_temp[1][i]) for i in chn_p]
        cc_s = [calc_cc(data_s[i], temp[2][i], norm_temp=norm_temp[2][i]) for i in chn_s]
        cc_p = np.mean(cc_p, axis=0)
        cc_s = np.mean(cc_s, axis=0)
        cc_det_phase = np.mean([
            calc_cc(data_det[i], temp[3][i], norm_temp=norm_temp[3][i])
            for i in range(3)
        ], axis=0)
        # [tp, ts], [dt_p, dt_s] (relative sec), & [cc_p, cc_s]
        tp_idx = tp0 + np.argmax(cc_p) - pick_win_p[0]
        ts_idx = ts0 + np.argmax(cc_s) - pick_win_s[0]
        tp, ts = tp_idx/phase_samp_rate, ts_idx/phase_samp_rate
        dt_p = (tp_idx-tp0) / phase_samp_rate
        dt_s = (ts_idx-ts0) / phase_samp_rate
        cc_p_max, cc_s_max = np.amax(cc_p), np.amax(cc_s)
        cc_det_phase_max = np.amax(cc_det_phase)
        # 2. get amplitude
        data_amp = data_np[:, tp_idx-amp_win[0] : ts_idx+amp_win[1]]
        if data_amp.shape[1]<amp_win[0]: continue
        s_amp = get_s_amp(data_amp)
        picks.append([
            net_sta, tp, ts, dt_p, dt_s, s_amp, cc_p_max, cc_s_max,
            cc_det_phase_max,
        ])
    return picks


""" Base functions
"""

def calc_cc(data, temp, norm_data=[], norm_temp=None):
    ntemp, ndata = len(temp), len(data)
    if ntemp>ndata: return [0]
    if not norm_temp:
        norm_temp = np.sqrt(np.sum(temp**2))
    if len(norm_data)==0:
        data_cum = np.concatenate(([0.0], np.cumsum(data**2)))
        norm_data = np.sqrt(data_cum[ntemp:] - data_cum[:-ntemp])
    cc = correlate(data, temp, mode='valid')
    cc /= norm_data * norm_temp
    cc[np.isinf(cc)] = 0.
    cc[np.isnan(cc)] = 0.
    return cc

# 1. matched filter (calc cc traces)
def match_filter(data_list, temp_list):
    num_sta = len(data_list)
    cc_mat = []
    for i in range(num_sta):
        data, norm_data = data_list[i]
        temp, norm_temp = temp_list[i]
        cc_i  = calc_cc(data[0], temp[0], norm_data[0], norm_temp[0])
        cc_i += calc_cc(data[1], temp[1], norm_data[1], norm_temp[1])
        cc_i += calc_cc(data[2], temp[2], norm_data[2], norm_temp[2])
        cc_i /= 3
        cc_mat.append(cc_i)
    return np.array(cc_mat)

# 2. expand peak value in CC trace
def expand_cc(cc):
    trig_idxs = np.where(cc>trig_thres)[0]
    slide_idx = 0
    for trig_idx in trig_idxs:
        if trig_idx < slide_idx: continue
        cc_trig = cc[trig_idx : trig_idx+2*expand_len]
        cc_max = np.amax(cc_trig)
        idx_max = trig_idx + np.argmax(cc_trig)
        idx_0 = max(0, idx_max - expand_len//2)
        idx_1 = idx_max + expand_len//2
        cc[idx_0:idx_1] = cc_max
        # next trig
        slide_idx = trig_idx + expand_len + det_gap
    return cc

# 3. shift time shift to ot
def shift_ot(cc_list, dt_ot_list, cc_holder):
    for i,dt_ot in enumerate(dt_ot_list):
        cc_i = cc_list[i][max(0,-dt_ot) : cc_holder.shape[1] - dt_ot]
        cc_holder[i][max(0,dt_ot) : max(0,dt_ot) + len(cc_i)] = cc_i
    return cc_holder

# 4. detect on stacked cc trace
def det_cc_stack(cc_stack):
    det_idxs = np.where(cc_stack>trig_thres)[0]
    slide_idx = 0
    dets = []
    for det_idx in det_idxs:
        if det_idx < slide_idx: continue
        # det ot (rel sec)
        cc_det = cc_stack[det_idx : det_idx+2*expand_len]
        cc_max = np.amax(cc_det)
        det_ot = (det_idx + np.median(np.where(cc_det == cc_max)[0])) / samp_rate
        dets.append([det_ot, cc_max]) 
        # next det
        slide_idx = det_idx + expand_len + det_gap
    return dets

# get S amplitide
def get_s_amp(velo):
    # remove mean
    velo -= np.reshape(np.mean(velo, axis=1), [velo.shape[0],1])
    # velocity to displacement
    disp = np.cumsum(velo, axis=1)
    disp /= phase_samp_rate
    return np.amax(np.sum(disp**2, axis=0))**0.5

# write detection to catalog
def write_ctlg(det_ot, det_cc, temp_name, temp_loc, out_ctlg):
    out_ctlg.write('{0},{1},{2[1]},{2[2]},{2[3]},{3:.3f}\n'.format(temp_name, det_ot, temp_loc, det_cc))

# write phase picks to phase file
def write_pha(det_ot, det_cc, temp_name, temp_loc, picks, out_pha):
    out_pha.write('{0},{1},{2[1]},{2[2]},{2[3]},{3:.3f}\n'.format(temp_name, det_ot, temp_loc, det_cc))
    for pick in picks:
        # net_sta, tp, ts, dt_p, dt_s, s_amp, cc_p, cc_s, cc_det_phase
        out_pha.write('{0[0]},{0[1]},{0[2]},{0[3]:.2f},{0[4]:.2f},{0[5]},{0[6]:.3f},{0[7]:.3f},{0[8]:.3f}\n'.format(pick))
