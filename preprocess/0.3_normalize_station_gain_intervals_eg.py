#!/usr/bin/env python3
"""Normalize time-varying gain intervals in a prepared station file."""

import os
import sys
from pathlib import Path

from preprocess_common import resolve_path


# ============================================================================
# USER SETTINGS: INPUT, OUTPUT, AND STUDY COVERAGE
# ============================================================================
CASE_CODE = "eg"
INPUT_STATION_FILE = Path("output/station_%s_raw.csv" % CASE_CODE)
OUTPUT_STATION_FILE = Path("output/station_%s.csv" % CASE_CODE)
AUDIT_FILE = Path("output/station_%s_gain_interval_audit.csv" % CASE_CODE)
STUDY_START = "2019-07-01"
STUDY_END = "2019-08-01"  # Exclusive.


# ============================================================================
# CONNECTION CODE: normally no edits are needed below this line
# ============================================================================
PALM_ROOT = Path(__file__).resolve().parents[1]
PAL_DIR = Path(os.environ.get("PAL_DIR", str(PALM_ROOT / "PAL_src")))
sys.path.insert(0, str(PAL_DIR))

from data_pipeline import normalize_station_gain_intervals


def main():
    input_path = resolve_path(INPUT_STATION_FILE)
    output_path = resolve_path(OUTPUT_STATION_FILE)
    audit_path = resolve_path(AUDIT_FILE)
    summary = normalize_station_gain_intervals(
        input_path=input_path,
        output_path=output_path,
        audit_path=audit_path,
        coverage_start=STUDY_START,
        coverage_end=STUDY_END,
        group_by_station=True,
    )
    print("normalized station gain intervals: {}".format(summary))
    print("station file: {}".format(output_path))
    print("audit file: {}".format(audit_path))


if __name__ == "__main__":
    main()
