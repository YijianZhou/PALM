"""Daily PAL association, merging, rates, and parallel orchestration."""

import contextlib
import csv
import importlib
import inspect
import json
import multiprocessing
import os
import traceback
import time
from queue import Empty
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from obspy import UTCDateTime

from phase_merge import merge_phase_files, read_phase_file
from trigger_counts import read_trigger_counts
import runtime_console


ASSOC_PARAM_NAMES = (
    "xy_margin", "xy_grid", "z_grids", "min_sta",
    "ot_dev", "max_res", "max_drop", "vp",
)
OPTIONAL_ASSOC_PARAM_NAMES = ("lat_range", "lon_range")
ASSOCIATION_PICK_MATCH_TOL_SEC = 0.1

def get_association_buffer_sec(cfg):
    """Return the offline interval halo without conflating it with pick logic."""
    if hasattr(cfg, "association_buffer_sec"):
        buffer_sec = float(cfg.association_buffer_sec)
    elif hasattr(cfg, "s_win"):
        # Backward compatibility for rule-based PAL configs. PAL picks S inside
        # s_win, and the historical daily association halo was twice that span.
        buffer_sec = 2.0 * float(cfg.s_win)
    else:
        raise AttributeError(
            "config requires association_buffer_sec (or legacy PAL s_win)"
        )
    if buffer_sec < 0:
        raise ValueError("association_buffer_sec must be nonnegative")
    return buffer_sec


def geometry_key(stations):
    return tuple(
        sorted((net_sta, row[0], row[1], row[2]) for net_sta, row in stations.items())
    )


def get_assoc_params(cfg, subnet):
    configured = getattr(cfg, "subnet_assoc_params", None)
    if configured is None:
        params = {name: getattr(cfg, name) for name in ASSOC_PARAM_NAMES}
        params.update({
            name: getattr(cfg, name, None) for name in OPTIONAL_ASSOC_PARAM_NAMES
        })
        return params
    params = dict(configured.get("default", {}))
    params.update(configured.get(subnet, {}))
    params.update(configured.get(subnet.split("_")[-1], {}))
    missing = [name for name in ASSOC_PARAM_NAMES if name not in params]
    if missing:
        raise KeyError("missing association params for {}: {}".format(
            subnet, ", ".join(missing)
        ))
    resolved = {name: params[name] for name in ASSOC_PARAM_NAMES}
    resolved.update({name: params.get(name) for name in OPTIONAL_ASSOC_PARAM_NAMES})
    return resolved


def to_associator_sta_dict(stations):
    return {
        net_sta: [
            row["latitude"], row["longitude"], row["elevation"],
            list(row["gains"]),
        ]
        for net_sta, row in stations.items()
    }

def utc_day(value):
    return UTCDateTime(str(value))


def processing_day_bounds(cfg, observed_date):
    """Return the half-open pick/event interval owned by a nominal date."""
    shift = float(getattr(cfg, "data_buffer_sec", 0.0))
    if shift < 0:
        raise ValueError("data_buffer_sec must be nonnegative")
    start = utc_day(observed_date) - shift
    return start, start + 86400


def load_picks(cfg, date, pick_dir):
    """Load picks, passing PAL's in-memory origin-time velocity when supported."""
    parameters = inspect.signature(cfg.get_picks).parameters
    kwargs = {}
    if "vp" in parameters:
        kwargs["vp"] = float(getattr(cfg, "picker_vp", 5.9))
    if "vs" in parameters:
        kwargs["vs"] = float(getattr(cfg, "picker_vs", 3.45))
    return cfg.get_picks(date, pick_dir, **kwargs)


def load_station_geometry(cfg, station_file, observed_date):
    get_sta_dict = cfg.get_sta_dict
    parameter_count = len(inspect.signature(get_sta_dict).parameters)
    if parameter_count >= 2:
        stations = get_sta_dict(station_file, observed_date)
    else:
        stations = get_sta_dict(station_file)
    if not stations:
        return {}
    first = next(iter(stations.values()))
    if isinstance(first, dict):
        return to_associator_sta_dict(stations)
    return stations


