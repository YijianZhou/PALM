"""Read and write per-station STA/LTA trigger inventories."""

import csv
import os
from pathlib import Path


TRIGGER_COUNT_SUFFIX = ".trigger_counts.csv"
TRIGGER_COUNT_FIELDS = (
    "date", "net_sta", "num_triggers", "num_accepted_picks",
)


def trigger_count_path(pick_dir, observed_date):
    return Path(pick_dir) / (
        "{}{}".format(str(observed_date)[:10], TRIGGER_COUNT_SUFFIX)
    )


def write_trigger_counts(pick_dir, observed_date, station_counts):
    """Atomically write one daily station trigger inventory."""
    output_path = trigger_count_path(pick_dir, observed_date)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_suffix(output_path.suffix + ".partial")
    date_text = str(observed_date)[:10]
    with partial.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=TRIGGER_COUNT_FIELDS)
        writer.writeheader()
        for net_sta in sorted(station_counts):
            num_triggers, num_accepted = [
                int(value) for value in station_counts[net_sta]
            ]
            if (
                num_triggers < 0 or num_accepted < 0
                or num_triggers < num_accepted
            ):
                raise ValueError(
                    "{} has {} accepted picks but only {} triggers".format(
                        net_sta, num_accepted, num_triggers
                    )
                )
            if num_triggers == 0 and num_accepted == 0:
                continue
            writer.writerow({
                "date": date_text,
                "net_sta": net_sta,
                "num_triggers": num_triggers,
                "num_accepted_picks": num_accepted,
            })
    os.replace(partial, output_path)
    return output_path


def read_trigger_counts(pick_dir, observed_date, required=True):
    """Return ``NET.STA -> (raw triggers, accepted picks)`` for one day."""
    input_path = trigger_count_path(pick_dir, observed_date)
    if not input_path.exists():
        if required:
            raise FileNotFoundError(
                "STA/LTA trigger inventory is missing: {}. Re-run PAL "
                "picking with the current source before association.".format(
                    input_path
                )
            )
        return None

    expected_date = str(observed_date)[:10]
    counts = {}
    with input_path.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        missing = set(TRIGGER_COUNT_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "{} missing trigger-count columns: {}".format(
                    input_path, ", ".join(sorted(missing))
                )
            )
        for row in reader:
            if row["date"] != expected_date:
                raise ValueError(
                    "{} contains date {}, expected {}".format(
                        input_path, row["date"], expected_date
                    )
                )
            net_sta = row["net_sta"].strip()
            if not net_sta or net_sta in counts:
                raise ValueError(
                    "{} contains invalid or duplicate station {!r}".format(
                        input_path, net_sta
                    )
                )
            num_triggers = int(row["num_triggers"])
            num_accepted = int(row["num_accepted_picks"])
            if (
                num_triggers < 0 or num_accepted < 0
                or num_triggers < num_accepted
            ):
                raise ValueError(
                    "{} has inconsistent counts for {}".format(
                        input_path, net_sta
                    )
                )
            counts[net_sta] = (num_triggers, num_accepted)
    return counts
