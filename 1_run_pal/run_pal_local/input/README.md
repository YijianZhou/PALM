# Example Travel-Time Model

`velocity_model_1d.json` is bundled at the default MODEL_FILE path used by
`../0_build_travel_time_table_eg.py`. It is an identical copy of the generated
model in `PALM/preprocess/build_1d_velo_mod/output/eg/`.

This small CVM-S4 example demonstrates the input format, not a validated model
for the supplied station network or an arbitrary study area. It uses depths
0-50 km below a representative surface at 0.545487 km above sea level.
See the package's preprocessing output README for extraction provenance.

For real processing, replace this JSON with your own step-2 model or change
MODEL_FILE. Review the model datum, station inventory and association grid;
use DRY_RUN=True to validate before building the .paltt file.

Alternatively use velocity_model_example.csv or the paired velocity_p_example.cre
and velocity_s_example.cre files. These are illustrative two-layer models, not
conversions of the bundled regional JSON. Set MODEL_FORMAT and an explicit
MODEL_REFERENCE_ELEVATION_KM; finite CSV/CRE also needs MODEL_BOTTOM_DEPTH_KM.
See [format settings](../../../PAL_src/VELOCITY_MODEL_FORMATS.md).
Hypoinverse command files and datum reports are not velocity-model inputs.
