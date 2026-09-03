#!/usr/bin/env python3
"""SCEDC S3 data access and epoch-aware PAL station metadata."""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import boto3
import numpy as np
from botocore import UNSIGNED
from botocore.config import Config as BotoConfig
from obspy import Stream, UTCDateTime, read


WAVEFORM_NAME = re.compile(
    r"^(?P<net>.{2})(?P<sta>.{5})(?P<chn>.{3})(?P<loc>.{2})_?"
    r"(?P<year_day>\d{7})\.ms$"
)
COMPONENT_ORDER = ("E", "N", "Z")
MAX_MSEED_SEGMENTS_PER_COMPONENT = 5000
MAX_MSEED_SAMPLE_COVERAGE_RATIO = 1.10


def _as_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, UTCDateTime):
        return value.date
    text = str(value).strip()
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def _component(channel):
    return {"1": "E", "2": "N"}.get(channel[-1], channel[-1])


def _normalize_location(value):
    value = value.strip("_ ")
    return value if value else "--"


@lru_cache(maxsize=8)
def _load_station_epochs(station_file):
    epochs = defaultdict(list)
    path = Path(station_file).expanduser().resolve()
    with path.open(newline="", encoding="utf-8-sig") as fp:
        for line_number, row in enumerate(csv.reader(fp), start=1):
            if not row or row[0].lstrip().startswith("#"):
                continue
            if len(row) != 9:
                raise ValueError(
                    f"{path}:{line_number}: expected 9 PAL fields, got {len(row)}"
                )
            codes = row[0].strip().split(".")
            if len(codes) != 3:
                raise ValueError(
                    f"{path}:{line_number}: first field must be NET.STA.BAND"
                )
            net, sta, band = codes
            start, end = _as_date(row[7]), _as_date(row[8])
            if start >= end:
                raise ValueError(f"{path}:{line_number}: t0 must be before t1")
            epoch = {
                "net": net,
                "sta": sta,
                "net_sta": f"{net}.{sta}",
                "band": band,
                "latitude": float(row[1]),
                "longitude": float(row[2]),
                "elevation": float(row[3]),
                "gains": tuple(float(value) for value in row[4:7]),
                "start": start,
                "end": end,
            }
            epochs[epoch["net_sta"]].append(epoch)

    for net_sta in epochs:
        epochs[net_sta].sort(key=lambda item: (item["start"], item["end"]))
    return dict(epochs)


def get_sta_dict_aws(station_file, when):
    """Return active NET.STA metadata selected from NET.STA.BAND epochs.

    Intervals use the same half-open convention as the station file: t0 <= day < t1.
    """
    observed_date = _as_date(when)
    active = {}
    for net_sta, epochs in _load_station_epochs(str(Path(station_file).resolve())).items():
        matches = [
            epoch for epoch in epochs
            if epoch["start"] <= observed_date < epoch["end"]
        ]
        if len(matches) > 1:
            descriptions = ", ".join(
                f"{item['band']}:{item['start']}/{item['end']}" for item in matches
            )
            raise ValueError(
                f"overlapping active station epochs for {net_sta} on "
                f"{observed_date}: {descriptions}"
            )
        if matches:
            active[net_sta] = matches[0]
    return active


def to_associator_sta_dict(active_sta_dict):
    """Convert AWS metadata to the list layout expected by PAL's associator."""
    return {
        net_sta: [
            row["latitude"], row["longitude"], row["elevation"], list(row["gains"])
        ]
        for net_sta, row in active_sta_dict.items()
    }


def build_s3_client(region="us-west-2", access_mode="unsigned"):
    config = {"retries": {"max_attempts": 10, "mode": "adaptive"}}
    if access_mode == "unsigned":
        config["signature_version"] = UNSIGNED
    return boto3.client("s3", region_name=region, config=BotoConfig(**config))


def _parse_key(key):
    match = WAVEFORM_NAME.match(key.rsplit("/", 1)[-1])
    if match is None:
        return None
    net = match.group("net").strip("_ ")
    sta = match.group("sta").strip("_ ")
    channel = match.group("chn").strip("_ ")
    if not net or not sta or len(channel) != 3:
        return None
    return {
        "key": key,
        "net": net,
        "sta": sta,
        "net_sta": f"{net}.{sta}",
        "location": _normalize_location(match.group("loc")),
        "channel": channel,
        "band": channel[:2],
        "component": _component(channel),
    }


def _location_rank(location, location_priority):
    if location in location_priority:
        return (0, location_priority.index(location))
    if location != "--":
        return (1, location)
    return (2, location)


