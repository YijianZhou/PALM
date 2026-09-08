"""Shared contracts for the example continuous-waveform preparation tools."""

import csv
from collections import defaultdict
from pathlib import Path

from obspy import UTCDateTime, read


RUN_DIR = Path(__file__).resolve().parent


def resolve_path(path):
    path = Path(path).expanduser()
    return path if path.is_absolute() else RUN_DIR / path


def parse_time(value):
    return UTCDateTime(str(value).strip())


def parse_station_id(value):
    """Parse the PAL ``NET.STA.BAND.LOC`` station selector."""
    parts = str(value).strip().split(".", 3)
    if len(parts) != 4 or not all(parts[:3]):
        raise ValueError("invalid PAL station selector: {!r}".format(value))
    return tuple(parts)


def read_station_epochs(path):
    """Read and validate a nine-column PAL/AI-PAL station CSV."""
    path = resolve_path(path)
    epochs = []
    with path.open(newline="", encoding="utf-8-sig") as fp:
        for line_number, row in enumerate(csv.reader(fp), start=1):
            if not row or row[0].lstrip().startswith("#"):
                continue
            if len(row) != 9:
                raise ValueError(
                    "{}:{}: expected 9 columns, got {}".format(
                        path, line_number, len(row)
                    )
                )
            net, sta, band, location = parse_station_id(row[0])
            start, end = parse_time(row[7]), parse_time(row[8])
            if end <= start:
                raise ValueError(
                    "{}:{}: station epoch end must follow start".format(
                        path, line_number
                    )
                )
            epochs.append({
                "net": net,
                "sta": sta,
                "band": band,
                "location": location,
                "latitude": float(row[1]),
                "longitude": float(row[2]),
                "elevation_m": float(row[3]),
                "gains": tuple(float(value) for value in row[4:7]),
                "start": start,
                "end": end,
            })
    if not epochs:
        raise ValueError("station file contains no usable epochs: {}".format(path))
    return epochs


def active_epochs(epochs, start, end):
    return [epoch for epoch in epochs if epoch["end"] > start and epoch["start"] < end]


def iter_days(time_range):
    start_text, end_text = str(time_range).split("-", 1)
    start, end = UTCDateTime(start_text), UTCDateTime(end_text)
    if end <= start:
        raise ValueError("time range end must follow start")
    current = UTCDateTime(start.date)
    while current < end:
        yield current
        current += 86400


def compact_date(value):
    return UTCDateTime(value).strftime("%Y%m%d")


def normalized_location(value):
    value = str(value).strip("_ ")
    return value if value and value != "--" else ""


def component_code(channel):
    return {"1": "E", "2": "N", "3": "Z"}.get(
        channel[-1].upper(), channel[-1].upper()
    )


def waveform_combinations(day_dir, expected_stations=None):
    """Inventory available location-band combinations from miniSEED headers."""
    day_dir = Path(day_dir)
    expected = set(expected_stations) if expected_stations is not None else None
    combinations = defaultdict(lambda: {"components": set(), "paths": set()})
    read_errors = []
    for path in sorted(day_dir.glob("*.mseed")):
        try:
            stream = read(str(path), headonly=True)
        except Exception as exc:
            read_errors.append("{}: {}".format(path.name, exc))
            continue
        for trace in stream:
            net_sta = (str(trace.stats.network), str(trace.stats.station))
            if expected is not None and net_sta not in expected:
                continue
            channel = str(trace.stats.channel)
            component = component_code(channel)
            if len(channel) < 2 or component not in ("E", "N", "Z"):
                continue
            key = (
                net_sta[0], net_sta[1],
                normalized_location(trace.stats.location), channel[:2],
            )
            combinations[key]["components"].add(component)
            combinations[key]["paths"].add(path)
    return dict(combinations), read_errors


def priority_rank(value, priorities):
    priorities = tuple(priorities)
    if value in priorities:
        return 0, priorities.index(value)
    return 1, value


def preferred_waveform_combinations(
    combinations, location_priority, channel_priority
):
    """Select one three- or single-component combination per station."""
    by_station = defaultdict(list)
    for key, details in combinations.items():
        by_station[key[:2]].append((key, details))

    selected = {}
    for net_sta, candidates in by_station.items():
        ordered = sorted(candidates, key=lambda item: (
            priority_rank(item[0][2], location_priority),
            priority_rank(item[0][3], channel_priority),
        ))
        complete = [
            item for item in ordered
            if item[1]["components"] == {"E", "N", "Z"}
        ]
        single = [
            item for item in ordered if len(item[1]["components"]) == 1
        ]
        selected[net_sta] = (
            complete[0] if complete else single[0] if single else ordered[0]
        )
    return selected


def prune_waveform_combinations(combinations, selected):
    """Delete files belonging only to non-selected station combinations."""
    selected_paths = set()
    all_paths = set()
    for key, details in combinations.items():
        paths = set(details["paths"])
        all_paths.update(paths)
        chosen = selected.get(key[:2])
        if chosen is not None and chosen[0] == key:
            selected_paths.update(paths)
    removed = []
    for path in sorted(all_paths - selected_paths):
        path.unlink()
        removed.append(path)
    return removed
