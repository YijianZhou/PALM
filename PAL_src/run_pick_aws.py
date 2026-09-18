#!/usr/bin/env python3
"""Run PAL picking directly from the public SCEDC S3 archive."""

import json
from concurrent.futures import ThreadPoolExecutor
import io
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from obspy import UTCDateTime

from data_pipeline_aws import build_s3_client
from rolling_waveform import merge_cached_tail, processing_bounds, raw_tail
from trigger_counts import trigger_count_path, write_trigger_counts



def parse_date(value):
    return datetime.strptime(value, "%Y%m%d").date()


def parse_time_range(value):
    start_text, end_text = value.split("-", 1)
    start, end = parse_date(start_text), parse_date(end_text)
    if start >= end:
        raise ValueError("time_range must have start < exclusive end")
    return start, end


def build_picker(pal_source_dir, cfg):
    sys.path.insert(0, str(Path(pal_source_dir).expanduser().resolve()))
    import picker_pal

    assoc_defaults = getattr(cfg, "subnet_assoc_params", {}).get("default", {})
    picker_vp = getattr(cfg, "picker_vp", getattr(cfg, "vp", assoc_defaults.get("vp", 5.9)))
    picker_vs = getattr(cfg, "picker_vs", getattr(cfg, "vs", 3.45))
    picker = picker_pal.STA_LTA_Kurtosis(
        win_sta=cfg.win_sta, win_lta=cfg.win_lta,
        trig_thres=cfg.trig_thres, p_win=cfg.p_win, s_win=cfg.s_win,
        pca_win=cfg.pca_win, pca_range=cfg.pca_range,
        amp_ratio_thres=cfg.amp_ratio_thres,
        amp_win=cfg.amp_win, win_kurt=cfg.win_kurt, det_gap=cfg.det_gap,
        to_filter=bool(getattr(cfg, "to_filter", True)), freq_band=cfg.freq_band,
        taper_max_length_sec=cfg.taper_max_length_sec,
        vp=picker_vp, vs=picker_vs,
        verbose=bool(getattr(cfg, "picker_verbose", False)),
    )
    return picker, cfg


def ownership_path(pick_path):
    return Path(str(pick_path) + ".ownership.json")


def has_current_ownership(pick_path, buffer_sec):
    path = ownership_path(pick_path)
    if not path.exists():
        return float(buffer_sec) == 0.0
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        return (
            float(metadata.get("data_buffer_sec")) == float(buffer_sec)
            and metadata.get("interval")
            == "[D-data_buffer_sec,D+1day-data_buffer_sec)"
        )
    except (OSError, TypeError, ValueError):
        return False


def write_ownership(pick_path, buffer_sec):
    path = ownership_path(pick_path)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps({
        "data_buffer_sec": float(buffer_sec),
        "interval": "[D-data_buffer_sec,D+1day-data_buffer_sec)",
    }, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)


def load_raw_tail_cache(
    observed_date, station_file, cfg, s3_client, s3_bucket,
    s3_root_prefix, loc_priority, acceleration_codes,
):
    buffer_sec = float(getattr(cfg, "data_buffer_sec", 0.0))
    if buffer_sec <= 0:
        return {}
    active = cfg.get_sta_dict(station_file, observed_date)
    day_end = UTCDateTime(observed_date.isoformat()) + 86400
    data_dict = cfg.get_data_dict(
        observed_date, active, s3_client,
        bucket=s3_bucket, root_prefix=s3_root_prefix,
        location_priority=loc_priority,
    )
    tails = {}
    for net_sta, records in sorted(data_dict.items()):
        try:
            stream = cfg.read_data(
                records, active[net_sta], s3_client, bucket=s3_bucket,
                acceleration_instrument_codes=acceleration_codes,
                start_time=day_end - 2.0 * buffer_sec,
                end_time=day_end,
                to_prep=bool(getattr(cfg, "to_prep", True)),
            )
            tail = raw_tail(stream, day_end, buffer_sec)
            if tail:
                tails[net_sta] = tail
        except Exception as exc:
            print("WARNING tail seed {}: {}".format(net_sta, exc))
    return tails