def _choose_component_record(records):
    # Lettered orientations are preferred to equivalent numeric orientations.
    return min(records, key=lambda row: (row["channel"][-1] in "12", row["key"]))


def get_data_dict_aws(
    when,
    active_sta_dict,
    s3_client,
    bucket="scedc-pds",
    root_prefix="continuous_waveforms",
    location_priority=("10", "20", "01", "02", "00", "--"),
):
    """List one SCEDC day and select the requested band for active stations.

    Values contain exactly three E/N/Z objects, or one selected object marked
    for three-component expansion. One- and two-component groups use the
    fallback trace selected below.
    """
    observed_date = _as_date(when)
    doy = observed_date.timetuple().tm_yday
    prefix = f"{root_prefix}/{observed_date.year}/{observed_date.year}_{doy:03d}/"
    if len(active_sta_dict) == 1:
        only = next(iter(active_sta_dict.values()))
        network = only["net"].ljust(2, "_")
        station = only["sta"].ljust(5, "_")
        prefix += network + station
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            record = _parse_key(item["Key"])
            if record is None or record["net_sta"] not in active_sta_dict:
                continue
            if record["band"] != active_sta_dict[record["net_sta"]]["band"]:
                continue
            grouped[record["net_sta"]][record["location"]][record["component"]].append(
                record
            )

    selected = {}
    for net_sta, by_location in grouped.items():
        # Match the inventory selector: choose location first, then use that
        # location's selected band. Do not fall through to a different location.
        location = min(
            by_location,
            key=lambda value: _location_rank(value, tuple(location_priority)),
        )
        by_component = by_location[location]
        components = set(by_component)
        if len(components) >= 3:
            assigned = {
                component: _choose_component_record(by_component[component])
                for component in COMPONENT_ORDER if component in by_component
            }
            extras = [
                _choose_component_record(by_component[component])
                for component in sorted(components - set(assigned))
            ]
            for component in COMPONENT_ORDER:
                if component not in assigned:
                    assigned[component] = extras.pop(0)
            selected[net_sta] = [assigned[component] for component in COMPONENT_ORDER]
        elif len(components) in (1, 2):
            # PAL requires E/N/Z. Prefer the vertical trace; when the available
            # traces are both horizontal, repeat the first E/N trace.
            if "Z" in by_component:
                component = "Z"
            else:
                component = min(
                    components,
                    key=lambda value: (
                        COMPONENT_ORDER.index(value)
                        if value in COMPONENT_ORDER else len(COMPONENT_ORDER),
                        value,
                    ),
                )
            selected[net_sta] = [_choose_component_record(by_component[component])]
    return selected


def _interpolate_trace(trace, sampling_rate):
    """Return a trace sampled on the requested rate while preserving timing."""
    sampling_rate = float(sampling_rate)
    if float(trace.stats.sampling_rate) == sampling_rate:
        return trace
    if len(trace) < 2:
        raise ValueError(
            f"cannot interpolate {trace.id} with only {len(trace)} sample(s)"
        )
    trace.data = np.asarray(trace.data, dtype=np.float64)
    trace.interpolate(
        sampling_rate=sampling_rate,
        method="lanczos",
        a=12,
    )
    return trace


def _validate_mseed_fragments(stream, target_rate, source):
    """Reject heavily fragmented or overlapping miniSEED before merging."""
    segment_count = len(stream)
    start_time = min(trace.stats.starttime for trace in stream)
    end_time = max(trace.stats.endtime for trace in stream)
    expected_samples = max(
        1, int(round(float(end_time - start_time) * target_rate)) + 1
    )
    equivalent_samples = sum(
        max(
            1,
            int(round(
                float(trace.stats.endtime - trace.stats.starttime) * target_rate
            )) + 1,
        )
        for trace in stream
    )
    coverage_ratio = equivalent_samples / expected_samples
    if (
        segment_count > MAX_MSEED_SEGMENTS_PER_COMPONENT
        or coverage_ratio > MAX_MSEED_SAMPLE_COVERAGE_RATIO
    ):
        raise ValueError(
            "pathological miniSEED fragmentation/overlap in {}: "
            "{} segments (limit {}), sample coverage ratio {:.3f} "
            "(limit {:.3f})".format(
                source,
                segment_count,
                MAX_MSEED_SEGMENTS_PER_COMPONENT,
                coverage_ratio,
                MAX_MSEED_SAMPLE_COVERAGE_RATIO,
            )
        )

