"""Shared AI-PAL/PAL data I/O and waveform preparation interfaces."""

import csv
import glob
import os
import warnings

import numpy as np
from obspy import read, Stream, UTCDateTime


_WARNED_GAIN_FALLBACKS = set()
COMPONENT_ORDER = ("E", "N", "Z")
DEFAULT_LOCATION_PRIORITY = ("10", "20", "01", "02", "00", "")
DEFAULT_CHANNEL_PRIORITY = ("HH", "BH", "EH", "HN", "EN", "SH")
MAX_MSEED_SEGMENTS_PER_COMPONENT = 5000
MAX_MSEED_SAMPLE_COVERAGE_RATIO = 1.10


def _is_time(value):
    try:
        UTCDateTime(value)
        return True
    except Exception:
        return False


def _parse_phase_time(value):
    """Return -1.0 for a missing phase, otherwise an absolute UTC time."""
    text = str(value).strip()
    try:
        if float(text) < 0:
            return -1.0
    except ValueError:
        pass
    return UTCDateTime(text)


def _pick_metadata(codes):
    """Parse optional key=value metadata appended to a phase-pick row."""
    metadata = {}
    for code in codes[3:]:
        if "=" not in code:
            continue
        key, value = [item.strip() for item in code.split("=", 1)]
        if not key:
            continue
        if key in {"num_aug", "num_aug_base"}:
            metadata[key] = int(value)
        elif key in {"rarity", "p_norm", "epi_dist_km", "hypo_dist_km"}:
            metadata[key] = float(value)
        else:
            metadata[key] = value
    return metadata


def read_fpha(fpha, include_pick_metadata=False):
    """Read PAL events and picks, optionally retaining tagged pick metadata."""
    event_list = []
    num_pos = 0
    with open(fpha, newline="") as fp:
        for line_number, codes in enumerate(csv.reader(fp), start=1):
            if not codes or not codes[0].strip():
                continue
            first = codes[0].strip()
            if len(codes) >= 5 and _is_time(first):
                ot = UTCDateTime(first)
                lat, lon, dep, mag = [float(code) for code in codes[1:5]]
                event_list.append([[ot, lat, lon, dep, mag], {}])
                continue
            if len(codes) < 3 or not event_list:
                raise ValueError(
                    "{}:{} invalid phase row".format(fpha, line_number)
                )
            net_sta = first
            tp, ts = [_parse_phase_time(code) for code in codes[1:3]]
            if float(tp) < 0 and float(ts) < 0:
                raise ValueError(
                    "{}:{} phase row has neither P nor S".format(
                        fpha, line_number
                    )
                )
            pick = [tp, ts]
            if include_pick_metadata:
                pick.append(_pick_metadata(codes))
            event_list[-1][1][net_sta] = pick
            num_pos += 1
    return event_list, num_pos


def read_fpick(fpick, fpha=None):
    """Count associated/unassociated legacy picks by station and UTC date."""
    associated = set()
    if fpha:
        events, _ = read_fpha(fpha)
        for _, picks in events:
            for net_sta, (tp, ts) in picks.items():
                associated.add((net_sta, round(float(tp), 4), round(float(ts), 4)))

    pick_num_dict = {}
    num_picks = 0
    with open(fpick, newline="") as fp:
        for line_number, codes in enumerate(csv.reader(fp), start=1):
            if not codes or not codes[0].strip():
                continue
            if codes[0].strip().lower() in {"net_sta", "station"}:
                continue
            # PAL: station, station_OT, P, S, ...; AI: station, P, S, ...
            if len(codes) >= 4 and _is_time(codes[3]):
                tp, ts = UTCDateTime(codes[2]), UTCDateTime(codes[3])
            elif len(codes) >= 3:
                tp, ts = UTCDateTime(codes[1]), UTCDateTime(codes[2])
            else:
                raise ValueError(
                    "{}:{} invalid pick row".format(fpick, line_number)
                )
            net_sta = codes[0].strip()
            sta_date = "{}_{}".format(net_sta, tp.date)
            counts = pick_num_dict.setdefault(sta_date, [0, 0])
            key = (net_sta, round(float(tp), 4), round(float(ts), 4))
            counts[1 if key in associated else 0] += 1
            num_picks += 1
    return pick_num_dict, num_picks


