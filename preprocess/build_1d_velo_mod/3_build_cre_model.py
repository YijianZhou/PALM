"""Optional Hypoinverse datum report/CRE export; not needed by PAL tables."""
from pathlib import Path

# USER SETTINGS: review hypoinverse datum/acknowledgements before enabling export.
CONFIG_FILE = Path(__file__).with_name('config_eg.json')

from velocity_model import run_stage

if __name__ == "__main__":
    run_stage("build_cre_model", config_path=CONFIG_FILE)