def buffered_picks(cfg, pick_dir, observed_date, buffer_seconds):
    arrays = []
    for offset in (-1, 0, 1):
        source_date = observed_date + timedelta(days=offset)
        picks = load_picks(cfg, utc_day(source_date), pick_dir)
        if len(picks):
            arrays.append(picks)
    if not arrays:
        return load_picks(cfg, utc_day(observed_date), pick_dir)
    picks = np.concatenate(arrays)
    owned_start, owned_end = processing_day_bounds(cfg, observed_date)
    start = owned_start - buffer_seconds
    end = owned_end + buffer_seconds
    return picks[(picks["sta_ot"] >= start) & (picks["sta_ot"] < end)]


def buffered_pick_arrays(
    pick_arrays, observed_date, buffer_seconds, day_shift_seconds=0.0,
):
    """Filter in-memory adjacent-day picks to one buffered association day."""
    start = utc_day(observed_date) - float(day_shift_seconds)
    return buffered_pick_interval_arrays(
        pick_arrays, start, start + 86400, buffer_seconds
    )


def buffered_pick_interval_arrays(
    pick_arrays, interval_start, interval_end, buffer_seconds,
):
    """Filter in-memory picks to an arbitrary buffered association interval."""
    arrays = [array for array in pick_arrays if array is not None and len(array)]
    if not arrays:
        return None
    picks = np.concatenate(arrays) if len(arrays) > 1 else arrays[0]
    start = UTCDateTime(interval_start) - buffer_seconds
    end = UTCDateTime(interval_end) + buffer_seconds
    return picks[(picks["sta_ot"] >= start) & (picks["sta_ot"] < end)]


def associate_subnet_picks(
    observed_date, subnet, station_file, picks,
    output_catalog, output_phase, cfg, associator_cache,
    buffer_seconds, associator_cache_lock=None,
):
    """Associate one subnet from an already buffered in-memory pick array."""
    import associator_pal

    stations = load_station_geometry(cfg, station_file, observed_date)
    if picks is None:
        picks = []
    if len(picks):
        picks = picks[[net_sta in stations for net_sta in picks["net_sta"]]]

    output_catalog = Path(output_catalog)
    output_phase = Path(output_phase)
    output_catalog.parent.mkdir(parents=True, exist_ok=True)
    output_phase.parent.mkdir(parents=True, exist_ok=True)
    catalog_partial = output_catalog.with_suffix(output_catalog.suffix + ".partial")
    phase_partial = output_phase.with_suffix(output_phase.suffix + ".partial")
    with catalog_partial.open("w", encoding="utf-8") as catalog_fp, \
            phase_partial.open("w", encoding="utf-8") as phase_fp:
        if stations and len(picks):
            key = (subnet, geometry_key(stations))
            if associator_cache_lock is None:
                associator = associator_cache.get(key)
                if associator is None:
                    associator = associator_pal.PS_Pair_Assoc(
                        stations, **get_assoc_params(cfg, subnet)
                    )
                    associator_cache[key] = associator
            else:
                with associator_cache_lock:
                    associator = associator_cache.get(key)
                    if associator is None:
                        associator = associator_pal.PS_Pair_Assoc(
                            stations, **get_assoc_params(cfg, subnet)
                        )
                        associator_cache[key] = associator
            associator.associate(picks, catalog_fp, phase_fp)
    os.replace(catalog_partial, output_catalog)
    os.replace(phase_partial, output_phase)
    events = read_phase_file(output_phase, source=subnet)
    return {
        "num_buffered_input_picks": len(picks),
        "num_events": len(events),
        "num_associated_picks": sum(len(event["picks"]) for event in events),
        "num_stations": len(stations),
        "buffer_seconds": buffer_seconds,
    }


def associate_subnet_day(
    observed_date, subnet, station_file, pick_dir,
    output_catalog, output_phase, cfg, associator_cache,
    buffer_seconds, association_buffer_enabled=True,
):
    if association_buffer_enabled:
        picks = buffered_picks(cfg, pick_dir, observed_date, buffer_seconds)
    else:
        picks = load_picks(cfg, utc_day(observed_date), pick_dir)
    return associate_subnet_picks(
        observed_date,
        subnet,
        station_file,
        picks,
        output_catalog,
        output_phase,
        cfg,
        associator_cache,
        buffer_seconds,
    )


