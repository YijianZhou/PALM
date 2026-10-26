"""Build the layered JSON model required by PAL travel-time preparation."""
from pathlib import Path

# USER SETTINGS: must match the extraction config; outputs are config-relative.
CONFIG_FILE = Path(__file__).with_name('config_eg.json')

from velocity_model import run_stage

if __name__ == "__main__":
    run_stage("build_1d", config_path=CONFIG_FILE)
