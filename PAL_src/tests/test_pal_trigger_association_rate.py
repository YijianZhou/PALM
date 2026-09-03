import csv
import sys
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
from obspy import UTCDateTime


PAL_SRC = Path(__file__).resolve().parents[1]
if str(PAL_SRC) not in sys.path:
    sys.path.insert(0, str(PAL_SRC))

import association_runner
from trigger_counts import read_trigger_counts, write_trigger_counts


PICK_DTYPE = [
    ("net_sta", "O"), ("sta_ot", "O"), ("tp", "O"),
    ("ts", "O"), ("s_amp", "O"),
]


def test_association_rate_uses_raw_triggers_and_keeps_qc_rejections():
    observed_date = date(2020, 1, 2)
    picks = np.array([
        (
            "CI.AAA", UTCDateTime("2020-01-02T00:00:00"),
            UTCDateTime("2020-01-02T00:00:01"),
            UTCDateTime("2020-01-02T00:00:03"), 1.0,
        ),
        (
            "CI.AAA", UTCDateTime("2020-01-02T01:00:00"),
            UTCDateTime("2020-01-02T01:00:01"),
            UTCDateTime("2020-01-02T01:00:03"), 1.0,
        ),
    ], dtype=PICK_DTYPE)

    with TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir) / "association_rate.csv"
        associated = [(
            "CI.AAA", UTCDateTime("2020-01-02T00:00:01"),
            UTCDateTime("2020-01-02T00:00:03"),
        )]
        with patch.object(
            association_runner,
            "associated_phase_picks_for_date",
            return_value=associated,
        ):
            summary = association_runner.write_association_rate_from_picks(
                observed_date,
                picks,
                [],
                output_path,
                trigger_counts={"CI.AAA": (10, 2), "CI.BBB": (4, 0)},
            )

        with output_path.open(newline="", encoding="utf-8") as fp:
            rows = {row["net_sta"]: row for row in csv.DictReader(fp)}

    assert rows["CI.AAA"]["num_picks"] == "10"
    assert rows["CI.AAA"]["num_associated_picks"] == "1"
    assert rows["CI.AAA"]["num_unassociated_picks"] == "9"
    assert rows["CI.AAA"]["association_ratio"] == "0.10000000"
    assert rows["CI.BBB"]["num_picks"] == "4"
    assert rows["CI.BBB"]["num_associated_picks"] == "0"
    assert rows["CI.BBB"]["num_unassociated_picks"] == "4"
    assert summary["total_picks"] == 14
    assert summary["accepted_picks"] == 2
    assert summary["associated_picks"] == 1
    assert summary["association_denominator"] == "stalta_triggers"


def test_trigger_inventory_round_trip():
    observed_date = date(2020, 1, 2)
    with TemporaryDirectory() as temp_dir:
        output_path = write_trigger_counts(
            temp_dir,
            observed_date,
            {"CI.AAA": (10, 2), "CI.BBB": (0, 0)},
        )
        counts = read_trigger_counts(temp_dir, observed_date)

    assert output_path.name == "2020-01-02.trigger_counts.csv"
    assert counts == {"CI.AAA": (10, 2)}