def read_assoc_rate(path):
    """Read one consolidated station-date association-rate CSV."""
    if os.path.isdir(path):
        raise IsADirectoryError(
            "expected one concatenated association-rate CSV, got directory: {}".format(
                path
            )
        )
    if not os.path.isfile(path):
        raise FileNotFoundError("association-rate CSV not found: {}".format(path))

    required = {
        "date", "net_sta", "num_picks",
        "num_associated_picks", "num_unassociated_picks",
    }
    pick_num_dict = {}
    num_picks = 0
    with open(path, newline="") as fp:
        rate_reader = csv.DictReader(fp)
        missing = required - set(rate_reader.fieldnames or [])
        if missing:
            raise ValueError(
                "{} missing columns: {}".format(path, ", ".join(sorted(missing)))
            )
        for row in rate_reader:
            # Plain cat can leave repeated daily headers in the combined file.
            if row["date"].strip().lower() == "date":
                continue
            sta_date = "{}_{}".format(
                row["net_sta"].strip(), row["date"].strip()
            )
            num_assoc = int(row["num_associated_picks"])
            num_unassoc = int(row["num_unassociated_picks"])
            declared_total = int(row["num_picks"])
            if num_assoc + num_unassoc != declared_total:
                raise ValueError(
                    "{} inconsistent counts for {}".format(path, sta_date)
                )
            counts = pick_num_dict.setdefault(sta_date, [0, 0])
            counts[0] += num_unassoc
            counts[1] += num_assoc
            num_picks += declared_total
    return pick_num_dict, num_picks


def get_data_dict(date, data_dir, normalize_to_three_channels=True):
    """Return all candidate paths for each station on one UTC date.

    Candidate paths must remain intact until ``read_data`` can inspect their
    trace headers and choose a band, location, and component set. The boolean
    is retained for API compatibility; when false, discovery keeps only
    stations represented by exactly three files.
    """
    data_dict = {}
    date_code = "{:0>4}{:0>2}{:0>2}".format(date.year, date.month, date.day)
    for st_path in sorted(glob.glob(os.path.join(data_dir, date_code, "*"))):
        fname = os.path.basename(st_path)
        net_sta = ".".join(fname.split(".")[0:2])
        data_dict.setdefault(net_sta, []).append(st_path)
    for net_sta, paths in list(data_dict.items()):
        if not normalize_to_three_channels and len(paths) != 3:
            data_dict.pop(net_sta)
    return data_dict


def get_buffered_data_dict(
    date, data_dir, buffer_seconds=60.0, normalize_to_three_channels=True,
):
    """Return station paths for one UTC day plus adjacent-day buffer files."""
    current = get_data_dict(
        date, data_dir, normalize_to_three_channels=normalize_to_three_channels
    )
    if buffer_seconds <= 0:
        return current

    buffered = {net_sta: list(paths) for net_sta, paths in current.items()}
    for day_offset in (-1, 1):
        nearby = get_data_dict(
            UTCDateTime(date) + day_offset * 86400,
            data_dir,
            normalize_to_three_channels=normalize_to_three_channels,
        )
        for net_sta in buffered:
            buffered[net_sta].extend(nearby.get(net_sta, []))
    return buffered


def get_1chn_data(date, data_dir):
    """Compatibility alias for the former single-channel-aware reader."""
    return get_data_dict(date, data_dir, normalize_to_three_channels=True)


