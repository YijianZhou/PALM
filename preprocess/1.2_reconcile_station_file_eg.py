#!/usr/bin/env python3
"""Reconcile downloaded waveform selectors with PAL station gain epochs."""

import csv
import os
import shutil
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from obspy import UTCDateTime

from preprocess_common import (
    component_code,
    iter_days,
    normalized_location,
    preferred_waveform_combinations,
    prune_waveform_combinations,
    resolve_path,
    waveform_combinations,
)


# ============================================================================
# USER SETTINGS
# ============================================================================
CASE_CODE = "eg"
STATION_FILE = Path("output/station_%s.csv" % CASE_CODE)
FULLFED_TEMPLATE = "input/station_{}.fullfed"
RAW_ROOT = Path("/data/ai_pal_%s_raw" % CASE_CODE)
TIME_RANGE = "20190704-20190707"  # Exclusive end date.
loc_codes = ("10", "20", "01", "02", "00", "")
chn_codes = ("HH", "BH", "EH", "HN")
AUDIT_FILE = Path("output/station_%s_download_reconciliation.csv" % CASE_CODE)
BACKUP_FILE = Path("output/station_%s_before_download_reconciliation.csv" % CASE_CODE)
NUM_WORKERS = 8  # Concurrent daily header scans on the waveform archive.


_REQUIRED_COMPONENTS = {"E", "N", "Z"}


def selector_parts(value):
    parts = str(value).strip().split(".", 3)
    if len(parts) != 4:
        raise ValueError("invalid station selector: {!r}".format(value))
    return parts[0], parts[1], parts[2], normalized_location(parts[3])


def selector_text(key):
    net, sta, location, band = key
    return "{}.{}.{}.{}".format(net, sta, band, location)


def read_pal_rows(path):
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as fp:
        for line_number, values in enumerate(csv.reader(fp), start=1):
            if not values or values[0].lstrip().startswith("#"):
                continue
            if len(values) != 9:
                raise ValueError(
                    "{}:{} expected 9 columns".format(path, line_number)
                )
            net, sta, band, location = selector_parts(values[0])
            start, end = UTCDateTime(values[7]), UTCDateTime(values[8])
            if end <= start:
                raise ValueError(
                    "{}:{} invalid station interval".format(path, line_number)
                )
            rows.append({
                "net": net,
                "sta": sta,
                "band": band,
                "location": location,
                "values": list(values[:7]),
                "start": start,
                "end": end,
            })
    return rows


def read_fullfed_records(paths):
    paths = sorted({resolve_path(path) for path in paths})
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "missing fullfed station files: {}".format(
                ", ".join(map(str, missing))
            )
        )

    records = defaultdict(list)
    for path in paths:
        with path.open(encoding="utf-8") as fp:
            for line_number, line in enumerate(fp, start=1):
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                codes = line.strip().split("|")
                if len(codes) < 17:
                    continue
                net, sta, location, channel = codes[:4]
                if len(channel) < 2:
                    continue
                try:
                    latitude, longitude, elevation = [
                        float(value) for value in codes[4:7]
                    ]
                    gain = float(codes[11])
                    start = UTCDateTime(codes[-2])
                    end = UTCDateTime(codes[-1]) if codes[-1] else None
                except (TypeError, ValueError):
                    continue
                component = component_code(channel)
                if component not in _REQUIRED_COMPONENTS:
                    continue
                key = (
                    net, sta, normalized_location(location), channel[:2],
                )
                records[key].append({
                    "channel": channel,
                    "component": component,
                    "latitude": latitude,
                    "longitude": longitude,
                    "elevation": elevation,
                    "gain": gain,
                    "start": start,
                    "end": end,
                    "source": "{}:{}".format(path.name, line_number),
                })
    return records


def overlaps(record, start, end):
    return record["start"] < end and (
        record["end"] is None or record["end"] > start
    )


def covers(record, start, end):
    return record["start"] <= start and (
        record["end"] is None or record["end"] >= end
    )


