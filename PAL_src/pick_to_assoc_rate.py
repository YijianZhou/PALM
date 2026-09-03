"""Convert full-range PAL/AI pick and phase files to association-rate CSV."""

import argparse
import bisect
import csv
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re


OUTPUT_FIELDS = (
    "date", "net_sta", "num_picks", "num_associated_picks",
    "num_unassociated_picks", "association_ratio",
)
COMPACT_TIME = re.compile(r"^\d{14}(?:\.\d+)?$")


def looks_like_time(value):
    text = value.strip()
    return "T" in text or bool(COMPACT_TIME.match(text))


def parse_time(value):
    text = value.strip()
    if COMPACT_TIME.match(text):
        code = "%Y%m%d%H%M%S.%f" if "." in text else "%Y%m%d%H%M%S"
        parsed = datetime.strptime(text, code).replace(tzinfo=timezone.utc)
    else:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
    return parsed.timestamp()


def utc_date(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()


def read_associated_phase_picks(phase_file):
    """Return unique associated P/S arrivals grouped and sorted by station."""
    unique = defaultdict(set)
    with Path(phase_file).open(newline="", encoding="utf-8-sig") as fp:
        for line_number, row in enumerate(csv.reader(fp), start=1):
            if not row or not row[0].strip():
                continue
            station = row[0].strip()
            # A phase-pick row has P and S timestamps in columns 2 and 3.
            # This also distinguishes compact event headers whose origin time
            # may contain a decimal point.
            if len(row) < 3 or not all(looks_like_time(row[i]) for i in (1, 2)):
                continue
            try:
                tp = parse_time(row[1])
                ts = parse_time(row[2])
            except ValueError as exc:
                raise ValueError(
                    "{}:{} cannot parse phase P/S times".format(
                        phase_file, line_number
                    )
                ) from exc
            unique[station].add((round(tp, 6), round(ts, 6)))
    return {station: sorted(arrivals) for station, arrivals in unique.items()}


def detect_pick_format(row, requested_format, pick_file, line_number):
    if requested_format != "auto":
        return requested_format
    if len(row) >= 4 and all(
        looks_like_time(row[index]) for index in (1, 2, 3)
    ):
        return "pal"
    if len(row) >= 3 and all(looks_like_time(row[index]) for index in (1, 2)):
        return "ai"
    raise ValueError(
        "{}:{} cannot detect pick format; expected PAL "
        "(station,station_OT,P,S,...) or AI (station,P,S,...)".format(
            pick_file, line_number
        )
    )


def iter_input_picks(pick_file, pick_format="auto"):
    with Path(pick_file).open(newline="", encoding="utf-8-sig") as fp:
        for line_number, row in enumerate(csv.reader(fp), start=1):
            if not row or not row[0].strip():
                continue
            if row[0].strip().lower() in {"net_sta", "station"}:
                continue
            row_format = detect_pick_format(
                row, pick_format, pick_file, line_number
            )
            tp_index, ts_index = (2, 3) if row_format == "pal" else (1, 2)
            try:
                tp = parse_time(row[tp_index])
                ts = parse_time(row[ts_index])
            except (IndexError, ValueError) as exc:
                raise ValueError(
                    "{}:{} cannot parse pick P/S times as {} format".format(
                        pick_file, line_number, row_format
                    )
                ) from exc
            yield row[0].strip(), tp, ts


def match_associated_pick(
    station, tp, ts, associated, associated_tp, used, tolerance_sec
):
    arrivals = associated.get(station, ())
    if not arrivals:
        return False
    tp_values = associated_tp[station]
    left = bisect.bisect_left(tp_values, tp - tolerance_sec)
    right = bisect.bisect_right(tp_values, tp + tolerance_sec)
    best_index = None
    best_delta = None
    for index in range(left, right):
        if index in used[station]:
            continue
        phase_tp, phase_ts = arrivals[index]
        if abs(phase_ts - ts) > tolerance_sec:
            continue
        delta = abs(phase_tp - tp) + abs(phase_ts - ts)
        if best_delta is None or delta < best_delta:
            best_index, best_delta = index, delta
    if best_index is None:
        return False
    used[station].add(best_index)
    return True


def convert_pick_to_assoc_rate(
    pick_file, phase_file, output_file, tolerance_sec=0.1, pick_format="auto"
):
    if tolerance_sec < 0:
        raise ValueError("tolerance_sec must be nonnegative")
    if pick_format not in {"auto", "pal", "ai"}:
        raise ValueError("pick_format must be auto, pal, or ai")

    associated = read_associated_phase_picks(phase_file)
    associated_tp = {
        station: [arrival[0] for arrival in arrivals]
        for station, arrivals in associated.items()
    }
    used = defaultdict(set)
    counts = defaultdict(lambda: [0, 0])  # total, associated
    num_input_picks = 0
    num_associated = 0

    for station, tp, ts in iter_input_picks(pick_file, pick_format):
        key = (utc_date(tp), station)
        counts[key][0] += 1
        num_input_picks += 1
        if match_associated_pick(
            station, tp, ts, associated, associated_tp, used, tolerance_sec
        ):
            counts[key][1] += 1
            num_associated += 1

    if not num_input_picks:
        raise ValueError("no picks found in {}".format(pick_file))

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_suffix(output_path.suffix + ".partial")
    with partial.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for (date, station), (total, num_assoc) in sorted(counts.items()):
            writer.writerow({
                "date": date,
                "net_sta": station,
                "num_picks": total,
                "num_associated_picks": num_assoc,
                "num_unassociated_picks": total - num_assoc,
                "association_ratio": "{:.8f}".format(num_assoc / total),
            })
    partial.replace(output_path)

    num_phase_picks = sum(len(arrivals) for arrivals in associated.values())
    return {
        "input_picks": num_input_picks,
        "associated_picks": num_associated,
        "unassociated_picks": num_input_picks - num_associated,
        "phase_picks": num_phase_picks,
        "unmatched_phase_picks": num_phase_picks - num_associated,
        "station_dates": len(counts),
        "association_ratio": num_associated / num_input_picks,
        "output_file": str(output_path),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pick_file", required=True)
    parser.add_argument("--phase_file", required=True)
    parser.add_argument("--output_file", required=True)
    parser.add_argument("--tolerance_sec", type=float, default=0.1)
    parser.add_argument(
        "--pick_format", choices=("auto", "pal", "ai"), default="auto"
    )
    args = parser.parse_args()
    summary = convert_pick_to_assoc_rate(
        args.pick_file, args.phase_file, args.output_file,
        tolerance_sec=args.tolerance_sec, pick_format=args.pick_format,
    )
    for name, value in summary.items():
        if name == "association_ratio":
            print("{}: {:.6f}".format(name, value))
        else:
            print("{}: {}".format(name, value))


if __name__ == "__main__":
    main()