def load_station_stream(date, data_dir, net_sta, normalize_to_three_channels=True):
    """Load one local station-day stream for training-sample cutters."""
    stream_paths = get_data_dict(
        date, data_dir, normalize_to_three_channels=normalize_to_three_channels
    ).get(net_sta)
    if not stream_paths:
        return []
    try:
        unique_paths = list(dict.fromkeys(stream_paths))
        stream = read(unique_paths[0])
        for path in unique_paths[1:]:
            stream += read(path)
        stream.merge(fill_value=0)
        if len(stream) != 3 and normalize_to_three_channels:
            stream = _normalize_stream_to_three(stream)
        return stream
    except Exception:
        return []


def is_acceleration_channel(channel):
    """Return whether a SEED channel code records acceleration."""
    channel = str(channel or "").upper()
    return len(channel) >= 2 and channel[1] == "N"


def convert_acc_to_vel(stream):
    """Integrate acceleration in m/s/s to velocity in m/s."""
    for trace in stream:
        if is_acceleration_channel(trace.stats.channel):
            trace.detrend("demean")
            trace.integrate()
    return stream


def _normalize_stream_to_three(stream):
    """Cycle or truncate merged traces into deterministic E/N/Z model inputs."""
    selected = (list(stream) * 3)[:3]
    normalized = Stream()
    for trace, component in zip(selected, "ENZ"):
        normalized_trace = trace.copy()
        prefix = (
            normalized_trace.stats.channel[:-1]
            if normalized_trace.stats.channel else ""
        )
        normalized_trace.stats.channel = prefix + component
        normalized.append(normalized_trace)
    return normalized


def _component_code(channel):
    if not channel:
        return ""
    return {"1": "E", "2": "N", "3": "Z"}.get(
        channel[-1].upper(), channel[-1].upper()
    )


def _normalize_location(value):
    value = str(value or "").strip("_ ")
    return "" if value == "--" else value


def _priority_rank(value, priority):
    values = tuple(_normalize_location(item) for item in priority)
    normalized = _normalize_location(value)
    if normalized in values:
        return 0, values.index(normalized)
    return 1, normalized


def _band_rank(value, priority):
    priority = tuple(str(item).upper() for item in priority)
    value = str(value).upper()
    if value in priority:
        return 0, priority.index(value)
    return 1, value


def _interpolate_trace(trace, sampling_rate):
    sampling_rate = float(sampling_rate)
    if np.isclose(float(trace.stats.sampling_rate), sampling_rate):
        return trace
    if len(trace) < 2:
        raise ValueError(
            "cannot interpolate {} with fewer than two samples".format(trace.id)
        )
    trace.data = np.asarray(trace.data, dtype=np.float64)
    trace.interpolate(sampling_rate=sampling_rate, method="lanczos", a=12)
    return trace


def _validate_mseed_fragments(stream, target_rate, source):
    """Reject the same pathological fragmentation/overlap as the AWS reader."""
    if not stream:
        raise ValueError("empty stream: {}".format(source))
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
        len(stream) > MAX_MSEED_SEGMENTS_PER_COMPONENT
        or coverage_ratio > MAX_MSEED_SAMPLE_COVERAGE_RATIO
    ):
        raise ValueError(
            "pathological miniSEED fragmentation/overlap in {}: {} segments "
            "(limit {}), sample coverage ratio {:.3f} (limit {:.3f})".format(
                source,
                len(stream),
                MAX_MSEED_SEGMENTS_PER_COMPONENT,
                coverage_ratio,
                MAX_MSEED_SAMPLE_COVERAGE_RATIO,
            )
        )


def _read_local_fragments(st_paths, start_time=None, end_time=None):
    read_kwargs = {}
    if start_time is not None:
        read_kwargs["starttime"] = UTCDateTime(start_time)
    if end_time is not None:
        read_kwargs["endtime"] = UTCDateTime(end_time)
    stream = Stream()
    for path in dict.fromkeys(st_paths):
        stream += read(path, **read_kwargs)
    return stream


