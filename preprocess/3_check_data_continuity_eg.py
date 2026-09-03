#!/usr/bin/env python3
"""Report and plot daily waveform availability against station epochs."""

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from obspy import read

from preprocess_common import (
    compact_date,
    component_code,
    iter_days,
    read_station_epochs,
    resolve_path,
)


# ============================================================================
# USER SETTINGS
# ============================================================================
CASE_CODE = "eg"
STATION_FILE = Path("output/station_%s.csv" % CASE_CODE)
DAILY_ROOT = Path("/data/ai_pal_%s_daily" % CASE_CODE)
TIME_RANGE = "20190704-20190707"  # Exclusive end date.
LOW_CONTINUITY_RATIO = 0.80
FIGURE_SIZE = (16, 10)


def expected_station_days(epochs, days):
    expected = defaultdict(set)
    bands = defaultdict(set)
    for day in days:
        day_end = day + 86400
        calendar_day = day.datetime.date()
        for epoch in epochs:
            if epoch["end"] <= day or epoch["start"] >= day_end:
                continue
            net_sta = "{}.{}".format(epoch["net"], epoch["sta"])
            expected[net_sta].add(calendar_day)
            bands[(net_sta, calendar_day)].add(epoch["band"])
    return expected, bands


def observed_station_days(root, days, expected_bands):
    components = defaultdict(set)
    read_errors = []
    for day in days:
        day_dir = root / compact_date(day)
        calendar_day = day.datetime.date()
        for path in sorted(day_dir.glob("*.mseed")):
            try:
                stream = read(str(path), headonly=True)
            except Exception as exc:
                read_errors.append((str(path), str(exc)))
                continue
            for trace in stream:
                net_sta = "{}.{}".format(trace.stats.network, trace.stats.station)
                bands = expected_bands.get((net_sta, calendar_day), set())
                if not any(trace.stats.channel.startswith(band) for band in bands):
                    continue
                component = component_code(trace.stats.channel)
                if component in {"E", "N", "Z"}:
                    components[(net_sta, calendar_day)].add(component)
    return components, read_errors


def build_rows(expected, observed):
    rows = []
    for net_sta in sorted(expected):
        expected_days = expected[net_sta]
        any_days = {day for day in expected_days if observed.get((net_sta, day))}
        complete_days = {
            day for day in expected_days
            if observed.get((net_sta, day), set()) == {"E", "N", "Z"}
        }
        count = len(expected_days)
        rows.append({
            "station": net_sta,
            "expected_days": count,
            "days_with_data": len(any_days),
            "days_with_three_components": len(complete_days),
            "continuity_ratio": "{:.4f}".format(len(any_days) / count if count else 0.0),
            "three_component_ratio": "{:.4f}".format(
                len(complete_days) / count if count else 0.0
            ),
        })
    return rows


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def plot_availability(path, expected, observed):
    stations = sorted(expected)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    for index, station in enumerate(stations):
        expected_days = sorted(expected[station])
        available_days = [
            day for day in expected_days if observed.get((station, day))
        ]
        ax.vlines(
            expected_days, index - 0.30, index + 0.30,
            color="0.75", linewidth=0.5, zorder=1,
        )
        ax.scatter(
            available_days, [index] * len(available_days),
            marker="|", s=35, color="tab:blue", zorder=2,
        )
    ax.set_yticks(range(len(stations)), labels=stations)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    ax.set_title("AI-PAL daily waveform continuity")
    ax.set_xlabel("UTC date")
    ax.set_ylabel("Station")
    ax.grid(True, axis="x", linewidth=0.4, color="0.85")
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def main():
    days = list(iter_days(TIME_RANGE))
    epochs = read_station_epochs(STATION_FILE)
    expected, expected_bands = expected_station_days(epochs, days)
    observed, read_errors = observed_station_days(
        resolve_path(DAILY_ROOT), days, expected_bands
    )
    rows = build_rows(expected, observed)
    fields = (
        "station", "expected_days", "days_with_data",
        "days_with_three_components", "continuity_ratio", "three_component_ratio",
    )
    output_dir = resolve_path("output")
    write_csv(output_dir / ("data_continuity_%s.csv" % CASE_CODE), rows, fields)
    low_rows = [
        row for row in rows
        if float(row["continuity_ratio"]) < LOW_CONTINUITY_RATIO
    ]
    write_csv(output_dir / ("data_continuity_low_%s.csv" % CASE_CODE), low_rows, fields)
    write_csv(
        output_dir / ("data_continuity_read_errors_%s.csv" % CASE_CODE),
        [{"path": path, "error": error} for path, error in read_errors],
        ("path", "error"),
    )
    plot_availability(
        output_dir / ("data_continuity_%s.png" % CASE_CODE), expected, observed
    )
    print("{} stations checked; {} below {:.0%}; {} unreadable files".format(
        len(rows), len(low_rows), LOW_CONTINUITY_RATIO, len(read_errors)
    ))


if __name__ == "__main__":
    main()
