"""Prepare a saved layered-model P/S table before association (optional)."""
from pathlib import Path
import sys

# USER SETTINGS: paths relative to this script, except the installed package root.
PAL_ROOT = Path(__file__).resolve().parents[2]
# Bundled step-2 example JSON; replace with your regional model for real work.
# CSV or paired CRE are also supported with the model-input settings below.
MODEL_FILE = 'input/velocity_model_1d.json'  # JSON/CSV or P CRE, km and km/s.
STATION_FILE = 'input/example_pal_format4.sta'
ASSOCIATION_CONFIG = 'config_eg.py'
SUBNET = 'full'  # Association grid to inherit when an override below is None.
OUTPUT_DIR = 'output/travel_time_table_eg'  # Must be new for every rebuild.
TABLE_DESTINATION = 'input/travel_time_table.paltt'

# USER SETTINGS: model input (preparation only, not runtime association config).
MODEL_FORMAT = 'auto'  # auto by .json/.csv/.cre, or explicit json/csv/cre.
MODEL_S_FILE = None  # CRE only: S model path; MODEL_FILE is the P CRE model.
MODEL_REFERENCE_ELEVATION_KM = None  # Required for CSV/CRE: depth-zero elevation above MSL, km.
MODEL_BOTTOM_DEPTH_KM = None  # CSV/CRE finite policy: bottom > last top, km below model zero.
# JSON: leave the three settings above None; embedded datum/layers are used.
# Halfspace CSV/CRE: bottom may be None. See PAL_src/VELOCITY_MODEL_FORMATS.md.

# USER SETTINGS: grid and datum. None inherits the association config.
LAT_RANGE_DEG = None  # [min, max], degrees within [-90, 90].
LON_RANGE_DEG = None  # [min, max], degrees within [-180, 180].
XY_MARGIN = None  # Nonnegative fraction of station extent.
XY_GRID_DEG = None  # Positive latitude/longitude grid spacing, degrees.
DEPTHS_KM = None  # Finite, strictly increasing source depths below mean sea level.
TOP_EXTENSION = 'constant_first_layer'  # Or 'error' above the model surface.
BOTTOM_POLICY = 'finite'  # Or 'halfspace' to extend the deepest layer.

# USER SETTINGS: ray solver and execution.
MAX_DISTANCE_KM = 200.0  # Positive horizontal source-station cutoff.
KM_PER_DEGREE = 111.0  # Positive PAL local-distance conversion, km/degree.
DISTANCE_TOLERANCE_KM = 1e-6  # Positive horizontal-distance convergence tolerance.
MAX_ITERATIONS = 80  # Positive integer direct-ray solver limit.
BATCH_SIZE = 32768  # Positive integer source-cell batch size (CPU, not picker batch).
DRY_RUN = False  # Boolean: validate/report without calculating or writing.
OVERWRITE_TABLE = False  # Boolean: allow replacing TABLE_DESTINATION, not OUTPUT_DIR.


# CONNECTION CODE: no separate builder config file is needed.
def build_settings():
    return {
        'model_file': MODEL_FILE, 'station_file': STATION_FILE, 'output_dir': OUTPUT_DIR,
        'model_format': MODEL_FORMAT, 'model_s_file': MODEL_S_FILE,
        'model_reference_elevation_km': MODEL_REFERENCE_ELEVATION_KM,
        'model_bottom_depth_km': MODEL_BOTTOM_DEPTH_KM,
        'table_file': Path(TABLE_DESTINATION).name,
        'grid': {'workflow_config': ASSOCIATION_CONFIG, 'subnet': SUBNET,
                 'lat_range_deg': LAT_RANGE_DEG, 'lon_range_deg': LON_RANGE_DEG,
                 'xy_margin': XY_MARGIN, 'xy_grid_deg': XY_GRID_DEG, 'depths_km': DEPTHS_KM},
        'datum': {'source_depth_reference': 'mean_sea_level',
                  'top_extension': TOP_EXTENSION, 'bottom_policy': BOTTOM_POLICY},
        'solver': {'max_distance_km': MAX_DISTANCE_KM, 'distance_metric': 'pal_local',
                   'km_per_degree': KM_PER_DEGREE, 'distance_tolerance_km': DISTANCE_TOLERANCE_KM,
                   'max_iterations': MAX_ITERATIONS, 'batch_size': BATCH_SIZE},
    }


def main():
    sys.path.insert(0, str(PAL_ROOT / 'PAL_src'))
    from travel_time_table import build_workflow
    return build_workflow(build_settings(), __file__, TABLE_DESTINATION,
                          dry_run=DRY_RUN, overwrite_table=OVERWRITE_TABLE)


if __name__ == '__main__':
    main()