def prepare_local_stream(
    stream,
    net_sta,
    location_priority=DEFAULT_LOCATION_PRIORITY,
    channel_priority=DEFAULT_CHANNEL_PRIORITY,
    normalize_to_three_channels=True,
):
    """Select and merge one local station stream using the AWS QC contract."""
    grouped = {}
    for trace in stream:
        component = _component_code(trace.stats.channel)
        if component not in COMPONENT_ORDER:
            continue
        band = str(trace.stats.channel)[:2]
        location = _normalize_location(trace.stats.location)
        key = band, location, component, str(trace.stats.channel)
        grouped.setdefault(key, Stream()).append(trace)
    if not grouped:
        return Stream(), []

    group_components = {}
    for band, location, component, _ in grouped:
        group_components.setdefault((band, location), set()).add(component)
    band, location = min(
        group_components,
        key=lambda item: (
            -len(group_components[item]),
            _band_rank(item[0], channel_priority),
            _priority_rank(item[1], location_priority),
        ),
    )

    available = group_components[(band, location)]
    if len(available) >= 3:
        source_components = list(COMPONENT_ORDER)
    elif normalize_to_three_channels and available:
        source = "Z" if "Z" in available else min(
            available, key=lambda item: COMPONENT_ORDER.index(item)
        )
        source_components = [source]
    else:
        return Stream(), []

    merged = {}
    for component in source_components:
        channels = [
            channel for key_band, key_location, key_component, channel in grouped
            if key_band == band and key_location == location
            and key_component == component
        ]
        # Lettered orientations are preferred over equivalent numeric channels.
        channel = min(channels, key=lambda item: (item[-1] in "123", item))
        fragments = grouped[(band, location, component, channel)]
        target = max(
            fragments,
            key=lambda trace: float(trace.stats.endtime - trace.stats.starttime),
        )
        target_rate = float(target.stats.sampling_rate)
        _validate_mseed_fragments(fragments, target_rate, net_sta)
        for trace in fragments:
            _interpolate_trace(trace, target_rate)
        fragments.merge(method=1, fill_value=0)
        if len(fragments) != 1:
            raise ValueError(
                "expected one merged {} component for {}, found {}".format(
                    component, net_sta, len(fragments)
                )
            )
        merged[component] = fragments[0]

    if len(source_components) == 1:
        source = source_components[0]
        assignments = [(component, merged[source].copy(), source) for component in COMPONENT_ORDER]
    else:
        assignments = [(component, merged[component], component) for component in COMPONENT_ORDER]

    output = Stream()
    gain_components = []
    for component, trace, gain_component in assignments:
        trace.stats.network, trace.stats.station = net_sta.split(".", 1)
        trace.stats.location = location
        trace.stats.channel = band + component
        output += trace
        gain_components.append(gain_component)
    return output, gain_components


def select_gain_for_time(gain, when, station=None, warn_fallback=True):
    """Return the active PAL gain, or the epoch nearest to ``when``.

    Time-varying station intervals are interpreted as half-open ``[t0, t1)``.
    A metadata gap is resolved by distance to the nearest interval boundary;
    ties prefer the earlier epoch. This keeps waveform processing alive while
    making imperfect station metadata visible to the operator.
    """
    if isinstance(gain, (float, int, np.floating, np.integer)):
        return float(gain)
    if not gain:
        raise ValueError("empty gain metadata for {}".format(station or "station"))
    if isinstance(gain[0], (float, int, np.floating, np.integer)):
        return [float(value) for value in gain]

    when = UTCDateTime(when)
    epochs = sorted(gain, key=lambda row: UTCDateTime(row[3]))
    for row in epochs:
        start, end = UTCDateTime(row[3]), UTCDateTime(row[4])
        if start <= when < end:
            return [float(value) for value in row[:3]]

    def interval_distance(row):
        start, end = UTCDateTime(row[3]), UTCDateTime(row[4])
        if when < start:
            return float(start - when)
        return float(when - end)

    selected = min(
        enumerate(epochs),
        key=lambda item: (interval_distance(item[1]), item[0]),
    )[1]
    start, end = UTCDateTime(selected[3]), UTCDateTime(selected[4])
    warning_key = (station, str(start), str(end))
    if warn_fallback and warning_key not in _WARNED_GAIN_FALLBACKS:
        _WARNED_GAIN_FALLBACKS.add(warning_key)
        warnings.warn(
            "{} has no gain interval at {}; using nearest interval [{}, {}) "
            "({:.1f} days away)".format(
                station or "station",
                when,
                start,
                end,
                interval_distance(selected) / 86400.0,
            ),
            RuntimeWarning,
            stacklevel=2,
        )
    return [float(value) for value in selected[:3]]


