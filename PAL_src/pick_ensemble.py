"""P/S-pair consensus utilities for sliding windows and picker ensembles."""
from datetime import datetime, timezone
import math
from pathlib import Path
from statistics import median, pstdev


EXTENDED_PICK_COLUMNS = (
    "net_sta", "tp", "ts", "s_amp", "p_prob", "s_prob",
    "tp_std", "ts_std", "p_prob_std", "s_prob_std",
    "num_support", "sources", "picker_cluster_sizes",
)


_PICKER_ORDER = {"SAR": 0, "FT": 1, "PHN": 2, "RUN": 3}


def parse_picker_cluster_sizes(value):
    """Return ``MODEL:count`` provenance as a normalized dictionary."""
    if isinstance(value, dict):
        items = value.items()
    else:
        items = []
        for token in str(value or "").split("|"):
            if not token or ":" not in token:
                continue
            name, count = token.rsplit(":", 1)
            items.append((name, count))
    parsed = {}
    for name, count in items:
        name = str(name).strip()
        if not name:
            continue
        try:
            count = int(count)
        except (TypeError, ValueError):
            continue
        if count >= 0:
            parsed[name] = max(parsed.get(name, 0), count)
    return parsed


def format_picker_cluster_sizes(value):
    """Serialize per-model window support in stable preferred-picker order."""
    values = parse_picker_cluster_sizes(value)
    names = sorted(values, key=lambda name: (_PICKER_ORDER.get(name, 1000), name))
    return "|".join("{}:{}".format(name, values[name]) for name in names)


def merge_picker_cluster_sizes(values):
    """Merge repeated provenance without double-counting subnet/window copies."""
    merged = {}
    for value in values:
        for name, count in parse_picker_cluster_sizes(value).items():
            merged[name] = max(merged.get(name, 0), count)
    return merged

def to_epoch(value):
    """Convert UTCDateTime, datetime, numeric, or ISO text to Unix seconds."""
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()


def format_epoch(value, digits=6):
    dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    if digits <= 0:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    fraction = "{:06d}".format(dt.microsecond)[:digits]
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "." + fraction + "Z"


def _field(record, name, default=None):
    if isinstance(record, dict):
        return record.get(name, default)
    try:
        return record[name]
    except (KeyError, ValueError, TypeError, IndexError):
        return default


def _valid_floats(values, minimum=None):
    valid = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        if minimum is not None and number < minimum:
            continue
        valid.append(number)
    return valid


def _median_or(values, default=-1.0, minimum=None):
    valid = _valid_floats(values, minimum=minimum)
    return float(median(valid)) if valid else float(default)


def _std(values):
    valid = _valid_floats(values)
    return float(pstdev(valid)) if len(valid) > 1 else 0.0


