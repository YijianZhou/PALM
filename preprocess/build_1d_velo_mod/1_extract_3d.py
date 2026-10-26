"""Extract a regional UCVM grid using the example case settings."""
from pathlib import Path

# USER SETTINGS: all grid, model, path and execution parameters are in this JSON.
CONFIG_FILE = Path(__file__).with_name('config_eg.json')

from velocity_model import run_stage

if __name__ == "__main__":
    run_stage("extract", config_path=CONFIG_FILE)
