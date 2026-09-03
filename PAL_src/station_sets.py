"""Station-network selection helpers for AI-PAL association workflows."""
from pathlib import Path


def association_station_file_mapping(cfg, full_station_file, subnet_station_files):
    """Use one full network or map optional files to ordered subnet configs."""
    subnet_station_files = [Path(path) for path in subnet_station_files]
    if not subnet_station_files:
        if full_station_file is None:
            raise ValueError(
                "FULL_STATION_FILE and SUBNET_STATION_FILES cannot both be empty"
            )
        full_station_file = Path(full_station_file)
        return {"full": full_station_file}

    configured = getattr(cfg, "subnet_assoc_params", {})
    subnet_names = [
        name for name in configured
        if name not in ("default", "full")
    ]
    if len(subnet_station_files) > len(subnet_names):
        raise ValueError(
            "{} subnet station files were provided, but subnet_assoc_params "
            "defines only {} ordered subnet keys: {}".format(
                len(subnet_station_files), len(subnet_names), subnet_names
            )
        )
    return {
        name: station_file
        for name, station_file in zip(subnet_names, subnet_station_files)
    }


def build_station_union(subnet_station_files, output_path):
    """Write a selector-deduplicated union of subnet station files."""
    station_files = [Path(path) for path in subnet_station_files]
    if not station_files:
        raise ValueError(
            "SUBNET_STATION_FILES is required when FULL_STATION_FILE is None"
        )
    rows = {}
    for station_file in station_files:
        if not station_file.is_file():
            raise FileNotFoundError(station_file)
        with station_file.open(encoding="utf-8") as fp:
            for line_number, line in enumerate(fp, start=1):
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                selector = text.split(",", 1)[0].strip()
                if not selector:
                    raise ValueError(
                        "empty station selector at {}:{}".format(
                            station_file, line_number
                        )
                    )
                previous = rows.get(selector)
                if previous is not None and previous != text:
                    raise ValueError(
                        "conflicting rows for station selector {}: {!r} vs {!r}"
                        .format(selector, previous, text)
                    )
                rows[selector] = text

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_suffix(output_path.suffix + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as fp:
        fp.write("# Union generated from: {}\n".format(
            ", ".join(str(path) for path in station_files)
        ))
        for selector in sorted(rows):
            fp.write(rows[selector] + "\n")
    partial.replace(output_path)
    return output_path
