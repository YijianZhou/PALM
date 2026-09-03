#!/usr/bin/env python3
"""Run GPU matched-filter detection over contiguous time segments."""

import subprocess
import sys
from pathlib import Path

from obspy import UTCDateTime

RUN_DIR = Path(__file__).resolve().parent
DEFAULT_PALM_ROOT = RUN_DIR.parent
sys.path.insert(0, str(DEFAULT_PALM_ROOT / "MFT_src"))

from workflow import resolve_run_path, source_environment


def resolve_path(path):
  return resolve_run_path(RUN_DIR, path)

# ============================================================================
# USER SETTINGS
# ============================================================================
PALM_ROOT = DEFAULT_PALM_ROOT
CASE_CODE = "eg"
GPU_INDEX = 0
DATA_DIR = Path("/data/Example_data")
TIME_RANGE = "20190704-20190707"  # Exclusive end date.
STATION_FILE = Path("input/example_pal_format1.sta")
TEMPLATE_ROOT = Path("output/Example_templates")
TEMPLATE_PHASE_FILE = Path("input/eg_mft.temp")
OUTPUT_ROOT = Path("output/eg")
SEGMENT_DAYS = 60


def iter_segments(time_range, segment_days):
  start, end = [UTCDateTime(value) for value in time_range.split("-")]
  current = start
  while current < end:
    following = min(current + segment_days * 86400, end)
    yield current, following
    current = following


def compact_date(value):
  return str(value.date).replace("-", "")


def main():
  mft_source = Path(PALM_ROOT).expanduser() / "MFT_src"
  env = source_environment(RUN_DIR, PALM_ROOT, CASE_CODE)
  output_root = resolve_path(OUTPUT_ROOT)
  output_root.mkdir(parents=True, exist_ok=True)
  for start, end in iter_segments(TIME_RANGE, SEGMENT_DAYS):
    label = "{}-{}".format(compact_date(start), compact_date(end))
    command = [
        sys.executable, str(mft_source / "run_mft_gpu.py"),
        "--gpu_idx", str(GPU_INDEX),
        "--data_dir", str(resolve_path(DATA_DIR)),
        "--time_range", label,
        "--sta_file", str(resolve_path(STATION_FILE)),
        "--temp_root", str(resolve_path(TEMPLATE_ROOT)),
        "--temp_pha", str(resolve_path(TEMPLATE_PHASE_FILE)),
        "--out_ctlg", str(output_root / "catalog_{}.dat".format(label)),
        "--out_pha", str(output_root / "phase_{}.dat".format(label)),
    ]
    subprocess.check_call(command, cwd=str(RUN_DIR), env=env)


if __name__ == "__main__":
  main()
