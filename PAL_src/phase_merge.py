"""Merge duplicate PAL events detected by multiple station subnetworks."""

import csv
from datetime import datetime, timedelta
from math import cos, hypot, pi
from pathlib import Path
from statistics import median

from pick_ensemble import (
    format_picker_cluster_sizes, merge_picker_cluster_sizes,
)


PICK_PROVENANCE_PRIORITY = (
    "both_groups", "pos_only", "pos_neg_only", "initial",
)


def resolve_pick_provenance(values):
    """Resolve merged provenance to one final station-pick classification."""
    tokens = {
        token.strip().lower()
        for value in values
        for token in str(value or "initial").split("|")
        if token.strip()
    }
    for provenance in PICK_PROVENANCE_PRIORITY:
        if provenance in tokens:
            return provenance
    return "initial"


def select_preferred_provenance_picks(picks):
    """Return the highest-priority provenance and picks carrying that label."""
    picks = list(picks)
    provenance = resolve_pick_provenance(
        pick.get("pick_provenance", "initial") for pick in picks
    )
    selected = [
        pick for pick in picks
        if resolve_pick_provenance([
            pick.get("pick_provenance", "initial")
        ]) == provenance
    ]
    return provenance, selected


def parse_time(value):
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    if "." in text:
        whole, fraction = text.rsplit(".", 1)
        if fraction.isdigit():
            text = whole + "." + fraction[:6].ljust(6, "0")
    return datetime.fromisoformat(text)


def format_time(value, digits=6):
    if digits <= 0:
        return value.isoformat(timespec="seconds") + "Z"
    if digits <= 3:
        text = value.isoformat(timespec="milliseconds")
        if digits < 3:
            text = text[:-(3 - digits)]
        return text + "Z"
    text = value.isoformat(timespec="microseconds")
    if digits < 6:
        text = text[:-(6 - digits)]
    return text + "Z"


def median_time(values):
    base = min(values)
    offsets = [(value - base).total_seconds() for value in values]
    return base + timedelta(seconds=median(offsets))


def median_valid(values, default=-1.0):
    valid = [float(value) for value in values if float(value) >= 0]
    return median(valid) if valid else default


def horizontal_distance_km(left, right):
    lat0 = 0.5 * (left["lat"] + right["lat"])
    dx = (right["lon"] - left["lon"]) * 111.32 * cos(lat0 * pi / 180.0)
    dy = (right["lat"] - left["lat"]) * 111.32
    return hypot(dx, dy)


def is_event_header(codes):
    if len(codes) != 5 or "T" not in codes[0]:
        return False
    try:
        [float(value) for value in codes[1:]]
    except ValueError:
        return False
    return True