def process_day(
    observed_date, station_file, pick_dir, picker, cfg, s3_client,
    s3_bucket, s3_root_prefix, loc_priority, acceleration_codes,
    to_overwrite=False, retry_failed=False, previous_raw_tails=None,
    num_workers=1,
):
    pick_dir = Path(pick_dir)
    status_dir = pick_dir.parent / "pick_status"
    pick_dir.mkdir(parents=True, exist_ok=True)
    status_dir.mkdir(parents=True, exist_ok=True)
    stem = observed_date.isoformat()
    pick_path = pick_dir / (stem + ".pick")
    count_path = trigger_count_path(pick_dir, observed_date)
    done_path = status_dir / (stem + ".done.json")
    failed_path = status_dir / (stem + ".failed.json")
    partial_path = pick_path.with_suffix(".pick.partial")
    buffer_sec = float(getattr(cfg, "data_buffer_sec", 0.0))
    if buffer_sec < 0:
        raise ValueError("data_buffer_sec must be nonnegative")
    if buffer_sec and buffer_sec < float(cfg.taper_max_length_sec):
        raise ValueError(
            "data_buffer_sec must be at least taper_max_length_sec"
        )
    if not to_overwrite:
        if (
            done_path.exists() and pick_path.exists() and count_path.exists()
            and has_current_ownership(pick_path, buffer_sec)
        ):
            print("skip completed day {}".format(stem))
            return None
        if (
            failed_path.exists() and pick_path.exists() and count_path.exists()
            and not retry_failed
            and has_current_ownership(pick_path, buffer_sec)
        ):
            print("skip completed day with accepted station errors {}".format(stem))
            return None
    if count_path.exists():
        count_path.unlink()

    active = cfg.get_sta_dict(station_file, observed_date)
    day_start = UTCDateTime(observed_date.isoformat())
    day_end = day_start + 86400
    pick_start, pick_end = processing_bounds(day_start, buffer_sec)
    data_dict = cfg.get_data_dict(
        observed_date, active, s3_client,
        bucket=s3_bucket, root_prefix=s3_root_prefix,
        location_priority=loc_priority,
    )
    errors = []
    num_processed = 0
    station_counts = {}
    next_raw_tails = {}
    ownership_path(pick_path).unlink(missing_ok=True)
    station_log_interval = max(
        1, int(getattr(cfg, "station_log_interval", 50))
    )
    items = sorted(data_dict.items())

    def process_station(item):
        net_sta, records = item
        try:
            stream = cfg.read_data(
                records, active[net_sta], s3_client, bucket=s3_bucket,
                acceleration_instrument_codes=acceleration_codes,
                start_time=day_start,
                end_time=day_end,
                to_prep=bool(getattr(cfg, "to_prep", True)),
            )
            next_tail = raw_tail(stream, day_end, buffer_sec)
            stream = merge_cached_tail(
                stream,
                (previous_raw_tails or {}).get(net_sta),
                day_start - 2.0 * buffer_sec,
                day_end,
            )
            station_output = io.StringIO()
            picks, num_triggers = picker.pick(
                stream, station_output,
                pick_start_time=pick_start, pick_end_time=pick_end,
                return_trigger_count=True,
            )
            return {
                "net_sta": net_sta,
                "output": station_output.getvalue(),
                "num_triggers": num_triggers,
                "num_picks": len(picks),
                "next_tail": next_tail,
                "error": None,
            }
        except Exception as exc:
            return {
                "net_sta": net_sta,
                "error": repr(exc),
                "traceback": traceback.format_exc(),
            }

    with partial_path.open("w", encoding="utf-8") as out_pick:
        if int(num_workers) > 1 and len(items) > 1:
            executor = ThreadPoolExecutor(
                max_workers=min(max(1, int(num_workers)), len(items))
            )
            results = executor.map(process_station, items)
        else:
            executor = None
            results = map(process_station, items)
        try:
            for index, (item, result) in enumerate(
                zip(items, results), start=1
            ):
                net_sta, records = item
                if result["error"] is None:
                    out_pick.write(result["output"])
                    station_counts[net_sta] = (
                        result["num_triggers"], result["num_picks"]
                    )
                    if result["next_tail"]:
                        next_raw_tails[net_sta] = result["next_tail"]
                    num_processed += 1
                else:
                    errors.append({
                        "net_sta": net_sta,
                        "error": result["error"],
                        "traceback": result["traceback"],
                    })
                    print(
                        "ERROR {}: {}".format(net_sta, result["error"]),
                        file=sys.stderr,
                    )
                if (
                    index == 1
                    or index % station_log_interval == 0
                    or index == len(items)
                ):
                    print("{} {}/{}: {} {} ({} object(s))".format(
                        stem, index, len(items), net_sta,
                        active[net_sta]["band"], len(records),
                    ))
        finally:
            if executor is not None:
                executor.shutdown()

    os.replace(partial_path, pick_path)
    write_trigger_counts(pick_dir, observed_date, station_counts)
    write_ownership(pick_path, buffer_sec)
    total_triggers = sum(value[0] for value in station_counts.values())
    total_accepted = sum(value[1] for value in station_counts.values())
    status = {
        "date": stem,
        "active_station_epochs": len(active),
        "stations_with_usable_s3_components": len(data_dict),
        "stations_processed": num_processed,
        "num_stalta_triggers": total_triggers,
        "num_accepted_picks": total_accepted,
        "station_errors": errors,
        "waveform_scope": "rolling_raw_tail",
        "data_buffer_sec": buffer_sec,
        "pick_interval_start": str(pick_start),
        "pick_interval_end": str(pick_end),
        "pick_file": str(pick_path),
        "trigger_count_file": str(count_path),
    }
    status_path = failed_path if errors else done_path
    stale_status_path = done_path if errors else failed_path
    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    if stale_status_path.exists():
        stale_status_path.unlink()
    result = "failed" if errors else "completed"
    print("{} {}: {}/{} stations, {} errors".format(
        result, stem, num_processed, len(data_dict), len(errors),
    ))
    return next_raw_tails