def gain_values(records):
    gains = {}
    for record in sorted(records, key=lambda item: item["channel"]):
        gains.setdefault(record["component"], record["gain"])
    fallback = next(iter(gains.values()))
    return [gains.get(component, fallback) for component in ("E", "N", "Z")]


def fullfed_day_rows(key, records, day_start, day_end):
    all_candidates = list(records.get(key, ()))
    candidates = [
        record for record in all_candidates
        if overlaps(record, day_start, day_end)
    ]
    used_nearest = False
    if not candidates and all_candidates:
        midpoint = day_start + (day_end - day_start) / 2.0

        def distance(record):
            if midpoint < record["start"]:
                return float(record["start"] - midpoint)
            if record["end"] is not None and midpoint >= record["end"]:
                return float(midpoint - record["end"])
            return 0.0

        nearest_by_component = {}
        for record in all_candidates:
            component = record["component"]
            if (
                component not in nearest_by_component
                or distance(record) < distance(nearest_by_component[component])
            ):
                nearest_by_component[component] = record
        candidates = []
        for record in nearest_by_component.values():
            copied = dict(record)
            copied["start"] = day_start
            copied["end"] = day_end
            candidates.append(copied)
        used_nearest = bool(candidates)
    if not candidates:
        return [], False

    edge_times = {float(day_start): day_start, float(day_end): day_end}
    for record in candidates:
        if day_start < record["start"] < day_end:
            edge_times[float(record["start"])] = record["start"]
        if (
            record["end"] is not None
            and day_start < record["end"] < day_end
        ):
            edge_times[float(record["end"])] = record["end"]
    edges = [edge_times[value] for value in sorted(edge_times)]

    periods = []
    for start, end in zip(edges, edges[1:]):
        active = [
            record for record in candidates if covers(record, start, end)
        ]
        if not active:
            continue
        gains = gain_values(active)
        periods.append({
            "start": start,
            "end": end,
            "latitude": sum(item["latitude"] for item in active) / len(active),
            "longitude": sum(item["longitude"] for item in active) / len(active),
            "elevation": sum(item["elevation"] for item in active) / len(active),
            "gains": gains,
        })
    if not periods:
        return [], used_nearest

    periods.sort(key=lambda item: (item["start"], item["end"]))
    periods[0]["start"] = day_start
    periods[-1]["end"] = day_end
    for left, right in zip(periods, periods[1:]):
        if left["end"] >= right["start"]:
            continue
        midpoint = left["end"] + (right["start"] - left["end"]) / 2.0
        left["end"] = midpoint
        right["start"] = midpoint

    net, sta, location, band = key
    selector = selector_text(key)
    output = []
    for period in periods:
        gains = period["gains"]
        output.append({
            "net": net,
            "sta": sta,
            "band": band,
            "location": location,
            "values": [
                selector,
                "{:.6f}".format(period["latitude"]),
                "{:.6f}".format(period["longitude"]),
                "{:.1f}".format(period["elevation"]),
                str(gains[0]), str(gains[1]), str(gains[2]),
            ],
            "start": period["start"],
            "end": period["end"],
        })
    return output, used_nearest


def matching_rows(rows, net_sta, key):
    return [
        row for row in rows
        if (row["net"], row["sta"]) == net_sta
        and row["location"] == key[2]
        and row["band"] == key[3]
    ]


def covers_day(rows, day_start, day_end):
    cursor = day_start
    for row in sorted(rows, key=lambda item: (item["start"], item["end"])):
        if row["end"] <= cursor:
            continue
        if row["start"] > cursor:
            return False
        cursor = max(cursor, row["end"])
        if cursor >= day_end:
            return True
    return False


def overlapping_selectors(rows, net_sta, day_start, day_end):
    selectors = set()
    for row in rows:
        if (row["net"], row["sta"]) != net_sta:
            continue
        if row["end"] <= day_start or row["start"] >= day_end:
            continue
        selectors.add(row["values"][0])
    return sorted(selectors)


