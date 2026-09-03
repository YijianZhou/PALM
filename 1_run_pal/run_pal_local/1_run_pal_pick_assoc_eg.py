#!/usr/bin/env python3
"""Run local independent-day PAL picking followed by association."""

import importlib
import os
import sys
from pathlib import Path

# ============================================================================
# USER SETTINGS: INPUTS AND OUTPUTS
# ============================================================================
PALM_ROOT = Path("~/software/PALM").expanduser()  # Installed source package.
CASE_CODE = "eg"  # Packaged example; drives the case config and all output paths.
STATION_FILE = Path("input/example_pal_format1.sta")
DATA_DIR = Path("/data/Example_data")
OUT_ROOT = Path("output/%s" % CASE_CODE)
TIME_RANGE = "20190704-20190707"  # Exclusive end date.

# ============================================================================
# USER SETTINGS: EXECUTION
# ============================================================================
NUM_PICK_WORKERS = 3
NUM_ASSOC_WORKERS = 3
OVERWRITE_PICKS = False
OVERWRITE_ASSOC = False
RETRY_FAILED_ASSOC_DAYS = True


# ============================================================================
# CONNECTION CODE: normally no edits are needed below this line
# ============================================================================
RUN_DIR = Path(__file__).resolve().parent
PAL_DIR = Path(
    os.environ.get("PAL_DIR", str(PALM_ROOT / "PAL_src"))
)
sys.path.insert(0, str(PAL_DIR))

cfg = importlib.import_module("config_%s" % CASE_CODE)
from association_runner import run_buffered_association
from pick_runner import run_parallel_local_pick


def case_path(path):
    return path if path.is_absolute() else RUN_DIR / path


def main():
    station_file = case_path(STATION_FILE)
    out_root = case_path(OUT_ROOT)
    pick_dir = out_root / "picks"
    run_parallel_local_pick(
        time_range=TIME_RANGE,
        data_dir=DATA_DIR,
        station_file=station_file,
        pick_dir=pick_dir,
        log_dir=out_root / "pick_logs",
        num_workers=NUM_PICK_WORKERS,
        config_factory=cfg.Config,
        overwrite=OVERWRITE_PICKS,
        include_association_halo=False,
    )
    run_buffered_association(
        subnet_station_files={"full": station_file},
        pick_dir=pick_dir,
        assoc_root=out_root / "association",
        time_range=TIME_RANGE,
        num_workers=NUM_ASSOC_WORKERS,
        config_factory=cfg.Config,
        overwrite=OVERWRITE_ASSOC,
        retry_failed_days=RETRY_FAILED_ASSOC_DAYS,
        association_buffer_enabled=False,
    )


if __name__ == "__main__":
    main()