def normalize_station_gain_intervals(
    input_path,
    output_path,
    audit_path=None,
    coverage_start=None,
    coverage_end=None,
    group_by_station=False,
):
    """Write an epoch-aware PAL station file with contiguous gain coverage.

    Internal gaps are divided at their midpoint: the preceding gain is
    extended through the first half and the following gain through the second.
    Optional coverage bounds extend the first/last epoch to the study limits.
    """
    with open(input_path, newline="") as fp:
        rows = [row for row in csv.reader(fp) if row and row[0].strip()]

    def interval_group(selector):
        if not group_by_station:
            return selector
        parts = selector.split(".")
        if len(parts) == 4:
            return ".".join((parts[0], parts[1], parts[3]))
        if len(parts) == 3:
            return ".".join(parts[:2])
        return selector

    grouped = {}
    station_order = []
    passthrough = []
    for line_number, row in enumerate(rows, start=1):
        row = [value.strip() for value in row]
        if len(row) != 9:
            passthrough.append((line_number, row))
            continue
        try:
            start, end = UTCDateTime(row[7]), UTCDateTime(row[8])
        except Exception as exc:
            raise ValueError(
                "{}:{} invalid gain interval: {}".format(
                    input_path, line_number, exc
                )
            ) from exc
        if start >= end:
            raise ValueError(
                "{}:{} gain interval start must precede end".format(
                    input_path, line_number
                )
            )
        station = interval_group(row[0])
        if station not in grouped:
            grouped[station] = []
            station_order.append(station)
        grouped[station].append([row, start, end])

    coverage_start = UTCDateTime(coverage_start) if coverage_start else None
    coverage_end = UTCDateTime(coverage_end) if coverage_end else None
    audit_rows = []
    normalized = []
    for station in station_order:
        epochs = sorted(grouped[station], key=lambda item: item[1])
        if coverage_start is not None and coverage_start < epochs[0][1]:
            old_start = epochs[0][1]
            epochs[0][1] = coverage_start
            audit_rows.append([
                station, "extend_start", str(coverage_start), str(old_start),
                str(coverage_start), str(coverage_start),
            ])
        if coverage_end is not None and coverage_end > epochs[-1][2]:
            old_end = epochs[-1][2]
            epochs[-1][2] = coverage_end
            audit_rows.append([
                station, "extend_end", str(old_end), str(coverage_end),
                str(coverage_end), str(coverage_end),
            ])
        for left, right in zip(epochs, epochs[1:]):
            if left[2] >= right[1]:
                continue
            gap_start, gap_end = left[2], right[1]
            midpoint = gap_start + (gap_end - gap_start) / 2.0
            left[2] = midpoint
            right[1] = midpoint
            audit_rows.append([
                station, "fill_internal_gap", str(gap_start), str(gap_end),
                str(midpoint), str(midpoint),
            ])
        for row, start, end in epochs:
            row[7], row[8] = str(start), str(end)
            normalized.append(row)

    # Non-epoch station formats are valid and need no interval normalization.
    normalized.extend(row for _, row in sorted(passthrough))
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", newline="") as fp:
        csv.writer(fp).writerows(normalized)
    if audit_path:
        os.makedirs(os.path.dirname(os.path.abspath(audit_path)), exist_ok=True)
        with open(audit_path, "w", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow([
                "station", "action", "gap_start", "gap_end",
                "new_left_end", "new_right_start",
            ])
            writer.writerows(audit_rows)
    return {
        "station_count": len(grouped),
        "epoch_count": sum(len(value) for value in grouped.values()),
        "adjustment_count": len(audit_rows),
        "passthrough_row_count": len(passthrough),
    }


def read_data(
    st_paths, sta_dict, start_time=None, end_time=None,
    normalize_to_three_channels=True,
    to_prep=True,
    location_priority=DEFAULT_LOCATION_PRIORITY,
    channel_priority=DEFAULT_CHANNEL_PRIORITY,
):
    """Read local waveforms, optionally select/merge raw traces, and calibrate."""
    if not st_paths:
        return Stream()
    print("reading stream: {}".format(st_paths[0]))
    try:
        stream = _read_local_fragments(st_paths, start_time, end_time)
    except Exception as exc:
        print("bad data read: {}".format(exc))
        return Stream()

    net, sta = os.path.basename(st_paths[0]).split(".")[0:2]
    net_sta = "{}.{}".format(net, sta)
    for trace in stream:
        trace.stats.network, trace.stats.station = net, sta
    gain_components = list(COMPONENT_ORDER)
    if to_prep:
        try:
            stream, gain_components = prepare_local_stream(
                stream,
                net_sta,
                location_priority=location_priority,
                channel_priority=channel_priority,
                normalize_to_three_channels=normalize_to_three_channels,
            )
        except Exception as exc:
            print("bad data preparation for {}: {}".format(net_sta, exc))
            return Stream()
    else:
        # Prepared archives must already contain one unambiguous trace per
        # component. Merging here only joins adjacent-day pieces of that trace.
        try:
            stream.merge(method=1, fill_value=0)
        except Exception as exc:
            print("bad prepared-data merge for {}: {}".format(net_sta, exc))
            return Stream()
        by_component = {
            _component_code(trace.stats.channel): trace for trace in stream
        }
        if len(stream) == 3 and all(
            component in by_component for component in COMPONENT_ORDER
        ):
            stream = Stream([
                by_component[component] for component in COMPONENT_ORDER
            ])
        else:
            stream = Stream()
    if len(stream) != 3:
        print("bad channel count for {} after merge: {}".format(net_sta, len(stream)))
        return Stream()

    gain = sta_dict[net_sta][3]
    common_start = max(trace.stats.starttime for trace in stream)
    common_end = min(trace.stats.endtime for trace in stream)
    if common_end <= common_start:
        return []
    stream.trim(common_start, common_end, nearest_sample=True)
    stream_time = common_start + (common_end - common_start) / 2

    if isinstance(gain, float):
        for trace in stream:
            trace.data = trace.data / gain
    elif isinstance(gain[0], float):
        component_gains = dict(zip(COMPONENT_ORDER, (list(gain) * 3)[:3]))
        for trace, component in zip(stream, gain_components):
            trace.data = trace.data / component_gains[component]
    elif isinstance(gain[0], list):
        selected_gain = select_gain_for_time(gain, stream_time, station=net_sta)
        component_gains = dict(zip(COMPONENT_ORDER, selected_gain))
        for trace, component in zip(stream, gain_components):
            trace.data = trace.data / component_gains[component]
    return convert_acc_to_vel(stream)


def preprocess_picker_stream(
    stream,
    num_channels=3,
    sampling_rate=100.0,
    min_length_sec=25.0,
    frequency_band=(1.0, 20.0),
    taper_max_length_sec=10.0,
    max_gap_sec=5.0,
    to_filter=True,
    retain_raw=True,
):
    """Apply shared signal preparation and optional frequency filtering once."""
    if len(stream) != num_channels:
        return [], []
    start_time = max(trace.stats.starttime for trace in stream)
    end_time = min(trace.stats.endtime for trace in stream)
    if end_time < start_time + min_length_sec:
        return [], []
    stream = stream.slice(start_time, end_time, nearest_sample=True)
    if len(stream) != num_channels:
        return [], []

    for trace in stream:
        trace.data[np.isnan(trace.data)] = 0
        trace.data[np.isinf(trace.data)] = 0
    if max(stream.max()) == 0:
        return [], []
    raw_stream = stream.copy() if retain_raw else None

    max_gap_npts = int(max_gap_sec * sampling_rate)
    for trace in stream:
        npts = len(trace.data)
        data_diff = np.diff(trace.data)
        gap_idx = np.where(data_diff == 0)[0]
        gap_list = np.split(gap_idx, np.where(np.diff(gap_idx) != 1)[0] + 1)
        gap_list = [gap for gap in gap_list if len(gap) >= 10]
        for index, gap in enumerate(gap_list):
            idx0 = max(0, gap[0] - 1)
            idx1 = min(npts - 1, gap[-1] + 1)
            if index < len(gap_list) - 1:
                idx2 = min(
                    idx1 + (idx1 - idx0),
                    idx1 + max_gap_npts,
                    gap_list[index + 1][0],
                )
            else:
                idx2 = min(idx1 + (idx1 - idx0), idx1 + max_gap_npts, npts - 1)
            if idx1 == idx2:
                continue
            if idx2 == idx1 + (idx1 - idx0):
                trace.data[idx0:idx1] = trace.data[idx1:idx2]
            else:
                num_tile = int(np.ceil((idx1 - idx0) / (idx2 - idx1)))
                trace.data[idx0:idx1] = np.tile(
                    trace.data[idx1:idx2], num_tile
                )[0:idx1 - idx0]

    stream = stream.detrend("demean").detrend("linear").taper(
        max_percentage=0.05,
        max_length=taper_max_length_sec,
    )
    if any(
        not np.isclose(trace.stats.sampling_rate, sampling_rate)
        for trace in stream
    ):
        stream.resample(sampling_rate)

    # Resampling traces that started with different rates can leave their final
    # samples offset by rounding. Re-establish one exact model-input grid.
    common_start = max(trace.stats.starttime for trace in stream)
    common_end = min(trace.stats.endtime for trace in stream)
    stream = stream.slice(common_start, common_end, nearest_sample=True)
    common_npts = min(len(trace) for trace in stream)
    if common_npts <= 0:
        return [], []
    for trace in stream:
        trace.data = trace.data[:common_npts]

    if to_filter:
        freq_min, freq_max = frequency_band
        if freq_min and freq_max:
            stream.filter("bandpass", freqmin=freq_min, freqmax=freq_max)
        elif freq_min:
            stream.filter("highpass", freq=freq_min)
        elif freq_max:
            stream.filter("lowpass", freq=freq_max)
        else:
            raise ValueError("at least one filter corner must be configured")
    return stream, raw_stream

def get_sta_dict(sta_file):
    """Read PAL station locations and time-invariant/time-varying gains."""
    sta_dict = {}
    with open(sta_file) as fp:
        for line in fp:
            codes = [code.strip() for code in line.split(",")]
            if not codes or not codes[0]:
                continue
            selector_parts = codes[0].split(".")
            if len(selector_parts) < 2:
                print("false station selector: {}".format(codes[0]))
                continue
            # Local file grouping and PAL association are keyed by NET.STA.
            # Optional BAND and LOC fields remain useful to acquisition tools
            # but do not alter the station identity here.
            net_sta = ".".join(selector_parts[:2])
            lat, lon, ele = [float(code) for code in codes[1:4]]
            if len(codes[4:]) == 1:
                gain = float(codes[4])
            elif len(codes[4:]) == 3:
                gain = [float(code) for code in codes[4:]]
            elif len(codes[4:]) == 5:
                gain = [float(code) for code in codes[4:7]]
                gain += [UTCDateTime(code) for code in codes[7:9]]
                gain = [gain]
            else:
                print("false sta_file format!")
                continue
            if net_sta not in sta_dict:
                sta_dict[net_sta] = [lat, lon, ele, gain]
            else:
                sta_dict[net_sta][-1].append(gain[0])
    for sta_info in sta_dict.values():
        gain = sta_info[-1]
        if isinstance(gain, list) and gain and isinstance(gain[0], list):
            gain.sort(key=lambda row: row[3])
    return sta_dict


def get_pal_picks(date, pick_dir, vp=5.9, vs=3.45):
    """Read PAL picks and derive PAL's rough origin time in memory."""
    picks = []
    dtype = [
        ("net_sta", "O"), ("sta_ot", "O"), ("tp", "O"),
        ("ts", "O"), ("s_amp", "O"),
    ]
    pick_path = os.path.join(pick_dir, str(date.date) + ".pick")
    if not os.path.exists(pick_path):
        return np.array([], dtype=dtype)
    with open(pick_path) as fp:
        for line in fp:
            codes = [code.strip() for code in line.split(",")]
            if len(codes) < 4:
                continue
            net_sta = codes[0]
            try:
                # Current portable schema: station,tp,ts,s_amp,...
                tp, ts = [UTCDateTime(code) for code in codes[1:3]]
                s_amp = float(codes[3])
                distance = (ts - tp) / (1.0 / float(vs) - 1.0 / float(vp))
                sta_ot = tp - distance / float(vp)
            except (TypeError, ValueError):
                # Backward compatibility: station,sta_ot,tp,ts,s_amp,...
                if len(codes) < 5:
                    continue
                sta_ot, tp, ts = [UTCDateTime(code) for code in codes[1:4]]
                s_amp = float(codes[4])
            picks.append((net_sta, sta_ot, tp, ts, s_amp))
    return np.array(picks, dtype=dtype)


def get_picks(date, pick_dir):
    """Read legacy or extended daily AI picks for association."""
    picks = []
    dtype = [
        ("net_sta", "O"), ("sta_ot", "O"), ("tp", "O"),
        ("ts", "O"), ("s_amp", "f8"),
        ("p_prob", "f8"), ("s_prob", "f8"),
        ("tp_std", "f8"), ("ts_std", "f8"),
        ("p_prob_std", "f8"), ("s_prob_std", "f8"),
        ("num_support", "i4"), ("sources", "O"),
        ("picker_cluster_sizes", "O"),
    ]
    pick_path = os.path.join(pick_dir, str(date.date) + ".pick")
    if not os.path.exists(pick_path):
        return np.array([], dtype=dtype)
    with open(pick_path) as fp:
        for line_number, line in enumerate(fp, start=1):
            codes = [code.strip() for code in line.split(",")]
            if len(codes) < 4:
                raise ValueError(
                    "{}:{} invalid AI pick row".format(pick_path, line_number)
                )
            net_sta = codes[0]
            tp, ts = [UTCDateTime(code) for code in codes[1:3]]
            values = (
                net_sta, calc_ot(tp, ts), tp, ts, float(codes[3]),
                float(codes[4]) if len(codes) > 4 else -1.0,
                float(codes[5]) if len(codes) > 5 else -1.0,
                float(codes[6]) if len(codes) > 6 else 0.0,
                float(codes[7]) if len(codes) > 7 else 0.0,
                float(codes[8]) if len(codes) > 8 else 0.0,
                float(codes[9]) if len(codes) > 9 else 0.0,
                int(codes[10]) if len(codes) > 10 else 1,
                codes[11] if len(codes) > 11 else "",
                codes[12] if len(codes) > 12 else "",
            )
            picks.append(values)
    return np.array(picks, dtype=dtype)


def calc_ot(tp, ts):
    vp, vs = 6.0, 3.45
    distance = (ts - tp) / (1 / vs - 1 / vp)
    return tp - distance / vp


def dtime2str(dtime):
    date = "".join(str(dtime).split("T")[0].split("-"))
    time = "".join(str(dtime).split("T")[1].split(":"))[0:9]
    return date + time
