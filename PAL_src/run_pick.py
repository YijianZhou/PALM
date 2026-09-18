"""Run PAL picker on locally stored daily waveform files."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
import warnings

from obspy import UTCDateTime

import config_pal
import picker_pal
from trigger_counts import trigger_count_path, write_trigger_counts
from rolling_waveform import merge_cached_tail, processing_bounds, raw_tail


warnings.filterwarnings("ignore")


def _ownership_path(pick_path):
    return pick_path + ".ownership.json"


def _has_current_ownership(pick_path, buffer_sec):
    path = _ownership_path(pick_path)
    if not os.path.exists(path):
        return float(buffer_sec) == 0.0
    try:
        with open(path, encoding="utf-8") as fp:
            metadata = json.load(fp)
        return (
            float(metadata.get("data_buffer_sec")) == float(buffer_sec)
            and metadata.get("interval")
            == "[D-data_buffer_sec,D+1day-data_buffer_sec)"
        )
    except (OSError, TypeError, ValueError):
        return False


def _write_ownership(pick_path, buffer_sec):
    path = _ownership_path(pick_path)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as fp:
        json.dump({
            "data_buffer_sec": float(buffer_sec),
            "interval": "[D-data_buffer_sec,D+1day-data_buffer_sec)",
        }, fp, indent=2)
        fp.write("\n")
    os.replace(partial, path)


def run_pick(
    time_range, data_dir, sta_file, out_pick_dir, cfg, overwrite=False,
    num_workers=1,
):
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
        to_filter=bool(getattr(cfg, "to_filter", True)),
        freq_band=cfg.freq_band,
        taper_max_length_sec=cfg.taper_max_length_sec,
        vp=getattr(cfg, "picker_vp", getattr(cfg, "vp", 5.9)),
        vs=getattr(cfg, "picker_vs", getattr(cfg, "vs", 3.45)),
        verbose=bool(getattr(cfg, "picker_verbose", False)),
    )

    os.makedirs(out_pick_dir, exist_ok=True)
    start_date, end_date = [UTCDateTime(date) for date in time_range.split("-")]
    num_days = (end_date.date - start_date.date).days
    buffer_sec = float(getattr(cfg, "data_buffer_sec", 0.0))
    num_workers = max(1, int(num_workers))
    if buffer_sec < 0:
        raise ValueError("data_buffer_sec must be nonnegative")
    if buffer_sec and buffer_sec < float(cfg.taper_max_length_sec):
        raise ValueError(
            "data_buffer_sec must be at least taper_max_length_sec"
        )

    def load_day_tail(date):
        if buffer_sec <= 0:
            return {}
        day_end = date + 86400
        data_dict = get_data_dict(
            date, data_dir,
            normalize_to_three_channels=getattr(
                cfg, "normalize_to_three_channels", True
            ),
        )
        tails = {}
        for net_sta, data_paths in sorted(data_dict.items()):
            if net_sta not in sta_dict:
                continue
            stream = read_data(
                data_paths, sta_dict,
                start_time=day_end - 2.0 * buffer_sec,
                end_time=day_end,
                normalize_to_three_channels=getattr(
                    cfg, "normalize_to_three_channels", True
                ),
                to_prep=bool(getattr(cfg, "to_prep", True)),
                location_priority=getattr(
                    cfg, "location_priority", ("10", "20", "01", "02", "00", "")
                ),
                channel_priority=getattr(
                    cfg, "channel_priority", ("HH", "BH", "EH", "HN", "EN", "SH")
                ),
            )
            tail = raw_tail(stream, day_end, buffer_sec)
            if tail:
                tails[net_sta] = tail
        return tails

    previous_raw_tails = load_day_tail(start_date - 86400)
    print("run pick: raw_waveform --> picks")
    print("time range: {} to {}".format(start_date.date, end_date.date))
    for day_idx in range(num_days):
        date = start_date + day_idx * 86400
        pick_path = os.path.join(out_pick_dir, "{}.pick".format(date.date))
        count_path = trigger_count_path(out_pick_dir, date.date)
        if (
            os.path.exists(pick_path)
            and count_path.exists()
            and not overwrite
            and _has_current_ownership(pick_path, buffer_sec)
        ):
            print("skip existing picks: {}".format(pick_path))
            previous_raw_tails = load_day_tail(date)
            continue
        if count_path.exists():
            count_path.unlink()

        ownership_path = _ownership_path(pick_path)
        if os.path.exists(ownership_path):
            os.remove(ownership_path)
        day_start, day_end = date, date + 86400
        pick_start, pick_end = processing_bounds(day_start, buffer_sec)
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
            next_raw_tails = {}
            items = sorted(data_dict.items())

            def process_station(item):
                net_sta, data_paths = item
                stream = read_data(
                    data_paths, sta_dict,
                    start_time=day_start,
                    end_time=day_end,
                    normalize_to_three_channels=normalize_to_three_channels,
                    to_prep=bool(getattr(cfg, "to_prep", True)),
                    location_priority=getattr(
                        cfg, "location_priority",
                        ("10", "20", "01", "02", "00", ""),
                    ),
                    channel_priority=getattr(
                        cfg, "channel_priority",
                        ("HH", "BH", "EH", "HN", "EN", "SH"),
                    ),
                )
                next_tail = raw_tail(stream, day_end, buffer_sec)
                stream = merge_cached_tail(
                    stream,
                    previous_raw_tails.get(net_sta),
                    day_start - 2.0 * buffer_sec,
                    day_end,
                )
                station_output = io.StringIO()
                picks, num_triggers = picker.pick(
                    stream, station_output,
                    pick_start_time=pick_start, pick_end_time=pick_end,
                    return_trigger_count=True,
                )
                return (
                    net_sta,
                    station_output.getvalue(),
                    num_triggers,
                    len(picks),
                    next_tail,
                )

            with open(partial_path, "w") as out_pick:
                if num_workers > 1 and len(items) > 1:
                    executor = ThreadPoolExecutor(
                        max_workers=min(num_workers, len(items))
                    )
                    results = executor.map(process_station, items)
                else:
                    executor = None
                    results = map(process_station, items)
                try:
                    for index, result in enumerate(results, start=1):
                        (
                            net_sta, station_output, num_triggers,
                            num_picks, next_tail,
                        ) = result
                        out_pick.write(station_output)
                        station_counts[net_sta] = (num_triggers, num_picks)
                        if next_tail:
                            next_raw_tails[net_sta] = next_tail
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
                finally:
                    if executor is not None:
                        executor.shutdown()
            os.replace(partial_path, pick_path)
            write_trigger_counts(out_pick_dir, date.date, station_counts)
            _write_ownership(pick_path, buffer_sec)
            previous_raw_tails = next_raw_tails
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
    parser.add_argument("--num_workers", type=int, default=1)
    args = parser.parse_args()
    run_pick(
        args.time_range,
        args.data_dir,
        args.sta_file,
        args.out_pick_dir,
        config_pal.Config(),
        overwrite=args.overwrite,
        num_workers=args.num_workers,
    )


if __name__ == "__main__":
    main()