def _read_s3_trace(record, s3_client, bucket):
    body = s3_client.get_object(Bucket=bucket, Key=record["key"])["Body"].read()
    stream = read(io.BytesIO(body), format="MSEED")
    matching = Stream(
        traces=[
            trace for trace in stream
            if trace.stats.network.strip() == record["net"]
            and trace.stats.station.strip() == record["sta"]
            and trace.stats.channel.strip() == record["channel"]
            and _normalize_location(trace.stats.location) == record["location"]
        ]
    )
    if not matching:
        matching = stream
    target_trace = max(
        matching,
        key=lambda trace: float(trace.stats.endtime - trace.stats.starttime),
    )
    target_rate = float(target_trace.stats.sampling_rate)
    _validate_mseed_fragments(
        matching,
        target_rate,
        "s3://{}/{}".format(bucket, record["key"]),
    )
    for trace in matching:
        _interpolate_trace(trace, target_rate)
    matching.merge(method=1, fill_value=0)
    if len(matching) != 1:
        raise ValueError(
            f"expected one merged trace in s3://{bucket}/{record['key']}, "
            f"found {len(matching)}"
        )
    return matching[0]


def read_data_aws(
    records,
    station_metadata,
    s3_client,
    bucket="scedc-pds",
    acceleration_instrument_codes=("N",),
    start_time=None,
    end_time=None,
):
    """Merge adjacent daily S3 components and convert counts to velocity."""
    if not records:
        return Stream()

    by_component = defaultdict(list)
    for record in records:
        trace = _read_s3_trace(record, s3_client, bucket)
        if start_time is not None or end_time is not None:
            trace.trim(
                UTCDateTime(start_time) if start_time is not None else trace.stats.starttime,
                UTCDateTime(end_time) if end_time is not None else trace.stats.endtime,
                nearest_sample=True,
            )
        if len(trace):
            by_component[record["component"]].append(trace)
    if not by_component:
        return Stream()

    if all(component in by_component for component in COMPONENT_ORDER):
        source_components = list(COMPONENT_ORDER)
    else:
        source_component = "Z" if "Z" in by_component else sorted(by_component)[0]
        source_components = [source_component]

    merged_components = {}
    for component in source_components:
        traces = by_component[component]
        target_rate = float(np.median([trace.stats.sampling_rate for trace in traces]))
        for trace in traces:
            _interpolate_trace(trace, target_rate)
        component_stream = Stream(traces=traces)
        component_stream.merge(method=1, fill_value=0)
        if len(component_stream) != 1:
            raise ValueError(
                "expected one merged {} component for {}, found {}".format(
                    component, station_metadata["net_sta"], len(component_stream)
                )
            )
        merged_components[component] = component_stream[0]

    if len(source_components) == 1:
        source_component = source_components[0]
        gain_index = (
            COMPONENT_ORDER.index(source_component)
            if source_component in COMPONENT_ORDER else 2
        )
        assignments = [
            (component, merged_components[source_component].copy(), gain_index)
            for component in COMPONENT_ORDER
        ]
    else:
        assignments = [
            (component, merged_components[component], COMPONENT_ORDER.index(component))
            for component in COMPONENT_ORDER
        ]

    output = Stream()
    for component, trace, gain_index in assignments:
        gain = station_metadata["gains"][gain_index]
        if not np.isfinite(gain) or gain == 0:
            raise ValueError(
                "invalid gain for {}: {}".format(station_metadata["net_sta"], gain)
            )
        trace.data = np.asarray(trace.data, dtype=np.float64) / gain
        if station_metadata["band"][1:2] in acceleration_instrument_codes:
            trace.detrend("demean").detrend("linear")
            trace.integrate(method="cumtrapz")
            trace.detrend("linear")
        trace.stats.network = station_metadata["net"]
        trace.stats.station = station_metadata["sta"]
        trace.stats.channel = station_metadata["band"] + component
        output += trace
    return output

def get_pal_picks(date_value, pick_dir):
    """Read PAL pick output while retaining its NET.STA identifier."""
    dtype = [
        ("net_sta", "O"), ("sta_ot", "O"), ("tp", "O"),
        ("ts", "O"), ("s_amp", "O"),
    ]
    path = Path(pick_dir) / f"{_as_date(date_value).isoformat()}.pick"
    if not path.exists():
        return np.array([], dtype=dtype)
    picks = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            values = line.rstrip("\n").split(",")
            if len(values) < 5:
                continue
            picks.append(
                (values[0], UTCDateTime(values[1]), UTCDateTime(values[2]),
                 UTCDateTime(values[3]), float(values[4]))
            )
    return np.array(picks, dtype=dtype)