def merge_canonical_day(
    observed_date, raw_phase_files, output_phase, output_catalog,
    output_groups, cfg,
):
    owned_start, owned_end = processing_day_bounds(cfg, observed_date)
    start = owned_start.datetime
    end = owned_end.datetime
    return merge_phase_files(
        raw_phase_files,
        output_phase,
        output_catalog,
        output_groups,
        cfg,
        event_time_start=start,
        event_time_end=end,
    )


def merge_canonical_interval(
    interval_start, interval_end, raw_phase_files, output_phase,
    output_catalog, output_groups, cfg, min_both_group_ratio=None,
):
    """Merge duplicate events and assign them to one half-open UTC interval."""
    start = UTCDateTime(interval_start).datetime
    end = UTCDateTime(interval_end).datetime
    return merge_phase_files(
        raw_phase_files,
        output_phase,
        output_catalog,
        output_groups,
        cfg,
        event_time_start=start,
        event_time_end=end,
        min_both_group_ratio=min_both_group_ratio,
    )


def merge_buffered_interval_candidates(
    raw_phase_files, output_phase, output_catalog, output_groups, cfg,
):
    """Merge subnet detections without assigning hourly origin ownership."""
    return merge_phase_files(
        raw_phase_files,
        output_phase,
        output_catalog,
        output_groups,
        cfg,
    )


def _pick_key(pick):
    return (
        str(pick["net_sta"]),
        round(float(pick["tp"]), 6),
        round(float(pick["ts"]), 6),
    )


def input_picks_for_date(
    cfg, pick_dir, observed_date, association_buffer_enabled=True,
):
    unique = {}
    owned_start, owned_end = processing_day_bounds(cfg, observed_date)
    offsets = (-1, 0, 1) if association_buffer_enabled else (0,)
    for offset in offsets:
        picks = load_picks(
            cfg, utc_day(observed_date + timedelta(days=offset)), pick_dir
        )
        for pick in picks:
            if owned_start <= pick["tp"] < owned_end:
                unique[_pick_key(pick)] = pick
    return list(unique.values())


def associated_phase_picks_for_date(
    phase_paths, observed_date, interval_start=None, interval_end=None,
):
    if interval_start is None:
        interval_start = utc_day(observed_date)
    if interval_end is None:
        interval_end = UTCDateTime(interval_start) + 86400
    interval_start = UTCDateTime(interval_start)
    interval_end = UTCDateTime(interval_end)
    picks = []
    seen = set()
    for path in phase_paths:
        path = Path(path)
        if not path.exists():
            continue
        for event in read_phase_file(path):
            for pick in event["picks"]:
                tp = UTCDateTime(pick["p"])
                ts = UTCDateTime(pick["s"])
                if not interval_start <= tp < interval_end:
                    continue
                key = (pick["sta"], round(float(tp), 6), round(float(ts), 6))
                if key not in seen:
                    seen.add(key)
                    picks.append((pick["sta"], tp, ts))
    return picks


