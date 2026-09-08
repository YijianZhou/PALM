#!/usr/bin/env python3
"""Compare fullfed station operation with raw or cleaned waveform coverage."""

import csv
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from obspy import UTCDateTime, read

from preprocess_common import compact_date, iter_days, read_station_epochs, resolve_path


# ============================================================================
# USER SETTINGS
# ============================================================================
CASE_CODE = "eg"
STATION_FILE = Path("output/station_%s.csv" % CASE_CODE)
FULLFED_TEMPLATE = "input/station_{}.fullfed"
RAW_ROOT = Path("/data/ai_pal_%s_raw" % CASE_CODE)
CLEAN_ROOT = Path("/data/ai_pal_%s_daily" % CASE_CODE)
DATA_SOURCE = "clean"  # "raw" or "clean"
TIME_RANGE = "20190704-20190707"  # Exclusive end date.
chn_codes = ("HH", "BH", "EH", "HN")
LOW_CONTINUITY_RATIO = 0.80
NUM_WORKERS = 8
PROGRESS_EVERY_DAYS = 100


# Plot style follows preprocess_EH/plot_data-continuity_eh.py.
PNG_DPI = 300
FIGURE_WIDTH = 18.0
ROW_HEIGHT = 0.18
MINIMUM_FIGURE_HEIGHT = 8.0
EXPECTED_COLOR = "0.72"
OBSERVED_COLOR = "#0072B2"
EXPECTED_LINEWIDTH = 5.0
OBSERVED_LINEWIDTH = 2.0
STATION_LABEL_FONTSIZE = 7.0
AXIS_FONTSIZE = 12
TITLE_FONTSIZE = 15


def clip_interval(start, end, study_start, study_end):
    start = max(UTCDateTime(start), study_start)
    end = min(UTCDateTime(end), study_end)
    return (start, end) if start < end else None


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals, key=lambda item: (item[0], item[1])):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def selected_stations(path):
    epochs = read_station_epochs(path)
    return sorted({"{}.{}".format(row["net"], row["sta"]) for row in epochs})


def fullfed_paths(stations):
    networks = sorted({station.split(".", 1)[0].lower() for station in stations})
    paths = [resolve_path(Path(FULLFED_TEMPLATE.format(net))) for net in networks]
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "missing fullfed station files: {}".format(
                ", ".join(map(str, missing))
            )
        )
    return paths


def expected_intervals(paths, stations, study_start, study_end):
    station_set = set(stations)
    intervals = defaultdict(list)
    for path in paths:
        with path.open(encoding="utf-8", errors="replace") as fp:
            for line in fp:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                values = line.rstrip("\n").split("|")
                if len(values) < 17:
                    continue
                net_sta = "{}.{}".format(values[0].strip(), values[1].strip())
                channel = values[3].strip()
                if (
                    net_sta not in station_set
                    or len(channel) < 2
                    or channel[:2] not in chn_codes
                ):
                    continue
                try:
                    start = UTCDateTime(values[-2])
                    end = UTCDateTime(values[-1]) if values[-1].strip() else study_end
                except (TypeError, ValueError):
                    continue
                clipped = clip_interval(start, end, study_start, study_end)
                if clipped:
                    intervals[net_sta].append(clipped)
    return {
        station: merge_intervals(intervals.get(station, []))
        for station in stations
    }


def scan_observed_day(day_dir, station_set, study_start, study_end):
    intervals = defaultdict(list)
    read_errors = []
    matched_files = 0
    if not day_dir.is_dir():
        return intervals, read_errors, matched_files
    for path in sorted(day_dir.glob("*.mseed")):
        try:
            stream = read(str(path), headonly=True)
        except Exception as exc:
            read_errors.append((str(path), str(exc)))
            continue
        file_matched = False
        for trace in stream:
            net_sta = "{}.{}".format(trace.stats.network, trace.stats.station)
            channel = str(trace.stats.channel)
            if (
                net_sta not in station_set
                or len(channel) < 2
                or channel[:2] not in chn_codes
            ):
                continue
            delta = float(getattr(trace.stats, "delta", 0.0))
            clipped = clip_interval(
                trace.stats.starttime,
                trace.stats.endtime + max(0.0, delta),
                study_start,
                study_end,
            )
            if clipped:
                intervals[net_sta].append(clipped)
                file_matched = True
        matched_files += int(file_matched)
    return intervals, read_errors, matched_files