def cluster_pair_votes(
    records, tp_dev, ts_dev, min_support=1, source_field="source",
):
    """Cluster records only when both P and S arrivals match.

    Each distinct source contributes at most one equally weighted vote to a
    cluster. If a source has multiple P/S combinations in one cluster, its
    highest summed P+S probability is retained before median aggregation.
    """
    normalized = []
    for record in records:
        tp = to_epoch(_field(record, "tp"))
        ts = to_epoch(_field(record, "ts"))
        if ts <= tp:
            continue
        source = str(_field(record, source_field, ""))
        normalized.append({
            "tp": tp,
            "ts": ts,
            "p_prob": float(_field(record, "p_prob", -1.0)),
            "s_prob": float(_field(record, "s_prob", -1.0)),
            "s_amp": _field(record, "s_amp", -1.0),
            "num_support": int(_field(record, "num_support", 1)),
            "picker_cluster_sizes": parse_picker_cluster_sizes(
                _field(record, "picker_cluster_sizes", "")
            ),
            "source": source,
        })
    if not normalized:
        return []

    order = sorted(range(len(normalized)), key=lambda idx: normalized[idx]["tp"])
    parent = list(range(len(normalized)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_pos, left_idx in enumerate(order):
        left = normalized[left_idx]
        for right_idx in order[left_pos + 1:]:
            right = normalized[right_idx]
            if right["tp"] - left["tp"] >= float(tp_dev):
                break
            if abs(right["ts"] - left["ts"]) < float(ts_dev):
                union(left_idx, right_idx)

    grouped = {}
    for index in range(len(normalized)):
        grouped.setdefault(find(index), []).append(normalized[index])

    consensus = []
    for cluster in grouped.values():
        by_source = {}
        for record in cluster:
            score = record["p_prob"] + record["s_prob"]
            current = by_source.get(record["source"])
            if current is None or score > current[0]:
                by_source[record["source"]] = (score, record)
        votes = [item[1] for item in by_source.values()]
        if len(votes) < int(min_support):
            continue
        tp_values = [record["tp"] for record in votes]
        ts_values = [record["ts"] for record in votes]
        p_prob_values = [record["p_prob"] for record in votes]
        s_prob_values = [record["s_prob"] for record in votes]
        consensus.append({
            "tp": float(median(tp_values)),
            "ts": float(median(ts_values)),
            "s_amp": _median_or(
                [record["s_amp"] for record in votes], minimum=0.0
            ),
            "p_prob": float(median(p_prob_values)),
            "s_prob": float(median(s_prob_values)),
            "tp_std": _std(tp_values),
            "ts_std": _std(ts_values),
            "p_prob_std": _std(p_prob_values),
            "s_prob_std": _std(s_prob_values),
            "num_support": len(votes),
            "sources": sorted(by_source),
            "picker_cluster_sizes": merge_picker_cluster_sizes([
                record["picker_cluster_sizes"] or {
                    record["source"]: record["num_support"]
                }
                for record in votes
                if record["source"]
            ]),
        })
    return sorted(consensus, key=lambda item: (item["tp"], item["ts"]))


def format_pick_row(record, time_digits=6):
    sources = record.get("sources", [])
    if isinstance(sources, str):
        sources = [value for value in sources.split("|") if value]
    cluster_sizes = parse_picker_cluster_sizes(
        record.get("picker_cluster_sizes", "")
    )
    if not cluster_sizes and len(sources) == 1:
        cluster_sizes[sources[0]] = int(record.get("num_support", 1))
    return ",".join([
        str(record["net_sta"]),
        format_epoch(record["tp"], time_digits),
        format_epoch(record["ts"], time_digits),
        str(record.get("s_amp", -1.0)),
        "{:.4f}".format(float(record.get("p_prob", -1.0))),
        "{:.4f}".format(float(record.get("s_prob", -1.0))),
        "{:.4f}".format(float(record.get("tp_std", 0.0))),
        "{:.4f}".format(float(record.get("ts_std", 0.0))),
        "{:.4f}".format(float(record.get("p_prob_std", 0.0))),
        "{:.4f}".format(float(record.get("s_prob_std", 0.0))),
        str(int(record.get("num_support", 1))),
        "|".join(sorted(set(sources))),
        format_picker_cluster_sizes(cluster_sizes),
    ]) + "\n"


def read_pick_file(path, picker_name=None):
    records = []
    path = Path(path)
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as fp:
        for line_number, line in enumerate(fp, start=1):
            text = line.strip()
            if not text:
                continue
            codes = [value.strip() for value in text.split(",")]
            if len(codes) < 6:
                raise ValueError(
                    "{}:{} expected at least 6 pick columns".format(
                        path, line_number
                    )
                )
            sources = (
                [value for value in codes[11].split("|") if value]
                if len(codes) > 11 and codes[11]
                else ([picker_name] if picker_name else [])
            )
            cluster_sizes = parse_picker_cluster_sizes(
                codes[12] if len(codes) > 12 else ""
            )
            if not cluster_sizes and picker_name:
                cluster_sizes[picker_name] = (
                    int(codes[10]) if len(codes) > 10 else 1
                )
            records.append({
                "net_sta": codes[0],
                "tp": to_epoch(codes[1]),
                "ts": to_epoch(codes[2]),
                "s_amp": float(codes[3]),
                "p_prob": float(codes[4]),
                "s_prob": float(codes[5]),
                "tp_std": float(codes[6]) if len(codes) > 6 else 0.0,
                "ts_std": float(codes[7]) if len(codes) > 7 else 0.0,
                "p_prob_std": float(codes[8]) if len(codes) > 8 else 0.0,
                "s_prob_std": float(codes[9]) if len(codes) > 9 else 0.0,
                "num_support": int(codes[10]) if len(codes) > 10 else 1,
                "sources": sources,
                "picker_cluster_sizes": cluster_sizes,
                "source": str(picker_name or (sources[0] if sources else "")),
            })
    return records


def merge_picker_records(
    records_by_picker, tp_dev, ts_dev, min_support=1,
):
    """Merge in-memory picker records into equal-weight station consensus."""
    by_station = {}
    input_counts = {}
    for picker_name, records in sorted(records_by_picker.items()):
        input_counts[picker_name] = len(records)
        for record in records:
            net_sta = str(_field(record, "net_sta"))
            by_station.setdefault(net_sta, []).append({
                "net_sta": net_sta,
                "tp": _field(record, "tp"),
                "ts": _field(record, "ts"),
                "s_amp": _field(record, "s_amp", -1.0),
                "p_prob": _field(record, "p_prob", -1.0),
                "s_prob": _field(record, "s_prob", -1.0),
                "num_support": _field(record, "num_support", 1),
                "picker_cluster_sizes": _field(
                    record, "picker_cluster_sizes",
                    {picker_name: int(_field(record, "num_support", 1))},
                ),
                "source": picker_name,
            })

    merged = []
    for net_sta in sorted(by_station):
        consensus = cluster_pair_votes(
            by_station[net_sta], tp_dev, ts_dev,
            min_support=min_support, source_field="source",
        )
        for record in consensus:
            record["net_sta"] = net_sta
            merged.append(record)
    merged.sort(key=lambda item: (item["tp"], item["net_sta"], item["ts"]))
    return merged, input_counts

def merge_picker_pick_files(
    picker_files, output_path, tp_dev, ts_dev, min_support=1,
):
    """Merge one same-time pick file per picker into one canonical file."""
    by_station = {}
    input_counts = {}
    for picker_name, pick_path in sorted(picker_files.items()):
        records = read_pick_file(pick_path, picker_name=picker_name)
        input_counts[picker_name] = len(records)
        for record in records:
            # Picker name, not sliding-window support, is the ensemble vote.
            record["source"] = picker_name
            by_station.setdefault(record["net_sta"], []).append(record)

    merged = []
    for net_sta in sorted(by_station):
        station_consensus = cluster_pair_votes(
            by_station[net_sta], tp_dev, ts_dev,
            min_support=min_support, source_field="source",
        )
        for record in station_consensus:
            record["net_sta"] = net_sta
            merged.append(record)
    merged.sort(key=lambda item: (item["tp"], item["net_sta"], item["ts"]))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_suffix(output_path.suffix + ".partial")
    with partial.open("w", encoding="utf-8") as fp:
        for record in merged:
            fp.write(format_pick_row(record))
    partial.replace(output_path)
    return {
        "output_path": str(output_path),
        "num_merged_picks": len(merged),
        "input_counts": input_counts,
    }


def merge_pick_directories(
    picker_dirs, output_dir, tp_dev, ts_dev, min_support=1, filenames=None,
):
    """Merge matching daily pick files from all configured picker branches."""
    picker_dirs = {
        name: Path(directory) for name, directory in picker_dirs.items()
    }
    file_sets = {
        name: {path.name for path in directory.glob("*.pick")}
        for name, directory in picker_dirs.items()
    }
    if not file_sets:
        raise ValueError("at least one picker directory is required")
    expected = set(filenames) if filenames is not None else None
    for picker_name, names in sorted(file_sets.items()):
        if expected is None:
            expected = names
        elif filenames is not None:
            missing = expected - names
            if missing:
                raise ValueError(
                    "picker files missing for {}: {}".format(
                        picker_name, sorted(missing)
                    )
                )
        elif names != expected:
            raise ValueError(
                "picker file sets differ for {}: missing={}, extra={}".format(
                    picker_name, sorted(expected - names), sorted(names - expected)
                )
            )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for filename in sorted(expected or []):
        summaries.append(merge_picker_pick_files(
            {
                name: directory / filename
                for name, directory in picker_dirs.items()
            },
            output_dir / filename,
            tp_dev,
            ts_dev,
            min_support=min_support,
        ))
    return summaries
