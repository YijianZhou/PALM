"""Persistent PAL workers consuming a sorted queue of unfinished dates."""
import contextlib
from datetime import timedelta
import multiprocessing as mp
import os
from pathlib import Path
from numbers import Integral
import queue
import traceback


def date_blocks(start, end, workers):
    days = (end - start).days
    count = min(max(1, int(workers)), days)
    if count <= 0:
        return []
    size, extra = divmod(days, count)
    blocks = []
    for index in range(count):
        stop = start + timedelta(days=size + (index < extra))
        blocks.append((start, stop))
        start = stop
    return blocks


def _worker(index, interval, data_dir, station_file, pick_dir, log_path,
            config_factory, overwrite, messages, threads_per_worker, tasks):
    with open(log_path, "w", encoding="utf-8", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                from run_pick import run_pick
                def progress(*args):
                    messages.put(("progress", index, args))
                # Environment limits apply at spawn; also limit loaded BLAS pools.
                try:
                    from threadpoolctl import threadpool_limits
                    limits = threadpool_limits(limits=threads_per_worker)
                except ImportError:
                    limits = contextlib.nullcontext()
                with limits:
                    def assigned_dates():
                        while True:
                            day = tasks.get()
                            if day is None:
                                return
                            yield day
                    run_pick(interval, data_dir, station_file, pick_dir,
                             config_factory(), overwrite=overwrite,
                             num_workers=1, progress_callback=progress,
                             _date_tasks=assigned_dates())
                messages.put(("done", index, None))
            except BaseException as exc:
                traceback.print_exc(file=log)
                log.flush()
                messages.put(("error", index, str(exc)))
                raise


def run_blocks(start, end, workers, data_dir, station_file, pick_dir, log_dir,
               config_factory, overwrite, progress, threads_per_worker=1):
    for name, value in [('workers', workers), ('threads_per_worker', threads_per_worker)]:
        if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
            raise ValueError('{} must be a positive integer'.format(name))
    from run_pick import pick_is_complete
    cfg = config_factory()
    buffer_sec = float(getattr(cfg, 'data_buffer_sec', 0.0))
    pending = []
    for offset in range((end-start).days):
        day = start + timedelta(days=offset)
        if not overwrite and pick_is_complete(pick_dir, day, buffer_sec):
            progress.update('skipped', str(day), 0, None)
        else:
            pending.append(day.isoformat())
    print('PAL picking queue: pending dates={} | skipped={} | effective workers={}'.format(
        len(pending), (end-start).days-len(pending), min(int(workers), len(pending))), flush=True)
    if not pending:
        return
    worker_count = min(int(workers), len(pending))
    interval = start.strftime('%Y%m%d') + '-' + end.strftime('%Y%m%d')
    context = mp.get_context("spawn")
    messages = context.Queue()
    tasks = context.Queue()
    processes = []
    logs = {}
    variables = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")
    saved = {name: os.environ.get(name) for name in variables}
    try:
        for day in pending:
            tasks.put(day)
        for _ in range(worker_count):
            tasks.put(None)
        try:
            for name in variables:
                os.environ[name] = str(threads_per_worker)
            for index in range(1, worker_count + 1):
                log = str(Path(log_dir) / ("pick_{}_worker{}.log".format(interval, index)))
                logs[index] = log
                print("PAL worker {}: pending-date queue | log {}".format(index, log), flush=True)
                process = context.Process(target=_worker, args=(
                    index, interval, str(Path(data_dir).resolve()),
                    str(Path(station_file).resolve()), str(pick_dir), log,
                    config_factory, bool(overwrite), messages, threads_per_worker, tasks))
                process.start()
                processes.append(process)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        done = set()
        while len(done) < len(processes):
            try:
                kind, index, payload = messages.get(timeout=1)
            except queue.Empty:
                for index, process in enumerate(processes, 1):
                    if process.exitcode is not None and index not in done:
                        raise RuntimeError("PAL worker {} exited without completion ({}); see {}".format(
                            index, process.exitcode, logs[index]))
                continue
            if kind == "progress":
                state, day, stations, total = payload
                progress.update(state, "worker{}:{}".format(index, day), stations, total)
            elif kind == "done":
                done.add(index)
            else:
                raise RuntimeError("PAL worker {} failed: {}; see {}".format(index, payload, logs[index]))
        for process in processes:
            process.join()
            if process.exitcode != 0:
                raise RuntimeError("PAL worker failed with exit code {}".format(process.exitcode))
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join()
        messages.close()
        messages.join_thread()
        tasks.cancel_join_thread()
        tasks.close()
