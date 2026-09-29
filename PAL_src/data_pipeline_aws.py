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
from station_inventory import read_inventory, selection_rank, calibrate_trace, CHANNEL_PRIORITY


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
    return {"1": "E", "2": "N", "3": "Z"}.get(channel[-1], channel[-1])


def _normalize_location(value):
    value = value.strip("_ ")
    return value if value else "--"


@lru_cache(maxsize=8)
def _load_station_epochs(station_file):
    return read_inventory(station_file)



def get_sta_dict_aws(station_file, when):
    """Return station identities with all metadata alternatives active that day."""
    day = float(UTCDateTime(str(_as_date(when))))
    active = {}
    for net_sta, info in _load_station_epochs(str(Path(station_file).resolve())).items():
        epochs = [e for e in info[3]['epochs'] if e['start'] < day + 86400 and e['end'] > day]
        if not epochs:
            continue
        first = epochs[0]
        net, sta = net_sta.split('.')
        active[net_sta] = dict(net=net, sta=sta, net_sta=net_sta,
            band=first['band'], latitude=info[0], longitude=info[1], elevation=info[2],
            gains=first['gains'], start=day, end=day + 86400,
            inventory=info[3])
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
    channel_priority=CHANNEL_PRIORITY,
    station_selection_order="channel_first",
):
    """List one SCEDC day and rank available band/location combinations.

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
            grouped[record["net_sta"]][(record["band"], record["location"])][record["component"]].append(
                record
            )

    selected = {}
    for net_sta, by_location in grouped.items():
        # Selection is independent of the preferred band in legacy metadata.
        location = min(
            by_location,
            key=lambda value: selection_rank(value[0], value[1], channel_priority,
                                               location_priority, station_selection_order),
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

def _read_s3_trace(record, s3_client, bucket, inventory=None,
                   start_time=None, end_time=None):
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
    if start_time is not None or end_time is not None:
        matching.trim(
            UTCDateTime(start_time) if start_time is not None else None,
            UTCDateTime(end_time) if end_time is not None else None,
            nearest_sample=True,
        )
    if not matching:
        return None
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
        if inventory is not None:
            calibrate_trace(trace, inventory, allow_fallback=True)
        _interpolate_trace(trace, target_rate)
    gain_missing = any(tr.stats.get('gain_missing', False) for tr in matching)
    matching.merge(method=1, fill_value=0)
    if gain_missing:
        for trace in matching:
            trace.stats.gain_missing = True
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
    to_clean=None,
    *, to_prep=None,
):
    """Merge adjacent daily S3 components and convert counts to velocity."""
    if to_clean is None:
        to_clean = True if to_prep is None else to_prep
    if not to_clean:
        raise ValueError("raw SCEDC waveform objects require to_clean=True")
    if not records:
        return Stream()

    by_component = defaultdict(list)
    for record in records:
        trace = _read_s3_trace(record, s3_client, bucket,
            inventory=station_metadata.get("inventory"),
            start_time=start_time, end_time=end_time)
        if trace is None:
            continue
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
        if "inventory" not in station_metadata:
            trace.data = np.asarray(trace.data, dtype=np.float64) / gain
        if trace.stats.channel[1:2] in acceleration_instrument_codes:
            trace.detrend("demean").detrend("linear")
            trace.integrate(method="cumtrapz")
            trace.detrend("linear")
        trace.stats.network = station_metadata["net"]
        trace.stats.station = station_metadata["sta"]
        trace.stats.channel = trace.stats.channel[:2] + component
        output += trace
    return output

def get_pal_picks(date_value, pick_dir, vp=5.9, vs=3.45):
    """Read PAL picks and derive PAL's rough origin time in memory."""
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
            values = [value.strip() for value in line.rstrip("\n").split(",")]
            if len(values) < 4:
                continue
            try:
                tp, ts = UTCDateTime(values[1]), UTCDateTime(values[2])
                s_amp = float(values[3])
                distance = (
                    (ts - tp) / (1.0 / float(vs) - 1.0 / float(vp))
                )
                sta_ot = tp - distance / float(vp)
            except (TypeError, ValueError):
                if len(values) < 5:
                    continue
                sta_ot = UTCDateTime(values[1])
                tp, ts = UTCDateTime(values[2]), UTCDateTime(values[3])
                s_amp = float(values[4])
            picks.append((values[0], sta_ot, tp, ts, s_amp))
    return np.array(picks, dtype=dtype)
