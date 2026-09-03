"""Parallel runner for local and SCEDC AWS daily PAL picking."""

import contextlib
import importlib
import multiprocessing
import traceback
from datetime import datetime, timedelta
from pathlib import Path


_AWS_PICK_CONTEXT = None


def parse_date_range(time_range):
    start_text, end_text = time_range.split("-")
    start = datetime.strptime(start_text, "%Y%m%d").date()
    end = datetime.strptime(end_text, "%Y%m%d").date()
    if start >= end:
        raise ValueError("time_range must have start < exclusive end")
    return start, end


def _split_ranges(start, end, worker_count):
    num_days = (end - start).days
    worker_count = min(max(1, worker_count), num_days)
    base_days, remainder = divmod(num_days, worker_count)
    ranges = []
    current = start
    for worker_index in range(worker_count):
        days = base_days + (1 if worker_index < remainder else 0)
        next_date = current + timedelta(days=days)
        ranges.append((current, next_date))
        current = next_date
    return ranges


def _pick_worker(task):
    from run_pick import run_pick

    (
        worker_range,
        data_dir,
        station_file,
        pick_dir,
        config_module,
        config_class,
        overwrite,
        log_path,
    ) = task
    cfg = getattr(importlib.import_module(config_module), config_class)()
    with Path(log_path).open("w", encoding="utf-8") as log_fp:
        with contextlib.redirect_stdout(log_fp), contextlib.redirect_stderr(log_fp):
            run_pick(
                worker_range,
                data_dir,
                station_file,
                pick_dir,
                cfg,
                overwrite=overwrite,
            )


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
    """Write daily picks, optionally including one day beyond each boundary."""
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
    tasks = []
    for index, (worker_start, worker_end) in enumerate(
        _split_ranges(start, end, num_workers), start=1
    ):
        worker_range = "{}-{}".format(
            worker_start.strftime("%Y%m%d"),
            worker_end.strftime("%Y%m%d"),
        )
        log_path = log_dir / "pick_worker_{}_{}.log".format(index, worker_range)
        print("launch pick worker {}: {} -> {}".format(index, worker_range, log_path))
        tasks.append((
            worker_range,
            str(Path(data_dir).resolve()),
            str(Path(station_file).resolve()),
            str(pick_dir),
            config_factory.__module__,
            config_factory.__name__,
            bool(overwrite),
            str(log_path),
        ))

    with multiprocessing.Pool(processes=len(tasks)) as pool:
        results = [pool.apply_async(_pick_worker, (task,)) for task in tasks]
        failures = []
        for index, result in enumerate(results, start=1):
            try:
                result.get()
                print("pick worker {} completed".format(index))
            except Exception as exc:
                failures.append((index, repr(exc)))
    if failures:
        raise RuntimeError("pick workers failed: {}".format(failures))

def _init_aws_pick_worker(settings):
    """Build one reusable picker and S3 client in each worker process."""
    global _AWS_PICK_CONTEXT
    from data_pipeline_aws import build_s3_client
    from run_pick_aws import build_picker, process_day

    cfg = getattr(
        importlib.import_module(settings["config_module"]),
        settings["config_class"],
    )()
    picker, cfg = build_picker(settings["pal_source_dir"], cfg)
    _AWS_PICK_CONTEXT = {
        **settings,
        "cfg": cfg,
        "picker": picker,
        "process_day": process_day,
        "s3_client": build_s3_client(settings["region"], settings["access_mode"]),
    }


def _aws_pick_day_worker(observed_date):
    """Process one date from the dynamic queue using process-local resources."""
    context = _AWS_PICK_CONTEXT
    if context is None:
        raise RuntimeError("AWS pick worker was not initialized")
    log_path = Path(context["log_dir"]) / (
        "pick_day_{}.log".format(observed_date.isoformat())
    )
    try:
        with log_path.open("w", encoding="utf-8") as log_fp:
            with contextlib.redirect_stdout(log_fp), contextlib.redirect_stderr(log_fp):
                context["process_day"](
                    observed_date,
                    context["station_file"],
                    context["pick_dir"],
                    context["picker"],
                    context["cfg"],
                    context["s3_client"],
                    context["bucket"],
                    context["root_prefix"],
                    context["location_priority"],
                    context["acceleration_instrument_codes"],
                    context["overwrite"],
                    context["retry_failed_dates"],
                )
        return observed_date.isoformat(), None
    except Exception as exc:
        with log_path.open("a", encoding="utf-8") as log_fp:
            log_fp.write("\nFATAL DATE ERROR\n")
            log_fp.write(traceback.format_exc())
        return observed_date.isoformat(), repr(exc)


def _pending_aws_dates(start, end, pick_dir, overwrite, retry_failed_dates):
    status_dir = Path(pick_dir).parent / "pick_status"
    pending = []
    current = start
    while current < end:
        stem = current.isoformat()
        done = status_dir / (stem + ".done.json")
        failed = status_dir / (stem + ".failed.json")
        if overwrite or (not done.exists() and (retry_failed_dates or not failed.exists())):
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
    """Dynamically schedule independent AWS picking dates across processes."""
    start, end = parse_date_range(time_range)
    pick_dir = Path(pick_dir).resolve()
    log_dir = Path(log_dir).resolve()
    pick_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    dates = _pending_aws_dates(
        start, end, pick_dir, bool(overwrite), bool(retry_failed_dates)
    )
    total_days = (end - start).days
    skipped_days = total_days - len(dates)
    if not dates:
        print("all {} AWS pick dates are already complete".format(total_days))
        return

    worker_count = min(max(1, int(num_workers)), len(dates))
    print(
        "dynamic daily picking: {} pending, {} skipped, {} workers".format(
            len(dates), skipped_days, worker_count
        )
    )
    settings = {
        "station_file": str(Path(station_file).resolve()),
        "pick_dir": str(pick_dir),
        "log_dir": str(log_dir),
        "pal_source_dir": str(Path(pal_source_dir).expanduser().resolve()),
        "config_module": config_factory.__module__,
        "config_class": config_factory.__name__,
        "bucket": bucket,
        "region": region,
        "root_prefix": root_prefix,
        "access_mode": access_mode,
        "location_priority": tuple(location_priority),
        "acceleration_instrument_codes": tuple(acceleration_instrument_codes),
        "overwrite": bool(overwrite),
        "retry_failed_dates": bool(retry_failed_dates),
    }
    failures = []
    with multiprocessing.Pool(
        processes=worker_count,
        initializer=_init_aws_pick_worker,
        initargs=(settings,),
    ) as pool:
        for completed, (date_text, error) in enumerate(
            pool.imap_unordered(_aws_pick_day_worker, dates, chunksize=1), start=1
        ):
            if error is not None:
                failures.append((date_text, error))
                print("AWS pick date {} failed: {}".format(date_text, error))
            elif completed == 1 or completed % 10 == 0 or completed == len(dates):
                print("AWS pick progress: {}/{} dates".format(completed, len(dates)))
    if failures:
        raise RuntimeError("AWS pick dates failed: {}".format(failures))




