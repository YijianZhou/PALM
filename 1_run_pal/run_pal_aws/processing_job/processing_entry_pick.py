#!/usr/bin/env python3
"""Processing-container entry point for one SCEDC AWS PAL picking job."""

import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


WORK_DIR = Path("/opt/ml/processing/work")
PAL_DIR = Path("/opt/ml/processing/pal")
RESUME_DIR = Path("/opt/ml/processing/resume")
RUNTIME_FILE = WORK_DIR / "pick_runtime.json"
REQUIREMENTS_FILE = WORK_DIR / "requirements.txt"


def main():
    if REQUIREMENTS_FILE.exists():
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "-r", str(REQUIREMENTS_FILE),
        ])

    runtime = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    case_code = runtime["case_code"]
    output_dir = WORK_DIR / "output" / case_code
    if RESUME_DIR.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(RESUME_DIR, output_dir, dirs_exist_ok=True)

    sys.path.insert(0, str(PAL_DIR))
    sys.path.insert(0, str(WORK_DIR))
    config_module = importlib.import_module(
        "config_aws_%s" % case_code
    )
    from pick_runner import run_parallel_aws_pick

    def work_path(value):
        path = Path(value)
        return path if path.is_absolute() else WORK_DIR / path

    run_parallel_aws_pick(
        time_range=runtime["time_range"],
        station_file=work_path(runtime["station_file"]),
        pick_dir=work_path(runtime["pick_dir"]),
        log_dir=work_path(runtime["log_dir"]),
        pal_source_dir=PAL_DIR,
        num_workers=int(runtime["num_workers"]),
        config_factory=config_module.Config,
        bucket=runtime["bucket"],
        region=runtime["region"],
        root_prefix=runtime["root_prefix"],
        access_mode=runtime["access_mode"],
        location_priority=tuple(runtime["location_priority"]),
        acceleration_instrument_codes=tuple(
            runtime["acceleration_instrument_codes"]
        ),
        overwrite=bool(runtime["overwrite"]),
        retry_failed_dates=bool(runtime["retry_failed_dates"]),
    )


if __name__ == "__main__":
    main()