def write_association_rate_from_picks(
    observed_date, input_picks, canonical_phase_paths, output_path,
    trigger_counts=None, interval_start=None, interval_end=None,
):
    """Write rates using raw STA/LTA triggers when an inventory is supplied."""
    denominator_source = (
        "accepted_picks" if trigger_counts is None else "stalta_triggers"
    )
    if interval_start is None:
        interval_start = utc_day(observed_date)
    if interval_end is None:
        interval_end = UTCDateTime(interval_start) + 86400
    interval_start = UTCDateTime(interval_start)
    interval_end = UTCDateTime(interval_end)
    by_station = {}
    for pick in input_picks:
        if not interval_start <= pick["tp"] < interval_end:
            continue
        by_station.setdefault(str(pick["net_sta"]), []).append({
            "tp": pick["tp"], "ts": pick["ts"], "matched": False,
        })

    tolerance = ASSOCIATION_PICK_MATCH_TOL_SEC
    associated = associated_phase_picks_for_date(
        canonical_phase_paths, observed_date, interval_start, interval_end
    )
    for station, tp, ts in associated:
        candidates = by_station.get(station, [])
        best = None
        best_delta = None
        for candidate in candidates:
            if candidate["matched"]:
                continue
            delta = abs(candidate["tp"] - tp) + abs(candidate["ts"] - ts)
            if best_delta is None or delta < best_delta:
                best, best_delta = candidate, delta
        if best is not None and best_delta <= 2.0 * tolerance:
            best["matched"] = True

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_suffix(output_path.suffix + ".partial")
    total_picks = 0
    total_associated = 0
    with partial.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow([
            "date", "net_sta", "num_picks", "num_associated_picks",
            "num_unassociated_picks", "association_ratio",
        ])
        if trigger_counts is None:
            trigger_counts = {
                station: (len(station_picks), len(station_picks))
                for station, station_picks in by_station.items()
            }
        stations = set(by_station) | set(trigger_counts)
        for station in sorted(stations):
            station_picks = by_station.get(station, [])
            if (
                denominator_source == "stalta_triggers"
                and station not in trigger_counts
            ):
                raise ValueError(
                    "{} {} is present in the daily pick file but missing "
                    "from the STA/LTA trigger inventory".format(
                        observed_date, station
                    )
                )
            num_picks, declared_accepted = trigger_counts.get(
                station, (len(station_picks), len(station_picks))
            )
            if declared_accepted != len(station_picks):
                raise ValueError(
                    "{} {} trigger inventory declares {} accepted picks; "
                    "daily pick file contains {}".format(
                        observed_date, station, declared_accepted,
                        len(station_picks),
                    )
                )
            num_associated = sum(pick["matched"] for pick in station_picks)
            if num_associated > num_picks:
                raise ValueError(
                    "{} {} has more associated picks than STA/LTA triggers"
                    .format(observed_date, station)
                )
            num_unassociated = num_picks - num_associated
            writer.writerow([
                observed_date.isoformat(), station, num_picks, num_associated,
                num_unassociated,
                "{:.8f}".format(num_associated / num_picks if num_picks else 0.0),
            ])
            total_picks += num_picks
            total_associated += num_associated
    os.replace(partial, output_path)
    return {
        "total_picks": total_picks,
        "accepted_picks": sum(len(value) for value in by_station.values()),
        "association_denominator": denominator_source,
        "associated_picks": total_associated,
        "unassociated_picks": total_picks - total_associated,
        "associated_pick_ratio": (
            total_associated / total_picks if total_picks else 0.0
        ),
        "num_station_dates": len(stations),
    }


def write_association_rate(
    observed_date, cfg, pick_dir, canonical_phase_paths, output_path,
    association_buffer_enabled=True,
):
    input_picks = input_picks_for_date(
        cfg, pick_dir, observed_date, association_buffer_enabled
    )
    trigger_counts = read_trigger_counts(
        pick_dir, observed_date, required=True
    )
    owned_start, owned_end = processing_day_bounds(cfg, observed_date)
    return write_association_rate_from_picks(
        observed_date, input_picks, canonical_phase_paths, output_path,
        trigger_counts=trigger_counts,
        interval_start=owned_start,
        interval_end=owned_end,
    )


@dataclass(frozen=True)
class AssociationRun:
    subnet_station_files: dict
    pick_dir: str
    out_root: str
    config_module: str
    config_class: str
    num_workers: int
    overwrite: bool
    retry_failed_days: bool
    association_buffer_enabled: bool

    def config(self):
        module = importlib.import_module(self.config_module)
        return getattr(module, self.config_class)()

    def path(self, *parts):
        return Path(self.out_root).joinpath(*parts)


def parse_date_range(time_range):
    start_text, end_text = time_range.split("-")
    start = datetime.strptime(start_text, "%Y%m%d").date()
    end = datetime.strptime(end_text, "%Y%m%d").date()
    if start >= end:
        raise ValueError("time_range must have start < exclusive end")
    return start, end


def dates_between(start, end):
    return [start + timedelta(days=index) for index in range((end - start).days)]


def _date_code(value):
    return value.isoformat()


def _atomic_json(path, value):
    path = Path(path)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def _status_paths(run, stage, observed_date, subnet=None):
    parts = [stage]
    if subnet is not None:
        parts.append(subnet)
    root = run.path(*parts)
    stem = _date_code(observed_date)
    return root / (stem + ".done.json"), root / (stem + ".failed.json")