def run_pick(
    run_time_range, station_file, pick_dir, pal_source_dir, cfg,
    s3_bucket, s3_region, s3_root_prefix, s3_access_mode,
    loc_priority, acceleration_codes, to_overwrite, retry_failed=False,
    num_workers=1,
):
    station_file = Path(station_file)
    if not station_file.exists():
        raise FileNotFoundError(station_file)
    if not Path(pal_source_dir).exists():
        raise FileNotFoundError("PAL source directory not found: {}".format(pal_source_dir))

    start, end = parse_time_range(run_time_range)
    picker, cfg = build_picker(pal_source_dir, cfg)
    s3_client = build_s3_client(s3_region, s3_access_mode)
    previous_raw_tails = load_raw_tail_cache(
        start - timedelta(days=1), station_file, cfg, s3_client,
        s3_bucket, s3_root_prefix, loc_priority, acceleration_codes,
    )
    current = start
    while current < end:
        next_raw_tails = process_day(
            current, station_file, pick_dir, picker, cfg, s3_client,
            s3_bucket, s3_root_prefix, loc_priority, acceleration_codes,
            to_overwrite, retry_failed, previous_raw_tails,
            num_workers=num_workers,
        )
        if next_raw_tails is None:
            next_raw_tails = load_raw_tail_cache(
                current, station_file, cfg, s3_client, s3_bucket,
                s3_root_prefix, loc_priority, acceleration_codes,
            )
        previous_raw_tails = next_raw_tails
        current += timedelta(days=1)
