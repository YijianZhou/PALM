# AWS Workflow

## Optional Step 0: Local Travel-Time Preparation

Run `python 0_build_travel_time_table_eg.py` locally, not as an AWS job.
This uses the same CPU/NumPy builder as the local workflow and needs no AWS
credentials or GPU. Edit paths and parameters at the top; use `DRY_RUN = True`
first. Runtime depends on network size and grid resolution.

The bundled `input/velocity_model_1d.json` is an example, not a calibrated
SoCal model. Replace it before operational use. Combined Vp/Vs CSV and paired
P/S CRE files are also supported; examples are in `input`. Consult the installed
package's `PAL_src/VELOCITY_MODEL_FORMATS.md` for units, columns, depth-zero
elevation and bottom-depth settings. All preparation settings remain in step 0.

The default station inventory covers the full network; the grid inherits
`subnet_assoc_params['full']`. Output is `output/travel_time_table_eg` and
`input/travel_time_table.paltt`. Use a new output directory for each rebuild.
Ensure coverage of all required stations, grid cells and depths across subnets
and full-network reassociation; build separate tables if their grids differ.

Set `travel_time_table` to `'input/travel_time_table.paltt'` in the relevant
association config default/full/subnet entries to enable it. Existing `None`
defaults are unchanged. Submission uploads selected tables and rewrites paths
only in the staged config, including AI-PAL reassociation. Missing tables fail
before submission. Use literal path strings (relative to the workflow folder
or absolute), or `None`; no computed expressions. Local configs are untouched.