def _should_skip(run, done_path, failed_path, cfg):
    if run.overwrite:
        return False
    status_path = done_path if done_path.exists() else failed_path
    if not status_path.exists():
        return False
    if status_path == failed_path and run.retry_failed_days:
        return False
    try:
        metadata = json.loads(status_path.read_text(encoding="utf-8"))
        recorded = metadata.get("data_buffer_sec")
        expected = float(getattr(cfg, "data_buffer_sec", 0.0))
        return (
            float(recorded) == expected
            if recorded is not None else expected == 0.0
        )
    except (OSError, TypeError, ValueError):
        return False


def _raw_catalog(run, subnet, observed_date):
    return run.path("subnets", subnet, "catalog_" + _date_code(observed_date) + ".dat")


def _raw_phase(run, subnet, observed_date):
    return run.path("subnets", subnet, "phase_" + _date_code(observed_date) + ".dat")


def _merged_file(run, kind, observed_date, suffix):
    return run.path("merged", kind + "_" + _date_code(observed_date) + suffix)


def _rate_file(run, observed_date):
    return run.path(
        "association_rates",
        "association_rate_" + _date_code(observed_date) + ".csv",
    )


def _raw_worker(task):
    run, subnet, worker_dates = task
    station_file = Path(run.subnet_station_files[subnet])
    cfg = run.config()
    associator_cache = {}
    results = []
    for observed_date in worker_dates:
        done_path, failed_path = _status_paths(run, "raw_status", observed_date, subnet)
        label = "{}:{}".format(subnet, _date_code(observed_date))
        raw_catalog = _raw_catalog(run, subnet, observed_date)
        raw_phase = _raw_phase(run, subnet, observed_date)
        if (
            _should_skip(run, done_path, failed_path, cfg)
            and raw_catalog.exists()
            and raw_phase.exists()
        ):
            results.append((label, "skipped"))
            _report_progress(label, "skipped")
            continue
        log_path = run.path("logs", "raw_assoc_{}_{}.log".format(subnet, _date_code(observed_date)))
        try:
            with log_path.open("w", encoding="utf-8") as log_fp:
                with contextlib.redirect_stdout(log_fp), contextlib.redirect_stderr(log_fp):
                    summary = associate_subnet_day(
                        observed_date,
                        subnet,
                        station_file,
                        run.pick_dir,
                        raw_catalog,
                        raw_phase,
                        cfg,
                        associator_cache,
                        (
                            get_association_buffer_sec(cfg)
                            if run.association_buffer_enabled else 0.0
                        ),
                        run.association_buffer_enabled,
                    )
                    summary.update({
                        "date": _date_code(observed_date),
                        "subnet": subnet,
                        "data_buffer_sec": float(getattr(
                            cfg, "data_buffer_sec", 0.0
                        )),
                    })
                    print(json.dumps(summary, indent=2))
            _atomic_json(done_path, summary)
            failed_path.unlink(missing_ok=True)
            state = "completed"
        except Exception as exc:
            _atomic_json(failed_path, {
                "date": _date_code(observed_date),
                "subnet": subnet,
                "data_buffer_sec": float(getattr(cfg, "data_buffer_sec", 0.0)),
                "error": repr(exc),
                "traceback": traceback.format_exc(),
                "log": str(log_path),
            })
            state = "failed"
        results.append((label, state))
        _report_progress(label, state)
    return results


def _merge_day(task):
    run, observed_date = task
    cfg = run.config()
    done_path, failed_path = _status_paths(run, "merge_status", observed_date)
    merged_phase = _merged_file(run, "phase", observed_date, ".dat")
    merged_catalog = _merged_file(run, "catalog", observed_date, ".dat")
    merged_groups = _merged_file(run, "event_groups", observed_date, ".csv")
    if (
        _should_skip(run, done_path, failed_path, cfg)
        and merged_phase.exists()
        and merged_catalog.exists()
        and merged_groups.exists()
    ):
        return _date_code(observed_date), "skipped"
    try:
        phase_files = {}
        offsets = (-1, 0, 1) if run.association_buffer_enabled else (0,)
        for offset in offsets:
            source_date = observed_date + timedelta(days=offset)
            for subnet in sorted(run.subnet_station_files):
                path = _raw_phase(run, subnet, source_date)
                if path.exists():
                    phase_files["{}:{}".format(subnet, _date_code(source_date))] = path
        summary = merge_canonical_day(
            observed_date,
            phase_files,
            merged_phase,
            merged_catalog,
            merged_groups,
            cfg,
        )
        summary["date"] = _date_code(observed_date)
        summary["data_buffer_sec"] = float(getattr(
            cfg, "data_buffer_sec", 0.0
        ))
        _atomic_json(done_path, summary)
        failed_path.unlink(missing_ok=True)
        return _date_code(observed_date), "completed"
    except Exception as exc:
        _atomic_json(failed_path, {
            "date": _date_code(observed_date),
            "data_buffer_sec": float(getattr(cfg, "data_buffer_sec", 0.0)),
            "error": repr(exc),
            "traceback": traceback.format_exc(),
        })
        return _date_code(observed_date), "failed"