def observed_intervals(root, days, stations, study_start, study_end):
    if not root.is_dir():
        raise FileNotFoundError("waveform archive does not exist: {}".format(root))
    station_set = set(stations)
    intervals = defaultdict(list)
    read_errors = []
    matched_files = 0
    day_dirs = [root / compact_date(day) for day in days]
    workers = max(1, min(int(NUM_WORKERS), len(day_dirs)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                scan_observed_day,
                day_dir,
                station_set,
                study_start,
                study_end,
            ): day_dir
            for day_dir in day_dirs
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            day_intervals, day_errors, day_matched = future.result()
            for station, station_intervals in day_intervals.items():
                intervals[station].extend(station_intervals)
            read_errors.extend(day_errors)
            matched_files += day_matched
            if completed % PROGRESS_EVERY_DAYS == 0 or completed == len(day_dirs):
                print(
                    "scanned {}/{} daily directories with {} workers".format(
                        completed, len(day_dirs), workers
                    )
                )
    read_errors.sort(key=lambda item: item[0])
    return (
        {
            station: merge_intervals(intervals.get(station, []))
            for station in stations
        },
        read_errors,
        matched_files,
    )


def interval_seconds(intervals):
    return sum(float(end - start) for start, end in intervals)


def overlap_seconds(left, right):
    total = 0.0
    left_index = right_index = 0
    while left_index < len(left) and right_index < len(right):
        start = max(left[left_index][0], right[right_index][0])
        end = min(left[left_index][1], right[right_index][1])
        if start < end:
            total += float(end - start)
        if left[left_index][1] <= right[right_index][1]:
            left_index += 1
        else:
            right_index += 1
    return total


def build_rows(stations, expected, observed, study_start, study_end):
    study_seconds = float(study_end - study_start)
    rows = []
    for station in stations:
        expected_seconds = interval_seconds(expected[station])
        observed_seconds = interval_seconds(observed[station])
        overlap = overlap_seconds(expected[station], observed[station])
        rows.append({
            "station": station,
            "expected_days": "{:.6f}".format(expected_seconds / 86400.0),
            "observed_days": "{:.6f}".format(observed_seconds / 86400.0),
            "observed_expected_days": "{:.6f}".format(overlap / 86400.0),
            "study_coverage": "{:.6f}".format(
                observed_seconds / study_seconds if study_seconds else 0.0
            ),
            "expected_continuity": (
                "{:.6f}".format(overlap / expected_seconds)
                if expected_seconds else ""
            ),
            "first_expected": str(expected[station][0][0]) if expected[station] else "",
            "last_expected": str(expected[station][-1][1]) if expected[station] else "",
            "first_observed": str(observed[station][0][0]) if observed[station] else "",
            "last_observed": str(observed[station][-1][1]) if observed[station] else "",
        })
    return rows


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_intervals(path, intervals):
    rows = []
    for station in sorted(intervals):
        for start, end in intervals[station]:
            rows.append({
                "station": station,
                "start": str(start),
                "end_exclusive": str(end),
                "duration_days": "{:.6f}".format(float(end - start) / 86400.0),
            })
    write_csv(path, rows, ("station", "start", "end_exclusive", "duration_days"))


def line_segments(intervals, y):
    return [
        [(mdates.date2num(start.datetime), y), (mdates.date2num(end.datetime), y)]
        for start, end in intervals
    ]


def plot_continuity(path, stations, expected, observed, study_start, study_end, source):
    height = max(MINIMUM_FIGURE_HEIGHT, ROW_HEIGHT * len(stations))
    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH, height))
    expected_segments = []
    observed_segments = []
    for index, station in enumerate(stations):
        expected_segments.extend(line_segments(expected[station], index))
        observed_segments.extend(line_segments(observed[station], index))
    ax.add_collection(LineCollection(
        expected_segments, colors=EXPECTED_COLOR,
        linewidths=EXPECTED_LINEWIDTH, zorder=1,
    ))
    ax.add_collection(LineCollection(
        observed_segments, colors=OBSERVED_COLOR,
        linewidths=OBSERVED_LINEWIDTH, zorder=2,
    ))
    ax.set_xlim(study_start.datetime, study_end.datetime)
    ax.set_ylim(-1, len(stations))
    ax.set_yticks(range(len(stations)))
    ax.set_yticklabels(stations, fontsize=STATION_LABEL_FONTSIZE)
    ax.invert_yaxis()
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.tick_params(axis="x", labelsize=AXIS_FONTSIZE, rotation=45)
    ax.set_xlabel("Date", fontsize=AXIS_FONTSIZE)
    ax.set_ylabel("Station", fontsize=AXIS_FONTSIZE)
    ax.set_title(
        "{} station coverage: fullfed expectation and {} data".format(CASE_CODE, source),
        fontsize=TITLE_FONTSIZE,
    )
    ax.grid(axis="x", color="0.85", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(handles=[
        Line2D(
            [0], [0], color=EXPECTED_COLOR, lw=EXPECTED_LINEWIDTH,
            label="Expected from fullfed",
        ),
        Line2D(
            [0], [0], color=OBSERVED_COLOR, lw=OBSERVED_LINEWIDTH,
            label="{} waveform coverage".format(source.capitalize()),
        ),
    ], loc="upper right", fontsize=AXIS_FONTSIZE)
    fig.tight_layout()
    fig.savefig(path, dpi=PNG_DPI, bbox_inches="tight")
    plt.close(fig)


def main():
    source = str(DATA_SOURCE).strip().lower()
    if source not in {"raw", "clean"}:
        raise ValueError('DATA_SOURCE must be "raw" or "clean"')
    days = list(iter_days(TIME_RANGE))
    if not days:
        raise ValueError("TIME_RANGE contains no days")
    study_start, study_end = days[0], days[-1] + 86400
    stations = selected_stations(STATION_FILE)
    expected = expected_intervals(
        fullfed_paths(stations), stations, study_start, study_end
    )
    data_root = resolve_path(RAW_ROOT if source == "raw" else CLEAN_ROOT)
    observed, read_errors, matched_files = observed_intervals(
        data_root, days, stations, study_start, study_end
    )
    rows = build_rows(stations, expected, observed, study_start, study_end)
    fields = (
        "station", "expected_days", "observed_days", "observed_expected_days",
        "study_coverage", "expected_continuity", "first_expected",
        "last_expected", "first_observed", "last_observed",
    )
    output_dir = resolve_path("output")
    stem = "data_continuity_{}_{}".format(CASE_CODE, source)
    write_csv(output_dir / (stem + ".csv"), rows, fields)
    low_rows = [
        row for row in rows
        if row["expected_continuity"]
        and float(row["expected_continuity"]) < LOW_CONTINUITY_RATIO
    ]
    write_csv(output_dir / (stem + "_low.csv"), low_rows, fields)
    write_csv(
        output_dir / (stem + "_read_errors.csv"),
        [{"path": path, "error": error} for path, error in read_errors],
        ("path", "error"),
    )
    write_intervals(output_dir / (stem + "_observed_intervals.csv"), observed)
    figure_path = output_dir / (stem + ".png")
    plot_continuity(
        figure_path, stations, expected, observed, study_start, study_end, source
    )
    print("data source: {}".format(data_root))
    print("{} stations; {} waveform files; {} below {:.0%}; {} read errors".format(
        len(stations), matched_files, len(low_rows),
        LOW_CONTINUITY_RATIO, len(read_errors),
    ))
    print("figure: {}".format(figure_path))


if __name__ == "__main__":
    main()