def apply_station_replacements(rows, replacements):
    """Apply all non-overlapping daily replacements in one linear pass."""
    rows_by_station = defaultdict(list)
    for row in rows:
        rows_by_station[(row["net"], row["sta"])].append(row)

    output = []
    all_stations = set(rows_by_station) | set(replacements)
    for net_sta in sorted(all_stations):
        station_rows = sorted(
            rows_by_station.get(net_sta, []),
            key=lambda row: (row["start"], row["end"]),
        )
        updates = sorted(replacements.get(net_sta, []), key=lambda item: item[0])
        if not updates:
            output.extend(station_rows)
            continue

        update_index = 0
        for row in station_rows:
            while (
                update_index < len(updates)
                and updates[update_index][1] <= row["start"]
            ):
                update_index += 1
            cursor = row["start"]
            candidate_index = update_index
            while candidate_index < len(updates):
                update_start, update_end, _ = updates[candidate_index]
                if update_start >= row["end"]:
                    break
                if cursor < update_start:
                    piece = dict(row)
                    piece["values"] = list(row["values"])
                    piece["start"] = cursor
                    piece["end"] = min(update_start, row["end"])
                    output.append(piece)
                cursor = max(cursor, update_end)
                if cursor >= row["end"]:
                    break
                candidate_index += 1
            if cursor < row["end"]:
                piece = dict(row)
                piece["values"] = list(row["values"])
                piece["start"] = cursor
                piece["end"] = row["end"]
                output.append(piece)

        for _, _, replacement_rows in updates:
            output.extend(replacement_rows)
    return coalesce_rows(output)


def coalesce_rows(rows):
    ordered = sorted(rows, key=lambda row: (
        row["net"], row["sta"], row["start"], row["end"],
        row["band"], row["location"],
    ))
    output = []
    for row in ordered:
        if (
            output
            and output[-1]["values"] == row["values"]
            and output[-1]["end"] == row["start"]
        ):
            output[-1]["end"] = row["end"]
        else:
            copied = dict(row)
            copied["values"] = list(row["values"])
            output.append(copied)
    return output


def write_pal_rows(path, rows):
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        for row in coalesce_rows(rows):
            writer.writerow(
                row["values"] + [str(row["start"]), str(row["end"])]
            )
    os.replace(partial, path)