def read_phase_file(path, source=None):
    path = Path(path)
    events = []
    current = None
    with path.open(encoding="utf-8") as fp:
        for line_number, line in enumerate(fp, start=1):
            text = line.strip()
            if not text:
                continue
            codes = [value.strip() for value in text.split(",")]
            if is_event_header(codes):
                if current is not None:
                    events.append(current)
                current = {
                    "source": source or str(path),
                    "time": parse_time(codes[0]),
                    "lat": float(codes[1]),
                    "lon": float(codes[2]),
                    "depth": float(codes[3]),
                    "mag": float(codes[4]),
                    "picks": [],
                }
                continue
            if current is None or len(codes) < 4:
                raise ValueError("bad phase row {}:{}: {}".format(path, line_number, text))
            current["picks"].append({
                "sta": codes[0],
                "p": parse_time(codes[1]),
                "s": parse_time(codes[2]),
                "score": float(codes[3]),
                "p_prob": float(codes[4]) if len(codes) > 4 else -1.0,
                "s_prob": float(codes[5]) if len(codes) > 5 else -1.0,
                "tp_std": float(codes[6]) if len(codes) > 6 else 0.0,
                "ts_std": float(codes[7]) if len(codes) > 7 else 0.0,
                "p_prob_std": float(codes[8]) if len(codes) > 8 else 0.0,
                "s_prob_std": float(codes[9]) if len(codes) > 9 else 0.0,
                "num_support": int(codes[10]) if len(codes) > 10 else 1,
                "sources": codes[11] if len(codes) > 11 else "",
                "picker_cluster_sizes": (
                    codes[12] if len(codes) > 12 else ""
                ),
                "picker_uncertainties": (
                    codes[13] if len(codes) > 13 else ""
                ),
                "pick_provenance": (
                    codes[14] if len(codes) > 14 else "initial"
                ),
                "repick_status": (
                    codes[15] if len(codes) > 15 else "unknown"
                ),
                "repick_support": (
                    int(codes[16]) if len(codes) > 16 and codes[16] else -1
                ),
                "repick_sources": codes[17] if len(codes) > 17 else "",
                "repick_required_support": (
                    int(codes[18]) if len(codes) > 18 and codes[18] else -1
                ),
                "p_snr_e": float(codes[19]) if len(codes) > 19 else -1.0,
                "p_snr_n": float(codes[20]) if len(codes) > 20 else -1.0,
                "p_snr_z": float(codes[21]) if len(codes) > 21 else -1.0,
            })
    if current is not None:
        events.append(current)
    return events


def write_phase_file(path, events, time_format_digits=6, catalog_path=None):
    """Atomically write events using the canonical extended phase schema."""
    path = Path(path)
    catalog_path = Path(catalog_path) if catalog_path else None
    partial = path.with_suffix(path.suffix + ".partial")
    catalog_partial = (
        catalog_path.with_suffix(catalog_path.suffix + ".partial")
        if catalog_path is not None else None
    )
    with partial.open("w", encoding="utf-8") as phase_fp:
        catalog_fp = (
            catalog_partial.open("w", encoding="utf-8")
            if catalog_partial is not None else None
        )
        try:
            for event in sorted(events, key=lambda item: item["time"]):
                header = "{},{:.5f},{:.5f},{:.1f},{:.2f}\n".format(
                    format_time(event["time"], time_format_digits),
                    event["lat"], event["lon"], event["depth"], event["mag"],
                )
                phase_fp.write(header)
                if catalog_fp is not None:
                    catalog_fp.write(header)
                for pick in sorted(event["picks"], key=lambda item: item["sta"]):
                    phase_fp.write(
                        "{},{},{},{},{:.4f},{:.4f},{:.4f},{:.4f},"
                        "{:.4f},{:.4f},{},{},{},{},{},{},{},{},{},"
                        "{:.4f},{:.4f},{:.4f}\n".format(
                            pick["sta"],
                            format_time(pick["p"], time_format_digits),
                            format_time(pick["s"], time_format_digits),
                            pick.get("score", -1.0),
                            pick.get("p_prob", -1.0),
                            pick.get("s_prob", -1.0),
                            pick.get("tp_std", 0.0),
                            pick.get("ts_std", 0.0),
                            pick.get("p_prob_std", 0.0),
                            pick.get("s_prob_std", 0.0),
                            pick.get("num_support", 1),
                            pick.get("sources", ""),
                            pick.get("picker_cluster_sizes", ""),
                            pick.get("picker_uncertainties", ""),
                            pick.get("pick_provenance", "initial"),
                            pick.get("repick_status", "unknown"),
                            pick.get("repick_support", -1),
                            pick.get("repick_sources", ""),
                            pick.get("repick_required_support", -1),
                            pick.get("p_snr_e", -1.0),
                            pick.get("p_snr_n", -1.0),
                            pick.get("p_snr_z", -1.0),
                        )
                    )
        finally:
            if catalog_fp is not None:
                catalog_fp.close()
    partial.replace(path)
    if catalog_partial is not None:
        catalog_partial.replace(catalog_path)


