"""Shared contracts for the example continuous-waveform preparation tools."""

import csv
from pathlib import Path

from obspy import UTCDateTime


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