def _finalize_day(task):
    run, observed_date = task
    cfg = run.config()
    done_path, failed_path = _status_paths(run, "assoc_status", observed_date)
    rate_file = _rate_file(run, observed_date)
    merged_phase = _merged_file(run, "phase", observed_date, ".dat")
    merged_catalog = _merged_file(run, "catalog", observed_date, ".dat")
    if (
        _should_skip(run, done_path, failed_path, cfg)
        and rate_file.exists()
        and merged_phase.exists()
        and merged_catalog.exists()
    ):
        return _date_code(observed_date), "skipped"
    try:
        offsets = (-1, 0, 1) if run.association_buffer_enabled else (0,)
        nearby_phases = [
            _merged_file(run, "phase", observed_date + timedelta(days=offset), ".dat")
            for offset in offsets
        ]
        rate_summary = write_association_rate(
            observed_date, cfg, run.pick_dir, nearby_phases, rate_file,
            run.association_buffer_enabled,
        )
        merge_done, _ = _status_paths(run, "merge_status", observed_date)
        merge_summary = json.loads(merge_done.read_text(encoding="utf-8"))
        summary = {
            "date": _date_code(observed_date),
            "data_buffer_sec": float(getattr(cfg, "data_buffer_sec", 0.0)),
            **rate_summary,
            "num_events": merge_summary["num_merged_events"],
            "num_input_subnet_events": merge_summary["num_input_events"],
            "num_duplicate_events_removed": merge_summary["num_duplicate_events_removed"],
            "num_multi_subnet_events": merge_summary["num_multi_subnet_events"],
            "association_rate_file": str(rate_file),
            "merged_phase": str(merged_phase),
            "merged_catalog": str(merged_catalog),
        }
        _atomic_json(done_path, summary)
        failed_path.unlink(missing_ok=True)
        return _date_code(observed_date), "completed"
    except Exception as exc:
        _atomic_json(failed_path, {
            "date": _date_code(observed_date),
            "data_buffer_sec": float(getattr(cfg, "data_buffer_sec", 0.0)),
            "error": repr(exc),
            "traceback": traceback.format_exc(),
        })
        return _date_code(observed_date), "failed"


def _build_raw_tasks(run, worker_dates):
    subnets = sorted(
        run.subnet_station_files,
        key=lambda subnet: (-Path(run.subnet_station_files[subnet]).stat().st_size, subnet),
    )
    workers_by_subnet = {subnet: 1 for subnet in subnets}
    for index in range(max(0, run.num_workers - len(subnets))):
        workers_by_subnet[subnets[index % len(subnets)]] += 1
    tasks = []
    for subnet in subnets:
        count = workers_by_subnet[subnet]
        for index in range(count):
            dates = worker_dates[index::count]
            if dates:
                tasks.append((run, subnet, dates))
    return tasks


_PROGRESS_QUEUE = None


def _init_progress(queue):
    global _PROGRESS_QUEUE
    _PROGRESS_QUEUE = queue


def _report_progress(label, state):
    if _PROGRESS_QUEUE is not None:
        _PROGRESS_QUEUE.put((label, state))


def _progress_task(task):
    function, item = task
    result = function(item)
    if not isinstance(result, list):
        _report_progress(*result)
    return result


