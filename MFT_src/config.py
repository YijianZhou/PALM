"""Resolve the MFT configuration selected by an executable workflow."""

import importlib
import os


module_name = os.environ.get("PALM_MFT_CONFIG")
if not module_name:
  raise RuntimeError(
      "PALM_MFT_CONFIG is not set. Run MFT through an indexed launcher in "
      "2_run_mft, or set it to an importable configuration module."
  )

Config = importlib.import_module(module_name).Config
