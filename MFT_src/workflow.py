"""Path and process-environment helpers for MFT case launchers."""

import os
import subprocess
import sys
from pathlib import Path


def resolve_run_path(run_dir, path):
  path = Path(path).expanduser()
  return path if path.is_absolute() else Path(run_dir) / path


def source_environment(run_dir, palm_root, case_code):
  run_dir = Path(run_dir).expanduser().resolve()
  palm_root = Path(palm_root).expanduser().resolve()
  source_paths = [run_dir, palm_root / "MFT_src", palm_root / "PAL_src"]
  missing = [path for path in source_paths if not path.exists()]
  if missing:
    raise FileNotFoundError(
        "missing PALM runtime path(s): {}".format(
            ", ".join(str(path) for path in missing)
        )
    )

  env = os.environ.copy()
  existing = env.get("PYTHONPATH")
  values = [str(path) for path in source_paths]
  if existing:
    values.append(existing)
  env["PYTHONPATH"] = os.pathsep.join(values)
  env["PALM_MFT_CONFIG"] = "config_{}".format(case_code)
  return env


def run_association(
    mft_source, run_dir, env, phase_files, template_phase_file,
    station_file, time_range, output_root,
):
  """Associate all segment detections and publish final MFT products."""
  phase_files = [Path(path) for path in phase_files]
  if not phase_files:
    raise ValueError("no MFT phase segments were produced")
  output_root = Path(output_root)
  command = [
      sys.executable, str(Path(mft_source) / "associate_mft.py"),
      "--det_pha", *[str(path) for path in phase_files],
      "--temp_pha", str(template_phase_file),
      "--sta_file", str(station_file),
      "--time_range", str(time_range),
      "--out_catalog", str(output_root / "catalog.csv"),
      "--out_phase", str(output_root / "phase.csv"),
      "--out_event", str(output_root / "event.dat"),
      "--out_dt", str(output_root / "dt.cc"),
  ]
  subprocess.check_call(command, cwd=str(run_dir), env=env)