class _DayProgress:
    def __init__(self, stage, total_days, subnets=1):
        self.stage = stage
        self.total_days = total_days
        self.subnets = subnets
        self.seen = set()
        self.days = {}
        self.counts = {"completed": 0, "skipped": 0, "failed": 0}
        self.started = time.monotonic()
        self.last_time = self.started
        self.last_done = 0

    def add(self, label, state):
        if label in self.seen:
            return
        self.seen.add(label)
        day = label.rsplit(":", 1)[-1]
        self.days[day] = self.days.get(day, 0) + 1
        self.counts[state] += 1
        self.show(force=state == "failed")

    def show(self, force=False):
        done = sum(count == self.subnets for count in self.days.values())
        now = time.monotonic()
        if not force and done < self.last_done + 10 and now - self.last_time < 30:
            return
        runtime_console.log(
            "progress",
            "{} | {} | days processed {}/{} | tasks completed={} skipped={} failed={} | elapsed {:.0f}s".format(
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.stage,
                done, self.total_days, self.counts["completed"],
                self.counts["skipped"], self.counts["failed"], now - self.started,
            ),
        )
        self.last_done = done
        self.last_time = now


def _run_parallel(function, items, worker_count):
    if not items:
        return []
    raw = function is _raw_worker
    total_days = len({day for _, _, dates in items for day in dates}) if raw else len(items)
    subnets = len({subnet for _, subnet, _ in items}) if raw else 1
    stage = {
        "_raw_worker": "association (including halo days)",
        "_merge_day": "merge (including halo days)",
        "_finalize_day": "finalization",
    }.get(function.__name__, function.__name__)
    progress = _DayProgress(stage, total_days, subnets)
    progress.show(force=True)
    queue = multiprocessing.Queue()
    try:
        with multiprocessing.Pool(
            processes=min(worker_count, len(items)),
            initializer=_init_progress, initargs=(queue,),
        ) as pool:
            pending = pool.map_async(_progress_task, [(function, item) for item in items])
            while not pending.ready():
                try:
                    progress.add(*queue.get(timeout=0.2))
                except Empty:
                    progress.show()
            results = pending.get()
            # Reconcile notifications still in transit and preserve the original result shape.
            for result in results:
                for label, state in result if isinstance(result, list) else [result]:
                    progress.add(label, state)
            progress.show(force=True)
            return results
    finally:
        queue.close()
        queue.join_thread()


def _require_success(stage, results):
    failures = []
    for result in results:
        rows = result if isinstance(result, list) else [result]
        failures.extend(row for row in rows if row[1] == "failed")
    if failures:
        raise RuntimeError("{} failures: {}".format(stage, failures))


