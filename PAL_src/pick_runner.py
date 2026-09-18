"""Parallel runner for local and SCEDC AWS daily PAL picking."""

import contextlib
import traceback
from datetime import datetime, timedelta
from pathlib import Path


def parse_date_range(time_range):
    start_text, end_text = time_range.split("-")
    start = datetime.strptime(start_text, "%Y%m%d").date()
    end = datetime.strptime(end_text, "%Y%m%d").date()
    if start >= end:
        raise ValueError("time_range must have start < exclusive end")
    return start, end


def run_parallel_local_pick(
    time_range,
    data_dir,
    station_file,
    pick_dir,
    log_dir,
    num_workers,
    config_factory,
    overwrite=False,
    include_association_halo=False,
):
    """Process dates sequentially and stations concurrently within each date."""
    from run_pick import run_pick

    start, end = parse_date_range(time_range)
    if include_association_halo:
        start -= timedelta(days=1)
        end += timedelta(days=1)
    if start >= end:
        raise ValueError("time range must contain at least one day")

    pick_dir = Path(pick_dir).resolve()
    log_dir = Path(log_dir).resolve()
    pick_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    run_range = "{}-{}".format(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    )
    log_path = log_dir / "pick_{}.log".format(run_range)
    print(
        "PAL picking: sequential days | {} station workers | log {}".format(
            max(1, int(num_workers)), log_path
        )
    )
    with log_path.open("w", encoding="utf-8") as log_fp:
        with contextlib.redirect_stdout(log_fp), contextlib.redirect_stderr(log_fp):
            try:
                run_pick(
                    run_range,
                    str(Path(data_dir).resolve()),
                    str(Path(station_file).resolve()),
                    str(pick_dir),
                    config_factory(),
                    overwrite=bool(overwrite),
                    num_workers=num_workers,
                )
            except BaseException:
                print("\nFATAL PAL PICK ERROR", flush=True)
                traceback.print_exc(file=log_fp)
                log_fp.flush()
                raise
    print("PAL picking completed: {}".format(run_range))

def _pending_aws_dates(
    start, end, pick_dir, overwrite, retry_failed_dates, buffer_sec,
):
    from run_pick_aws import has_current_ownership

    status_dir = Path(pick_dir).parent / "pick_status"
    pending = []
    current = start
    while current < end:
        stem = current.isoformat()
        done = status_dir / (stem + ".done.json")
        failed = status_dir / (stem + ".failed.json")
        pick_path = Path(pick_dir) / (stem + ".pick")
        count_path = Path(pick_dir) / (stem + ".trigger_counts.csv")
        ownership_current = (
            pick_path.exists()
            and count_path.exists()
            and has_current_ownership(pick_path, buffer_sec)
        )
        if overwrite or not ownership_current or (
            not done.exists() and (retry_failed_dates or not failed.exists())
        ):
            pending.append(current)
        current += timedelta(days=1)
    return pending


def run_parallel_aws_pick(
    time_range,
    station_file,
    pick_dir,
    log_dir,
    pal_source_dir,
    num_workers,
    config_factory,
    bucket="scedc-pds",
    region="us-west-2",
    root_prefix="continuous_waveforms",
    access_mode="signed",
    location_priority=(),
    acceleration_instrument_codes=("N",),
    overwrite=False,
    retry_failed_dates=False,
):
    """Process AWS dates sequentially and stations concurrently per date."""
    from data_pipeline_aws import build_s3_client
    from run_pick_aws import build_picker, load_raw_tail_cache, process_day

    start, end = parse_date_range(time_range)
    pick_dir = Path(pick_dir).resolve()
    log_dir = Path(log_dir).resolve()
    pick_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    cfg_preview = config_factory()
    buffer_sec = float(getattr(cfg_preview, "data_buffer_sec", 0.0))
    dates = _pending_aws_dates(
        start, end, pick_dir, bool(overwrite), bool(retry_failed_dates),
        buffer_sec,
    )
    total_days = (end - start).days
    skipped_days = total_days - len(dates)
    if not dates:
        print("all {} AWS pick dates are already complete".format(total_days))
        return
    station_workers = max(1, int(num_workers))
    print(
        "sequential AWS dates: {} pending, {} skipped, {} station workers"
        .format(
            len(dates), skipped_days, station_workers
        )
    )
    cfg = config_factory()
    picker, cfg = build_picker(
        str(Path(pal_source_dir).expanduser().resolve()), cfg
    )
    s3_client = build_s3_client(region, access_mode)
    pending_dates = set(dates)
    previous_raw_tails = {}
    failures = []
    current = start
    completed = 0
    while current < end:
        if current not in pending_dates:
            if current + timedelta(days=1) in pending_dates:
                previous_raw_tails = load_raw_tail_cache(
                    current, station_file, cfg, s3_client, bucket,
                    root_prefix, location_priority,
                    acceleration_instrument_codes,
                )
            current += timedelta(days=1)
            continue
        if not previous_raw_tails and current == start:
            previous_raw_tails = load_raw_tail_cache(
                current - timedelta(days=1), station_file, cfg, s3_client,
                bucket, root_prefix, location_priority,
                acceleration_instrument_codes,
            )
        log_path = log_dir / "pick_day_{}.log".format(current.isoformat())
        try:
            with log_path.open("w", encoding="utf-8") as log_fp:
                with contextlib.redirect_stdout(log_fp), contextlib.redirect_stderr(log_fp):
                    next_raw_tails = process_day(
                        current, station_file, pick_dir, picker, cfg,
                        s3_client, bucket, root_prefix, location_priority,
                        acceleration_instrument_codes, overwrite,
                        retry_failed_dates, previous_raw_tails,
                        num_workers=station_workers,
                    )
                    if next_raw_tails is None:
                        next_raw_tails = load_raw_tail_cache(
                            current, station_file, cfg, s3_client, bucket,
                            root_prefix, location_priority,
                            acceleration_instrument_codes,
                        )
            previous_raw_tails = next_raw_tails
        except Exception as exc:
            with log_path.open("a", encoding="utf-8") as log_fp:
                log_fp.write("\nFATAL DATE ERROR\n")
                log_fp.write(traceback.format_exc())
            failures.append((current.isoformat(), repr(exc)))
            print("AWS pick date {} failed: {}".format(current, exc))
            try:
                previous_raw_tails = load_raw_tail_cache(
                    current, station_file, cfg, s3_client, bucket,
                    root_prefix, location_priority,
                    acceleration_instrument_codes,
                )
            except Exception:
                previous_raw_tails = {}
        completed += 1
        if completed == 1 or completed % 10 == 0 or completed == len(dates):
            print("AWS pick progress: {}/{} pending dates".format(
                completed, len(dates)
            ))
        current += timedelta(days=1)
    if failures:
        raise RuntimeError("AWS pick dates failed: {}".format(failures))
