"""Run PAL picker on locally stored daily waveform files."""

import argparse
import os
import warnings

from obspy import UTCDateTime

import config_pal
import picker_pal
from trigger_counts import trigger_count_path, write_trigger_counts


warnings.filterwarnings("ignore")


def run_pick(time_range, data_dir, sta_file, out_pick_dir, cfg, overwrite=False):
    get_data_dict = cfg.get_data_dict
    read_data = cfg.read_data
    sta_dict = cfg.get_sta_dict(sta_file)
    picker = picker_pal.STA_LTA_Kurtosis(
        win_sta=cfg.win_sta,
        win_lta=cfg.win_lta,
        trig_thres=cfg.trig_thres,
        p_win=cfg.p_win,
        s_win=cfg.s_win,
        pca_win=cfg.pca_win,
        pca_range=cfg.pca_range,
        amp_ratio_thres=cfg.amp_ratio_thres,
        amp_win=cfg.amp_win,
        win_kurt=cfg.win_kurt,
        det_gap=cfg.det_gap,
        to_prep=cfg.to_prep,
        freq_band=cfg.freq_band,
        taper_max_length_sec=cfg.taper_max_length_sec,
        vp=getattr(cfg, "picker_vp", getattr(cfg, "vp", 5.9)),
        vs=getattr(cfg, "picker_vs", getattr(cfg, "vs", 3.45)),
        verbose=bool(getattr(cfg, "picker_verbose", False)),
    )

    os.makedirs(out_pick_dir, exist_ok=True)
    start_date, end_date = [UTCDateTime(date) for date in time_range.split("-")]
    num_days = (end_date.date - start_date.date).days
    print("run pick: raw_waveform --> picks")
    print("time range: {} to {}".format(start_date.date, end_date.date))
    for day_idx in range(num_days):
        date = start_date + day_idx * 86400
        pick_path = os.path.join(out_pick_dir, "{}.pick".format(date.date))
        count_path = trigger_count_path(out_pick_dir, date.date)
        if os.path.exists(pick_path) and count_path.exists() and not overwrite:
            print("skip existing picks: {}".format(pick_path))
            continue
        if count_path.exists():
            count_path.unlink()

        day_start, day_end = date, date + 86400
        normalize_to_three_channels = getattr(cfg, "normalize_to_three_channels", True)
        data_dict = get_data_dict(
            date,
            data_dir,
            normalize_to_three_channels=normalize_to_three_channels,
        )
        data_dict = {
            net_sta: paths for net_sta, paths in data_dict.items()
            if net_sta in sta_dict
        }
        partial_path = pick_path + ".partial"
        station_log_interval = max(
            1, int(getattr(cfg, "station_log_interval", 50))
        )
        try:
            station_counts = {}
            with open(partial_path, "w") as out_pick:
                items = sorted(data_dict.items())
                for index, (net_sta, data_paths) in enumerate(items, start=1):
                    if (
                        index == 1
                        or index % station_log_interval == 0
                        or index == len(items)
                    ):
                        print(
                            "{} {}/{}: {}".format(
                                date.date, index, len(items), net_sta
                            )
                        )
                    stream = read_data(
                        data_paths, sta_dict,
                        start_time=day_start,
                        end_time=day_end,
                        normalize_to_three_channels=normalize_to_three_channels,
                    )
                    picks, num_triggers = picker.pick(
                        stream, out_pick,
                        pick_start_time=day_start, pick_end_time=day_end,
                        return_trigger_count=True,
                    )
                    station_counts[net_sta] = (num_triggers, len(picks))
            os.replace(partial_path, pick_path)
            write_trigger_counts(out_pick_dir, date.date, station_counts)
        except Exception:
            if os.path.exists(partial_path):
                os.remove(partial_path)
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_dir", type=str, default="/data/Example_data")
    parser.add_argument("--time_range", type=str, default="20190704-20190707")
    parser.add_argument("--sta_file", type=str, default="input/example_pal_format1.sta")
    parser.add_argument("--out_pick_dir", type=str, default="output/eg/picks")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    run_pick(
        args.time_range,
        args.data_dir,
        args.sta_file,
        args.out_pick_dir,
        config_pal.Config(),
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
