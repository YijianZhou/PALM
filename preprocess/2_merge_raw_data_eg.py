#!/usr/bin/env python3
"""Validate and merge raw miniSEED into an AI-PAL daily waveform archive."""

import csv
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from obspy import Stream, read

from preprocess_common import (
    active_epochs,
    compact_date,
    component_code,
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
CLEAN_ROOT = Path("/data/ai_pal_%s_daily" % CASE_CODE)
TIME_RANGE = "20190704-20190707"  # Exclusive end date.
LOCATION_PRIORITY = ("", "00", "10", "20", "01", "02")
MAX_MSEED_SEGMENTS_PER_COMPONENT = 5000
MAX_MSEED_SAMPLE_COVERAGE_RATIO = 1.10
NUM_WORKERS = 4
OVERWRITE = False


def location_rank(location, selected_locations):
    if location in selected_locations:
        return selected_locations.index(location)
    if location in LOCATION_PRIORITY:
        return len(selected_locations) + LOCATION_PRIORITY.index(location)
    return len(selected_locations) + len(LOCATION_PRIORITY)


def channel_rank(channel):
    component = channel[-1].upper()
    order = {"E": 0, "N": 0, "Z": 0, "1": 1, "2": 1, "3": 1}
    return order.get(component, 2), channel


def interpolate_trace(trace, sampling_rate):
    sampling_rate = float(sampling_rate)
    if float(trace.stats.sampling_rate) == sampling_rate:
        return trace
    if len(trace) < 2:
        raise ValueError("cannot interpolate {} with fewer than 2 samples".format(trace.id))
    trace.data = np.asarray(trace.data, dtype=np.float64)
    trace.interpolate(sampling_rate=sampling_rate, method="lanczos", a=12)
    return trace


def validate_fragments(stream, target_rate, source):
    """Reject the same pathological fragmentation/overlap caught by AWS PAL."""
    if not stream:
        raise ValueError("empty stream: {}".format(source))
    start_time = min(trace.stats.starttime for trace in stream)
    end_time = max(trace.stats.endtime for trace in stream)
    expected_samples = max(1, int(round(float(end_time - start_time) * target_rate)) + 1)
    equivalent_samples = sum(
        max(
            1,
            int(round(float(trace.stats.endtime - trace.stats.starttime) * target_rate)) + 1,
        )
        for trace in stream
    )
    coverage_ratio = equivalent_samples / expected_samples
    if len(stream) > MAX_MSEED_SEGMENTS_PER_COMPONENT:
        raise ValueError(
            "{} segments exceeds limit {}".format(
                len(stream), MAX_MSEED_SEGMENTS_PER_COMPONENT
            )
        )
    if coverage_ratio > MAX_MSEED_SAMPLE_COVERAGE_RATIO:
        raise ValueError(
            "sample coverage ratio {:.3f} exceeds limit {:.3f}".format(
                coverage_ratio, MAX_MSEED_SAMPLE_COVERAGE_RATIO
            )
        )
    return coverage_ratio


def active_selectors(station_epochs, day):
    selectors = defaultdict(list)
    for epoch in active_epochs(station_epochs, day, day + 86400):
        key = epoch["net"], epoch["sta"], epoch["band"]
        location = normalized_location(epoch["location"])
        if location not in selectors[key]:
            selectors[key].append(location)
    return selectors


def index_raw_day(day_dir, selectors):
    """Index files by selected station, band, component, location, and channel."""
    indexed = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    errors = []
    for path in sorted(day_dir.glob("*.mseed")):
        try:
            stream = read(str(path), headonly=True)
        except Exception as exc:
            errors.append((path, "header read failed: {}".format(exc)))
            continue
        for trace in stream:
            net, sta = trace.stats.network, trace.stats.station
            channel = trace.stats.channel
            matching_bands = [
                band for key_net, key_sta, band in selectors
                if key_net == net and key_sta == sta and channel.startswith(band)
            ]
            for band in matching_bands:
                component = component_code(channel)
                if component not in {"E", "N", "Z"}:
                    continue
                key = net, sta, band, component
                location = normalized_location(trace.stats.location)
                indexed[key][location][channel].add(path)
    return indexed, errors


def read_candidate(paths, net, sta, location, channel, day):
    stream = Stream()
    for path in sorted(paths):
        loaded = read(str(path), starttime=day, endtime=day + 86400)
        stream += loaded.select(
            network=net, station=sta, location=location, channel=channel
        )
    if not stream:
        raise ValueError("no matching traces")
    segment_count = len(stream)
    target_trace = max(stream, key=lambda trace: float(trace.stats.endtime - trace.stats.starttime))
    target_rate = float(target_trace.stats.sampling_rate)
    coverage_ratio = validate_fragments(stream, target_rate, ", ".join(map(str, paths)))
    for trace in stream:
        interpolate_trace(trace, target_rate)
    stream.merge(method=1, fill_value=0)
    if len(stream) != 1:
        raise ValueError("expected one merged trace, found {}".format(len(stream)))
    trace = stream[0]
    day_last_sample = day + 86400 - 1.0 / target_rate
    trace.trim(day, day_last_sample, nearest_sample=True)
    if not len(trace):
        raise ValueError("merged trace is empty after daily trim")
    if not np.all(np.isfinite(trace.data)):
        raise ValueError("merged trace contains NaN or infinite samples")
    return trace, target_rate, coverage_ratio, len(paths), segment_count


def clean_component(day, output_dir, key, candidates, selected_locations):
    net, sta, band, component = key
    failures = []
    ordered_locations = sorted(
        candidates, key=lambda value: location_rank(value, selected_locations)
    )
    for location in ordered_locations:
        channels = sorted(candidates[location], key=channel_rank)
        for channel in channels:
            paths = candidates[location][channel]
            try:
                trace, rate, ratio, raw_files, segment_count = read_candidate(
                    paths, net, sta, location, channel, day
                )
                canonical_channel = band + component
                trace.stats.channel = canonical_channel
                location_name = location if location else "--"
                output = output_dir / "{}.{}.{}.{}.mseed".format(
                    net, sta, location_name, canonical_channel
                )
                if output.exists() and not OVERWRITE:
                    status = "existing"
                else:
                    partial = output.with_suffix(output.suffix + ".partial")
                    Stream(traces=[trace]).write(str(partial), format="MSEED")
                    os.replace(partial, output)
                    status = "written"
                return {
                    "date": compact_date(day),
                    "net_sta": "{}.{}".format(net, sta),
                    "band": band,
                    "component": component,
                    "location": location_name,
                    "source_channel": channel,
                    "status": status,
                    "raw_files": raw_files,
                    "segments": segment_count,
                    "coverage_ratio": "{:.4f}".format(ratio),
                    "sampling_rate": "{:.6g}".format(rate),
                    "output": str(output),
                    "error": " | ".join(failures),
                }
            except Exception as exc:
                failures.append("{} {}: {}".format(
                    location if location else "--", channel, exc
                ))
    return {
        "date": compact_date(day),
        "net_sta": "{}.{}".format(net, sta),
        "band": band,
        "component": component,
        "location": "",
        "source_channel": "",
        "status": "rejected",
        "raw_files": sum(
            len(paths) for channels in candidates.values() for paths in channels.values()
        ),
        "segments": "",
        "coverage_ratio": "",
        "sampling_rate": "",
        "output": "",
        "error": " | ".join(failures) if failures else "no candidate stream",
    }


def missing_component_row(day, key):
    net, sta, band, component = key
    return {
        "date": compact_date(day),
        "net_sta": "{}.{}".format(net, sta),
        "band": band,
        "component": component,
        "location": "",
        "source_channel": "",
        "status": "missing",
        "raw_files": 0,
        "segments": "",
        "coverage_ratio": "",
        "sampling_rate": "",
        "output": "",
        "error": "no raw candidate for expected component",
    }


REPORT_FIELDS = (
    "date", "net_sta", "band", "component", "location", "source_channel",
    "status", "raw_files", "segments", "coverage_ratio", "sampling_rate",
    "output", "error",
)


def write_report(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    if NUM_WORKERS < 1:
        raise ValueError("NUM_WORKERS must be positive")
    station_epochs = read_station_epochs(STATION_FILE)
    raw_root = resolve_path(RAW_ROOT)
    clean_root = resolve_path(CLEAN_ROOT)
    report_rows = []

    for day in iter_days(TIME_RANGE):
        day_code = compact_date(day)
        raw_dir = raw_root / day_code
        output_dir = clean_root / day_code
        output_dir.mkdir(parents=True, exist_ok=True)
        selectors = active_selectors(station_epochs, day)
        indexed, header_errors = index_raw_day(raw_dir, selectors)
        expected_keys = {
            (net, sta, band, component)
            for net, sta, band in selectors
            for component in ("E", "N", "Z")
        }
        for key in sorted(expected_keys - set(indexed)):
            report_rows.append(missing_component_row(day, key))
        for path, message in header_errors:
            report_rows.append({
                "date": day_code, "net_sta": "", "band": "", "component": "",
                "location": "", "source_channel": "", "status": "rejected_file",
                "raw_files": 1, "segments": "", "coverage_ratio": "",
                "sampling_rate": "", "output": "", "error": "{}: {}".format(path, message),
            })
        print("{}: cleaning {} station-components".format(day_code, len(indexed)))
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            futures = {
                executor.submit(
                    clean_component, day, output_dir, key, candidates, selectors[key[:3]]
                ): key
                for key, candidates in sorted(indexed.items())
            }
            for future in as_completed(futures):
                row = future.result()
                report_rows.append(row)
                print("{} {} {}: {}".format(
                    day_code, row["net_sta"], row["component"], row["status"]
                ))

    report_rows.sort(key=lambda row: (
        row["date"], row["net_sta"], row["band"], row["component"]
    ))
    report = resolve_path(Path("output/merge_%s_report.csv" % CASE_CODE))
    write_report(report, report_rows)
    rejected = sum(str(row["status"]).startswith("rejected") for row in report_rows)
    print("wrote {} rows to {}; {} rejected".format(len(report_rows), report, rejected))


if __name__ == "__main__":
    main()
