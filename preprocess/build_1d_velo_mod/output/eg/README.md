# Generated Example Outputs

These files demonstrate the products of the packaged example configuration.
They are real numerical outputs, not renamed historical figures or synthetic
velocity values, but they are not a recommended model for another study area.

## Provenance

The input ucvm_3d_eg.txt contains 909 unmodified rows selected from a previously
queried CVM-S4 UCVM grid: 3 longitudes (-119.50 to -119.40), 3 latitudes
(35.00 to 35.10), and 101 local-surface depths (0 to 50 km at 0.5 km spacing).
No new UCVM query was run on this workstation. The extraction sidecar records
the original grid checksum, original executable/config checksums, subset method,
and absence of interpolation. Local-surface elevation is represented by the
nine grid points' median, 0.545487 km above sea level.

The packaged velocity_model.py step-2 functions generated the dense profile,
layered model, diagnostics and lateral maps on 2026-10-26. Step 3 generated the
datum report using config_eg.json. Both packages contain identical products.

## What to Expect

- ucvm_3d_eg.txt and .meta.json: saved extraction input and provenance.
- mean1d_eg.csv, mean1d_eg.csv.meta.json, mean1d_eg_full.txt: regional profile.
- layered_eg.csv and .meta.json: simplified layers and construction metadata.
- velocity_model_1d.json: input for 0_build_travel_time_table_eg.py.
- velocity_model_eg.png: dense versus layered P/S profiles.
- velocity_gradient_eg.png: gradient/interface diagnostic.
- major_layer_velocity_maps.png and .meta.json: lateral midpoint maps.
- hypoinverse_datum.json: optional datum report.

CRE files are intentionally absent: hypoinverse.export_cre defaults to false.
To generate them, review the datum, set a suitable reference elevation and
explicit acknowledgements, enable export_cre, and rerun step 3.

## Reproduce or Replace

From the build_1d_velo_mod folder, step 2 can reuse the supplied extraction
without UCVM. Since the example outputs already exist, set overwrite=true in
config_eg.json before running 2_build_1d.py and 3_build_cre_model.py. This replaces
the existing demonstration products; overwrite=false intentionally protects them.
The extraction and its checksum sidecar must remain together.

For your own case, choose a new output_dir, configure installed UCVM paths and
regional settings, and run steps 1 and 2. Review the surface elevation and depth
coverage before using the JSON to build a PAL table. A fresh query may differ
with UCVM/model versions; do not expect byte-for-byte agreement across versions.