def phase_file_counts(path):
    events = read_phase_file(path)
    return {
        "num_events": len(events),
        "num_associated_picks": sum(len(event["picks"]) for event in events),
    }


def events_match(left, right, origin_tol, epicenter_tol, depth_tol):
    if abs((right["time"] - left["time"]).total_seconds()) > origin_tol:
        return False
    if horizontal_distance_km(left, right) > epicenter_tol:
        return False
    return abs(right["depth"] - left["depth"]) <= depth_tol


def group_events(events, origin_tol, epicenter_tol, depth_tol,
                 min_shared_stations=0, phase_pick_tol=1.0):
    if not events:
        return []
    parent = list(range(len(events)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    if min_shared_stations > 0:
        by_station = {}
        for event_index, event in enumerate(events):
            for pick in event["picks"]:
                by_station.setdefault(pick["sta"], {}).setdefault(
                    event_index, []
                ).append(pick)
        shared_counts = {}
        for event_picks in by_station.values():
            event_indices = sorted(event_picks)
            for left_pos, left_idx in enumerate(event_indices):
                for right_idx in event_indices[left_pos + 1:]:
                    matched = any(
                        abs((right_pick["p"] - left_pick["p"]).total_seconds())
                        < phase_pick_tol
                        and abs((right_pick["s"] - left_pick["s"]).total_seconds())
                        < phase_pick_tol
                        for left_pick in event_picks[left_idx]
                        for right_pick in event_picks[right_idx]
                    )
                    if matched:
                        pair = (left_idx, right_idx)
                        shared_counts[pair] = shared_counts.get(pair, 0) + 1
        for pair, count in shared_counts.items():
            if count >= min_shared_stations:
                union(*pair)

    indexed = sorted(enumerate(events), key=lambda item: item[1]["time"])
    for left_pos, (left_idx, left_event) in enumerate(indexed):
        for right_idx, right_event in indexed[left_pos + 1:]:
            if (right_event["time"] - left_event["time"]).total_seconds() > origin_tol:
                break
            if events_match(
                left_event, right_event, origin_tol, epicenter_tol, depth_tol
            ):
                union(left_idx, right_idx)

    grouped = {}
    for index, event in enumerate(events):
        grouped.setdefault(find(index), []).append(event)
    groups = [sorted(group, key=lambda item: item["time"]) for group in grouped.values()]
    return sorted(groups, key=lambda group: median_time([item["time"] for item in group]))


def _cluster_station_picks(picks, tolerance_sec):
    groups = []
    for pick in sorted(picks, key=lambda item: (item["p"], item["s"])):
        matching = next((
            group for group in groups
            if abs((pick["p"] - median_time(
                [item["p"] for item in group]
            )).total_seconds()) < tolerance_sec
            and abs((pick["s"] - median_time(
                [item["s"] for item in group]
            )).total_seconds()) < tolerance_sec
        ), None)
        if matching is None:
            groups.append([pick])
        else:
            matching.append(pick)
    return groups


def merge_group(events, phase_pick_tol=1.0):
    picks_by_station = {}
    for event in events:
        for pick in event["picks"]:
            picks_by_station.setdefault(pick["sta"], []).append(pick)
    picks = []
    for station in sorted(picks_by_station):
        for phase_group in _cluster_station_picks(
            picks_by_station[station], phase_pick_tol
        ):
            provenance, station_picks = select_preferred_provenance_picks(
                phase_group
            )
            picks.append({
            "sta": station,
            "p": median_time([pick["p"] for pick in station_picks]),
            "s": median_time([pick["s"] for pick in station_picks]),
            "score": median([pick["score"] for pick in station_picks]),
            "p_prob": median_valid([pick["p_prob"] for pick in station_picks]),
            "s_prob": median_valid([pick["s_prob"] for pick in station_picks]),
            "tp_std": median([pick["tp_std"] for pick in station_picks]),
            "ts_std": median([pick["ts_std"] for pick in station_picks]),
            "p_prob_std": median([pick["p_prob_std"] for pick in station_picks]),
            "s_prob_std": median([pick["s_prob_std"] for pick in station_picks]),
            # Identical ensemble picks may occur in several subnet events.
            "num_support": max(pick["num_support"] for pick in station_picks),
            "sources": "|".join(sorted({
                source
                for pick in station_picks
                for source in pick["sources"].split("|")
                if source
            })),
            "picker_cluster_sizes": format_picker_cluster_sizes(
                merge_picker_cluster_sizes(
                    pick["picker_cluster_sizes"] for pick in station_picks
                )
            ),
            "picker_uncertainties": "|".join(sorted({
                value
                for pick in station_picks
                for value in pick.get("picker_uncertainties", "").split("|")
                if value
            })),
            "pick_provenance": provenance,
            "repick_status": "|".join(sorted({
                pick.get("repick_status", "unknown")
                for pick in station_picks
                if pick.get("repick_status", "unknown")
            })),
            "repick_support": max(
                pick.get("repick_support", -1) for pick in station_picks
            ),
            "repick_sources": "|".join(sorted({
                source
                for pick in station_picks
                for source in pick.get("repick_sources", "").split("|")
                if source
            })),
            "repick_required_support": max(
                pick.get("repick_required_support", -1)
                for pick in station_picks
            ),
            "p_snr_e": median_valid([
                pick.get("p_snr_e", -1.0) for pick in station_picks
            ]),
            "p_snr_n": median_valid([
                pick.get("p_snr_n", -1.0) for pick in station_picks
            ]),
            "p_snr_z": median_valid([
                pick.get("p_snr_z", -1.0) for pick in station_picks
            ]),
            })
    return {
        "time": median_time([event["time"] for event in events]),
        "lat": median([event["lat"] for event in events]),
        "lon": median([event["lon"] for event in events]),
        "depth": median([event["depth"] for event in events]),
        "mag": median_valid([event["mag"] for event in events]),
        "picks": picks,
        "num_input_events": len(events),
        "sources": sorted({event["source"] for event in events}),
        "subnets": sorted({event["source"].split(":", 1)[0] for event in events}),
    }


def event_both_group_pick_ratio(event):
    """Return the phase-pair fraction detected by both repicker groups."""
    picks = event.get("picks", [])
    if not picks:
        return 0.0
    num_both = 0
    for pick in picks:
        provenance = {
            token.strip().lower()
            for token in pick.get("pick_provenance", "initial").split("|")
            if token.strip()
        }
        if "both_groups" in provenance:
            num_both += 1
    return num_both / float(len(picks))


def event_refined_pick_ratio(event):
    """Backward-compatible alias for pre-v14 callers and phase files."""
    return event_both_group_pick_ratio(event)


def merge_phase_files(
    phase_files, output_phase, output_catalog, output_groups, cfg,
    event_time_start=None, event_time_end=None,
    min_both_group_ratio=None,
):
    events = []
    input_counts = {}
    for source, phase_path in sorted(phase_files.items()):
        source_events = read_phase_file(phase_path, source=source)
        input_counts[source] = len(source_events)
        events.extend(source_events)

    groups = group_events(
        events,
        cfg.merge_origin_time_tol_sec,
        cfg.merge_epicenter_tol_km,
        cfg.merge_depth_tol_km,
        cfg.merge_min_shared_phase_stations,
        cfg.merge_phase_pick_time_tol_sec,
    )
    merged = []
    for group in groups:
        event = merge_group(group, cfg.merge_phase_pick_time_tol_sec)
        if event_time_start is not None and event["time"] < event_time_start:
            continue
        if event_time_end is not None and event["time"] >= event_time_end:
            continue
        merged.append(event)
    merged.sort(key=lambda event: event["time"])
    merged_before_both_group_qc = list(merged)
    num_both_group_ratio_rejected = 0
    if min_both_group_ratio is not None:
        accepted = [
            event for event in merged
            if event_both_group_pick_ratio(event) >= min_both_group_ratio
        ]
        num_both_group_ratio_rejected = len(merged) - len(accepted)
        merged = accepted

    output_phase = Path(output_phase)
    output_catalog = Path(output_catalog)
    output_groups = Path(output_groups)
    for path in (output_phase, output_catalog, output_groups):
        path.parent.mkdir(parents=True, exist_ok=True)

    phase_partial = output_phase.with_suffix(output_phase.suffix + ".partial")
    catalog_partial = output_catalog.with_suffix(output_catalog.suffix + ".partial")
    groups_partial = output_groups.with_suffix(output_groups.suffix + ".partial")
    with phase_partial.open("w", encoding="utf-8") as phase_fp, \
            catalog_partial.open("w", encoding="utf-8") as catalog_fp:
        for event in merged:
            header = "{},{:.5f},{:.5f},{:.1f},{:.2f}\n".format(
                format_time(event["time"], cfg.merge_time_format_digits),
                event["lat"], event["lon"], event["depth"], event["mag"],
            )
            phase_fp.write(header)
            catalog_fp.write(header)
            for pick in event["picks"]:
                line = (
                    "{},{},{},{},{:.4f},{:.4f},{:.4f},{:.4f},"
                    "{:.4f},{:.4f},{},{},{},{},{},{},{},{},{},"
                    "{:.4f},{:.4f},{:.4f}\n".format(
                        pick["sta"],
                        format_time(pick["p"], cfg.merge_time_format_digits),
                        format_time(pick["s"], cfg.merge_time_format_digits),
                        pick["score"], pick["p_prob"], pick["s_prob"],
                        pick["tp_std"], pick["ts_std"],
                        pick["p_prob_std"], pick["s_prob_std"],
                        pick["num_support"], pick["sources"],
                        pick["picker_cluster_sizes"],
                        pick.get("picker_uncertainties", ""),
                        pick.get("pick_provenance", "initial"),
                        pick.get("repick_status", "unknown"),
                        pick.get("repick_support", -1),
                        pick.get("repick_sources", ""),
                        pick.get("repick_required_support", -1),
                        pick.get("p_snr_e", -1.0),
                        pick.get("p_snr_n", -1.0),
                        pick.get("p_snr_z", -1.0),
                    )
                )
                phase_fp.write(line)

    with groups_partial.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow([
            "merged_event_id", "num_input_events", "num_sources", "num_picks",
            "time", "lat", "lon", "depth", "mag", "sources",
        ])
        for event_index, event in enumerate(merged):
            writer.writerow([
                event_index, event["num_input_events"], len(event["sources"]),
                len(event["picks"]),
                format_time(event["time"], cfg.merge_time_format_digits),
                event["lat"], event["lon"], event["depth"], event["mag"],
                "|".join(event["sources"]),
            ])

    phase_partial.replace(output_phase)
    catalog_partial.replace(output_catalog)
    groups_partial.replace(output_groups)
    contributing_events = sum(event["num_input_events"] for event in merged)
    contributing_before_both_group_qc = sum(
        event["num_input_events"] for event in merged_before_both_group_qc
    )
    return {
        "num_candidate_input_events": len(events),
        "num_input_events": contributing_events,
        "num_merged_events": len(merged),
        "num_events_both_group_ratio_rejected": num_both_group_ratio_rejected,
        "num_duplicate_events_removed": (
            contributing_before_both_group_qc
            - len(merged_before_both_group_qc)
        ),
        "num_associated_picks": sum(len(event["picks"]) for event in merged),
        "num_multi_subnet_events": sum(
            1 for event in merged if len(event["subnets"]) > 1
        ),
        "input_events_by_source": input_counts,
    }
