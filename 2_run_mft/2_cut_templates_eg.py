#!/usr/bin/env python3
"""Cut waveform templates for selected PAL or AI-PAL events."""

import subprocess
import sys
from pathlib import Path

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
DATA_DIR = Path("/data/Example_data")
TEMPLATE_PHASE_FILE = Path("input/eg_mft.temp")
OUTPUT_ROOT = Path("output/Example_templates")
CUT_METHOD = "intense"  # "intense" or "long"


def main():
  if CUT_METHOD not in {"intense", "long"}:
    raise ValueError("CUT_METHOD must be 'intense' or 'long'")
  mft_source = Path(PALM_ROOT).expanduser() / "MFT_src"
  command = [
      sys.executable,
      str(mft_source / "cut_template_{}.py".format(CUT_METHOD)),
      "--data_dir", str(resolve_path(DATA_DIR)),
      "--temp_pha", str(resolve_path(TEMPLATE_PHASE_FILE)),
      "--out_root", str(resolve_path(OUTPUT_ROOT)),
  ]
  subprocess.check_call(
      command,
      cwd=str(RUN_DIR),
      env=source_environment(RUN_DIR, PALM_ROOT, CASE_CODE),
  )


if __name__ == "__main__":
  main()
