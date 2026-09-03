#!/usr/bin/env python3
"""Run local parallel daily PAL picking as a separate workflow step."""

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
PICK_DIR = Path("output/%s/picks" % CASE_CODE)
LOG_DIR = Path("output/%s/pick_logs" % CASE_CODE)
TIME_RANGE = "20190704-20190707"  # Exclusive end date.

# ============================================================================
# USER SETTINGS: EXECUTION
# ============================================================================
NUM_WORKERS = 3
OVERWRITE = False


# ============================================================================
# CONNECTION CODE: normally no edits are needed below this line
# ============================================================================
RUN_DIR = Path(__file__).resolve().parent
PAL_DIR = Path(
    os.environ.get("PAL_DIR", str(PALM_ROOT / "PAL_src"))
)
sys.path.insert(0, str(PAL_DIR))

cfg = importlib.import_module("config_%s" % CASE_CODE)
from pick_runner import run_parallel_local_pick


def case_path(path):
    return path if path.is_absolute() else RUN_DIR / path


def main():
    run_parallel_local_pick(
        time_range=TIME_RANGE,
        data_dir=DATA_DIR,
        station_file=case_path(STATION_FILE),
        pick_dir=case_path(PICK_DIR),
        log_dir=case_path(LOG_DIR),
        num_workers=NUM_WORKERS,
        config_factory=cfg.Config,
        overwrite=OVERWRITE,
        include_association_halo=False,
    )


if __name__ == "__main__":
    main()