def write_audit(path, rows):
    fields = (
        "date", "net_sta", "downloaded_selector", "components",
        "previous_selectors", "status", "removed_files", "message",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def scan_waveform_day(day_start, raw_root):
    day_code = day_start.strftime("%Y%m%d")
    day_dir = raw_root / day_code
    if not day_dir.exists():
        return None
    combinations, read_errors = waveform_combinations(day_dir)
    selected = preferred_waveform_combinations(
        combinations, loc_codes, chn_codes
    )
    removed = prune_waveform_combinations(combinations, selected)
    choices = []
    for net_sta, (key, details) in selected.items():
        choices.append((net_sta, key, set(details["components"])))
    return {
        "day_start": day_start,
        "day_code": day_code,
        "choices": choices,
        "read_errors": read_errors,
        "removed_files": len(removed),
    }


def main():
    if NUM_WORKERS < 1:
        raise ValueError("NUM_WORKERS must be positive")
    station_path = resolve_path(STATION_FILE)
    audit_path = resolve_path(AUDIT_FILE)
    backup_path = resolve_path(BACKUP_FILE)
    raw_root = resolve_path(RAW_ROOT)
    station_rows = read_pal_rows(station_path)
    networks = sorted({row["net"].lower() for row in station_rows})
    fullfed_records = read_fullfed_records(
        Path(FULLFED_TEMPLATE.format(network)) for network in networks
    )
    audit_rows = []
    replacements = defaultdict(list)
    station_rows_by_net_sta = defaultdict(list)
    for row in station_rows:
        station_rows_by_net_sta[(row["net"], row["sta"])].append(row)

    days = list(iter_days(TIME_RANGE))
    scans = []
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {
            executor.submit(scan_waveform_day, day, raw_root): day
            for day in days
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result is not None:
                scans.append(result)
            if completed % 100 == 0 or completed == len(futures):
                print("scanned {}/{} daily waveform directories".format(
                    completed, len(futures)
                ))

    for scan in sorted(scans, key=lambda item: item["day_start"]):
        day_start = scan["day_start"]
        day_end = day_start + 86400
        day_code = scan["day_code"]
        removed_files = scan["removed_files"]

        for net_sta, key, components_set in sorted(scan["choices"]):
            selector = selector_text(key)
            components = "".join(sorted(components_set))
            source_rows = station_rows_by_net_sta.get(net_sta, [])
            previous = overlapping_selectors(
                source_rows, net_sta, day_start, day_end
            )
            exact_rows = matching_rows(source_rows, net_sta, key)
            if len(components_set) not in (1, 3):
                status = "unsupported_component_set"
                message = "expected one component or E/N/Z"
            elif (
                covers_day(exact_rows, day_start, day_end)
                and set(previous) == {selector}
            ):
                status = "covered"
                message = ""
            else:
                replacement, used_nearest = fullfed_day_rows(
                    key, fullfed_records, day_start, day_end
                )
                if not replacement:
                    status = "unresolved"
                    message = "no matching fullfed gain metadata"
                else:
                    replacements[net_sta].append(
                        (day_start, day_end, replacement)
                    )
                    status = "updated"
                    message = (
                        "used nearest exact-selector fullfed gain epoch"
                        if used_nearest else ""
                    )
            audit_rows.append({
                "date": day_code,
                "net_sta": ".".join(net_sta),
                "downloaded_selector": selector,
                "components": components,
                "previous_selectors": ";".join(previous),
                "status": status,
                "removed_files": removed_files,
                "message": message,
            })

        for message in scan["read_errors"]:
            audit_rows.append({
                "date": day_code,
                "net_sta": "",
                "downloaded_selector": "",
                "components": "",
                "previous_selectors": "",
                "status": "read_error",
                "removed_files": removed_files,
                "message": message,
            })

    if replacements:
        station_rows = apply_station_replacements(station_rows, replacements)
        final_rows_by_net_sta = defaultdict(list)
        for row in station_rows:
            final_rows_by_net_sta[(row["net"], row["sta"])].append(row)
        verification_errors = []
        for net_sta, updates in replacements.items():
            for day_start, day_end, replacement in updates:
                key = (
                    net_sta[0], net_sta[1],
                    replacement[0]["location"], replacement[0]["band"],
                )
                exact_rows = matching_rows(
                    final_rows_by_net_sta[net_sta], net_sta, key
                )
                if not covers_day(exact_rows, day_start, day_end):
                    verification_errors.append(
                        "{} {}".format(".".join(net_sta), day_start.date)
                    )
        if verification_errors:
            raise RuntimeError(
                "reconciled station coverage verification failed: {}".format(
                    ", ".join(verification_errors[:20])
                )
            )
        if not backup_path.exists():
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(station_path, backup_path)
        write_pal_rows(station_path, station_rows)
        print("updated station file: {}".format(station_path))
        print("original station file: {}".format(backup_path))
    else:
        print("station file unchanged: {}".format(station_path))

    write_audit(audit_path, audit_rows)
    unresolved = sum(
        row["status"] in {"unresolved", "unsupported_component_set"}
        for row in audit_rows
    )
    print("reconciliation audit: {}".format(audit_path))
    status_counts = Counter(row["status"] for row in audit_rows)
    print("station-day status: {}".format(
        ", ".join(
            "{}={}".format(status, count)
            for status, count in sorted(status_counts.items())
        ) or "none"
    ))
    print("checked {} station-days; unresolved {}".format(
        sum(bool(row["net_sta"]) for row in audit_rows), unresolved
    ))


if __name__ == "__main__":
    main()