def run_daily_association(
    subnet_station_files,
    pick_dir,
    out_root,
    time_range,
    num_workers,
    config_factory,
    overwrite=False,
    retry_failed_days=True,
    association_buffer_enabled=True,
):
    """Associate target days, optionally using adjacent-day pick and event halos."""
    console_cfg = config_factory()
    runtime_console.configure(console_cfg)
    run = AssociationRun(
        subnet_station_files={
            name: str(Path(path).resolve()) for name, path in subnet_station_files.items()
        },
        pick_dir=str(Path(pick_dir).resolve()),
        out_root=str(Path(out_root).resolve()),
        config_module=config_factory.__module__,
        config_class=config_factory.__name__,
        num_workers=max(1, int(num_workers)),
        overwrite=bool(overwrite),
        retry_failed_days=bool(retry_failed_days),
        association_buffer_enabled=bool(association_buffer_enabled),
    )
    if not run.subnet_station_files:
        raise ValueError("at least one subnet station file is required")
    missing = [path for path in run.subnet_station_files.values() if not Path(path).exists()]
    if missing:
        raise FileNotFoundError("missing subnet station files: {}".format(missing))
    if not Path(run.pick_dir).exists():
        raise FileNotFoundError(run.pick_dir)

    for path in (
        run.path("subnets"),
        run.path("merged"),
        run.path("association_rates"),
        run.path("raw_status"),
        run.path("merge_status"),
        run.path("assoc_status"),
        run.path("logs"),
    ):
        path.mkdir(parents=True, exist_ok=True)
    for subnet in run.subnet_station_files:
        run.path("subnets", subnet).mkdir(parents=True, exist_ok=True)
        run.path("raw_status", subnet).mkdir(parents=True, exist_ok=True)

    start, end = parse_date_range(time_range)
    target_dates = dates_between(start, end)
    missing_pick_dates = [
        observed_date for observed_date in target_dates
        if not (Path(run.pick_dir) / ("{}.pick".format(observed_date))).exists()
    ]
    if missing_pick_dates:
        preview = ", ".join(str(value) for value in missing_pick_dates[:10])
        if len(missing_pick_dates) > 10:
            preview += ", ..."
        raise FileNotFoundError(
            "{} target daily pick files are missing: {}".format(
                len(missing_pick_dates), preview
            )
        )
    if run.association_buffer_enabled:
        work_dates = dates_between(start - timedelta(days=1), end + timedelta(days=1))
    else:
        work_dates = target_dates

    mode = "buffered" if run.association_buffer_enabled else "independent-day"
    runtime_console.log(
        "AI-PAL",
        "offline association | target={} | subnets={} | workers={}".format(
            time_range, len(run.subnet_station_files), run.num_workers
        ),
    )
    runtime_console.log("stage", "1/3 {} subnet association".format(mode))
    print("stage 1: {} subnet association".format(mode))
    _require_success(
        "raw association",
        _run_parallel(_raw_worker, _build_raw_tasks(run, work_dates), run.num_workers),
    )
    merge_scope = (
        "cross-day and cross-subnet" if run.association_buffer_enabled
        else "same-day cross-subnet"
    )
    runtime_console.log("stage", "2/3 {} canonical merge".format(merge_scope))
    print("stage 2: {} canonical merge".format(merge_scope))
    _require_success(
        "daily merge",
        _run_parallel(
            _merge_day,
            [(run, observed_date) for observed_date in work_dates],
            run.num_workers,
        ),
    )
    runtime_console.log("stage", "3/3 station-date association rates")
    print("stage 3: station-date association rates")
    _require_success(
        "daily finalization",
        _run_parallel(
            _finalize_day,
            [(run, observed_date) for observed_date in target_dates],
            run.num_workers,
        ),
    )
    print("association complete: {} target days".format(len(target_dates)))
    runtime_console.log(
        "complete",
        "association | {} target days".format(len(target_dates)),
    )
    runtime_console.log("output", "association products | {}".format(out_root))

def _combine_files(paths, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_suffix(output_path.suffix + ".partial")
    with partial.open("w", encoding="utf-8") as output_fp:
        for path in paths:
            path = Path(path)
            if path.exists():
                output_fp.write(path.read_text(encoding="utf-8"))
    partial.replace(output_path)


def combine_daily_association_outputs(
    assoc_root, time_range, output_catalog, output_phase
):
    """Combine canonical daily outputs into the legacy range-level files."""
    start, end = parse_date_range(time_range)
    target_dates = dates_between(start, end)
    merged_dir = Path(assoc_root) / "merged"
    _combine_files(
        [merged_dir / "catalog_{}.dat".format(date) for date in target_dates],
        output_catalog,
    )
    _combine_files(
        [merged_dir / "phase_{}.dat".format(date) for date in target_dates],
        output_phase,
    )


def run_buffered_association(
    subnet_station_files,
    pick_dir,
    assoc_root,
    time_range,
    num_workers,
    config_factory,
    overwrite=False,
    retry_failed_days=True,
    association_buffer_enabled=True,
    output_catalog=None,
    output_phase=None,
):
    """Run daily association and optionally produce legacy range-level files."""
    run_daily_association(
        subnet_station_files=subnet_station_files,
        pick_dir=pick_dir,
        out_root=assoc_root,
        time_range=time_range,
        num_workers=num_workers,
        config_factory=config_factory,
        overwrite=overwrite,
        retry_failed_days=retry_failed_days,
        association_buffer_enabled=association_buffer_enabled,
    )
    if (output_catalog is None) != (output_phase is None):
        raise ValueError("output_catalog and output_phase must be provided together")
    if output_catalog is not None:
        combine_daily_association_outputs(
            assoc_root, time_range, output_catalog, output_phase
        )
        runtime_console.log(
            "output", "final PAL phase | {}".format(output_phase)
        )
        runtime_console.log(
            "output", "final PAL catalog | {}".format(output_catalog)
        )
