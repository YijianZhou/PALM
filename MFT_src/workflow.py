"""Path and process-environment helpers for MFT case launchers."""

import os
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
