#!/usr/bin/env python3
"""Download daily raw miniSEED for stations selected by a PAL station CSV."""

import csv
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from obspy import Stream
from obspy.clients.fdsn import Client

from preprocess_common import (
    active_epochs,
    compact_date,
    iter_days,
    normalized_location,
    read_station_epochs,
    resolve_path,
)


# ============================================================================
# USER SETTINGS
# ============================================================================
CASE_CODE = "eg"
STATION_FILE = Path("output/station_%s.csv" % CASE_CODE)
RAW_ROOT = Path("/data/ai_pal_%s_raw" % CASE_CODE)
TIME_RANGE = "20190704-20190707"  # Exclusive end date.
PROVIDERS = ("IRIS", "SCEDC", "NCEDC")
NUM_WORKERS = 5
REQUEST_TIMEOUT_SEC = 120
OVERWRITE = False


_thread_state = threading.local()


def get_client(provider):
    clients = getattr(_thread_state, "clients", None)
    if clients is None:
        clients = {}
        _thread_state.clients = clients
    if provider not in clients:
        clients[provider] = Client(provider, timeout=REQUEST_TIMEOUT_SEC)
    return clients[provider]


def selector_key(epoch):
    return epoch["net"], epoch["sta"], epoch["band"], epoch["location"]


def write_raw_stream(stream, day_dir, day_code, overwrite=False):
    written = []
    trace_ids = sorted({
        (
            trace.stats.network,
            trace.stats.station,
            normalized_location(trace.stats.location),
            trace.stats.channel,
        )
        for trace in stream
    })
    for net, sta, location, channel in trace_ids:
        selected = stream.select(
            network=net, station=sta, location=location, channel=channel
        )
        if not selected:
            continue
        location_name = location if location else "--"
        output = day_dir / "{}.{}.{}.{}__{}.mseed".format(
            net, sta, location_name, channel, day_code
        )
        if output.exists() and not overwrite:
            written.append(str(output))
            continue
        partial = output.with_suffix(output.suffix + ".part")
        selected.write(str(partial), format="MSEED")
        os.replace(partial, output)
        written.append(str(output))
    return written


def download_selector(day, epoch, day_dir):
    location = normalized_location(epoch["location"])
    request = (
        epoch["net"], epoch["sta"], location,
        epoch["band"] + "*", day, day + 86400,
    )
    failures = []
    for provider in PROVIDERS:
        try:
            stream = get_client(provider).get_waveforms(*request)
            stream = Stream(traces=[
                trace for trace in stream
                if trace.stats.network == epoch["net"]
                and trace.stats.station == epoch["sta"]
                and trace.stats.channel.startswith(epoch["band"])
                and normalized_location(trace.stats.location) == location
            ])
            if not stream:
                raise RuntimeError("empty matching stream")
            paths = write_raw_stream(
                stream, day_dir, compact_date(day), overwrite=OVERWRITE
            )
            if paths:
                return {
                    "status": "downloaded",
                    "provider": provider,
                    "selector": ".".join(selector_key(epoch)),
                    "files": len(paths),
                    "message": "",
                }
        except Exception as exc:
            failures.append("{}: {}".format(provider, exc))
    return {
        "status": "failed",
        "provider": "",
        "selector": ".".join(selector_key(epoch)),
        "files": 0,
        "message": " | ".join(failures),
    }


def write_report(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ("date", "selector", "status", "provider", "files", "message")
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_report(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def main():
    if NUM_WORKERS < 1:
        raise ValueError("NUM_WORKERS must be positive")
    station_epochs = read_station_epochs(STATION_FILE)
    raw_root = resolve_path(RAW_ROOT)
    raw_root.mkdir(parents=True, exist_ok=True)
    all_rows = []

    for day in iter_days(TIME_RANGE):
        day_code = compact_date(day)
        day_dir = raw_root / day_code
        day_dir.mkdir(parents=True, exist_ok=True)
        done_path = day_dir / "download_complete.json"
        failed_path = day_dir / "download_incomplete.json"
        day_report = day_dir / "download_report.csv"
        if done_path.exists() and not OVERWRITE:
            print("skip completed day {}".format(day_code))
            all_rows.extend(read_report(day_report))
            continue
        epochs = {
            selector_key(epoch): epoch
            for epoch in active_epochs(station_epochs, day, day + 86400)
        }
        print("{}: downloading {} station selectors".format(day_code, len(epochs)))
        rows = []
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            futures = [
                executor.submit(download_selector, day, epoch, day_dir)
                for _, epoch in sorted(epochs.items())
            ]
            for future in as_completed(futures):
                result = future.result()
                result["date"] = day_code
                rows.append(result)
                print("{} {}: {}".format(
                    day_code, result["selector"], result["status"]
                ))
        rows.sort(key=lambda row: row["selector"])
        all_rows.extend(rows)
        failed = sum(row["status"] == "failed" for row in rows)
        write_report(day_report, rows)
        status_path = failed_path if failed else done_path
        stale_path = done_path if failed else failed_path
        status_path.write_text(json.dumps({
            "date": day_code,
            "selectors": len(rows),
            "failed": failed,
        }, indent=2) + "\n", encoding="utf-8")
        if stale_path.exists():
            stale_path.unlink()

    report = resolve_path(Path("output/download_%s_report.csv" % CASE_CODE))
    write_report(report, all_rows)
    print("wrote download report: {}".format(report))


if __name__ == "__main__":
    main()
