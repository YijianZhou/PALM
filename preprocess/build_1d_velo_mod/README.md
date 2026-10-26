# Regional 1D Velocity-Model Preparation

Build a case-specific layered velocity model before generating PAL travel-time
tables. This optional UCVM workflow is shared preparation for rule-based PAL
and AI-PAL, including PALM's PAL stage; it does not run association.

## Example Configuration

Edit `config_eg.json` before running. It is an illustrative Southern California
box compatible with the example choice `extract.model = "cvms"`, not a calibrated
velocity model for a particular study. Choose an installed UCVM model with
coverage of your region; this extractor is not a generic reader for arbitrary
3D model files. See [installation](installation/README.md) for UCVM setup.

All scientific parameters and I/O names live in that JSON. Each numbered script
has an editable `CONFIG_FILE` at the top; all three must use the same config.
Paths under `output_dir` are relative to the config file, independent of the
working directory. New outputs go to `output/eg/`, with `overwrite = false`.
Generated example products are provided in [output/eg](output/eg/README.md),
using a small saved UCVM grid and the packaged processing code. Read that
provenance before reusing them; they are not a model for an arbitrary region.
With overwrite disabled, existing example products are protected. Choose a
new output_dir for your case, or explicitly enable overwrite to regenerate.

Before extraction, review the UCVM executable/config paths, model, query bounds,
sampling and depth reference. Before step 2, review the averaging/layer settings
and `layers.model_surface_elevation_km`: **0.545487 km** is the median surface
elevation of the supplied nine example grid points, not a universal datum.
Supply a finite representative elevation in km above mean sea level for your
own local-surface profile.

## Running Order

```bash
conda activate ucvm
python 1_extract_3d.py
python 2_build_1d.py
# Optional: Hypoinverse datum report, and CRE export only when configured
python 3_build_cre_model.py
```

1. Extract the configured 3D grid into `output/eg/ucvm_3d_eg.txt` plus its
   checksum/provenance `.meta.json`. Keep both.
2. Average and layer the profiles; write `output/eg/velocity_model_1d.json`,
   diagnostic CSVs, profile/gradient figures and optional lateral maps.
3. Optionally prepare Hypoinverse inputs. Defaults write only a datum report:
   `hypoinverse.export_cre = false`. To export CRE, set a suitable
   `reference_elevation_km`, review the surface elevation, and explicitly
   acknowledge the representative-surface and final-halfspace approximations.

No CLI parameters are required by the numbered scripts. The optional installed
`ucvm-model extract|build|cre` wrapper also supports `--config PATH`; regenerate
it with `python installation/create_launchers.py` after updating this folder.
Step 2 reuses extraction files and does not call UCVM. Preserve matching sidecars;
set `overwrite = true` deliberately when rebuilding existing model products.

## Input to the PAL Travel-Time Builder

**Provide the step-2 `output/eg/velocity_model_1d.json` as `MODEL_FILE`.**
JSON is the simplest handoff because it retains layer bottoms and datum.
The builder also accepts the layered CSV or paired P/S CRE files with explicit
model-format, datum and bottom settings; see [supported input formats](../../PAL_src/VELOCITY_MODEL_FORMATS.md).
Do not supply extracted 3D text or hypoinverse_datum.json. Step 3 is not needed
when using JSON/CSV. Dense CSV samples would be treated as layer tops, not a
continuous velocity gradient.

For `1_run_pal/run_pal_local/0_build_travel_time_table_eg.py` in either package,
or AI-PAL's `3_run_ai_pal/run_ai_pal_local/0_build_travel_time_table_eg.py`, set:

```python
MODEL_FILE = '../../preprocess/build_1d_velo_mod/output/eg/velocity_model_1d.json'
```

This path is relative to the **builder script**, not the terminal. Alternatively,
copy the JSON into that workflow's `input/velocity_model_1d.json` and retain its
default `MODEL_FILE`. Both packages contain the same preparation scripts and
example config. Each builder can use the JSON produced within its own package,
or an absolute path to a shared model file.

For JSON input, the current builder requires `depth_reference = "local_surface"`,
`depth_positive = "down"`, `interpolation = "piecewise_constant"`, units of km
and km/s, a finite `model_surface_elevation_km`, and contiguous layers starting
at zero with `Vp > Vs > 0`. The extractor can also produce sea-level profiles,
but those JSONs are **not accepted by the current PAL builder**; do not relabel
them as local-surface profiles to bypass validation.

Set the builder's station file, association config/subnet, grid/depth coverage,
and finite-bottom/halfspace policy. Run with `DRY_RUN = True` to validate, then
`False` to build into a new `OUTPUT_DIR`. The published artifact is
`TABLE_DESTINATION` (default `input/travel_time_table.paltt`).
Set `travel_time_table` in each applicable association parameter set to that
file, including `full` when used for reassociation. Check explicit subnet
overrides. Model JSON is a preparation input; runtime association reads the
saved `.paltt`, not JSON or CRE. Rebuild the table after changing the model.

See [PAL preparation](../../1_run_pal/README.md#optional-layered-model-travel-time-preparation)
and [AI-PAL preparation](../../../AI-PAL/3_run_ai_pal/README.md#optional-travel-time-preparation)
for builder settings and forwarding.

## Lateral velocity maps

The major intervals are bounded by profile endpoints and detected interface
centers (not individual transition/sub-layer boundaries). Each map uses the
interval midpoint. P and S are shown separately with a common color range per
phase across all depths. Saved samples are interpolated only vertically at each
lat/lon; this is not a new UCVM query. In local-surface mode these slices follow
topography rather than a common sea-level plane.

| Map setting | Default / validation |
| --- | --- |
| `maps.enabled` | true; boolean |
| `maps.output` | `major_layer_velocity_maps.png`; distinct output path |
| `maps.interpolation` | `linear`; `nearest` alternatively uses the nearest saved depth and labels the actual depth |
| `maps.cmap` | `viridis`; Matplotlib colormap name |
| `maps.dpi` | 180; positive integer |

## Step 3: depth datum and Hypoinverse

The example selects **CVM-S4**, not CVM-H. UCVM `gd` queries depths below
local ground; `ge` queries elevations relative to mean sea level. CVM-H has its
own DEM and was designed for elevation queries; its native surface can differ
from the UCVM DEM. Do not assume the saved UCVM surface-elevation column proves
CVM-H's native datum. These distinctions are documented in
[SCEC model notes](https://github.com/SCECcode/ucvm/wiki/Model-Specific-Notes) and
[UCVM query documentation](https://github.com/SCECcode/ucvm/wiki/ucvm_query).
The report uses each distinct grid location once when computing surface stats.

Klein's 2014 Hypoinverse manual, pp. 21–25 and 75–76, distinguishes:
- CRE: file depths below a selected reference elevation E; event depths relative
  to sea level, with station elevations optionally used.
- CRH/CRT: model-surface depths; `GEO T` reports geoid depths using the
  event-dependent nearby-station datum. A regional fixed shift is not equivalent.

For a local-surface profile assigned representative elevation H, CRE interior
layer tops become `z_CRE = z_original + E - H`. The first top remains zero,
extending its material to E. For a sea-level profile, H=0. This flattening is an
approximation: averaging at varying local surfaces does not define one exact H.
The report therefore does not silently export an unverified corrected model.
Set E to a reference covering the intended stations/topography, not simply a
universal 1.5 km. CRE earthquake depths must not be corrected again by E-H.

| Hypoinverse setting | Default / meaning |
| --- | --- |
| `model_type` | `CRE`; also `CRH` or `CRT` for reports only |
| `output` | `hypoinverse_datum.json` |
| `surface_elevation_method` | `explicit`; alternatively `grid_median` |
| `surface_elevation_km` | 0.545487 km for this example; finite MSL elevation required for explicit method |
| `reference_elevation_km` | null; set a finite MSL elevation at or above the original model top before export |
| `export_cre` | false; true enables CRE export after datum review |
| `acknowledge_approximate_surface` | false; explicitly acknowledge the local-surface approximation before export |
| `acknowledge_halfspace_extension` | false; explicitly acknowledge CRE final-halfspace semantics before export |
| `use_station_elevations` | true; emitted CRE flag |
| `p_model_number`, `s_model_number` | 1, 2; distinct integers 1–36 |
| `output_p`, `output_s` | `hypoinverse_p.cre`, `hypoinverse_s.cre` |
| `commands_output` | `hypoinverse_models.hyp`; CRE and SAL commands, not a full locator run |

CRE export checks the documented 20-layer limit and adjacent velocity differences
of at least 0.01 km/s separately for P and S. No station file or located-event
catalog is modified. Use step 3 for datum-aware exports. The old CRE helper
function remains only for compatibility with the external parent-folder launcher;
its fixed shift setting is not used by step 3.

## Interface-based layering (default)

Steps 01 and 02 are unchanged; saved 3D samples and their metadata can be reused.
For layering experiments rerun step 2, enabling overwrite intentionally.

`layers.method=interfaces` uses adjacent-depth differences of the dense profile.
For each contiguous excursion where P OR S gradient exceeds the threshold,
choose the largest P/S gradient (shallowest on ties). Its depth is the sample
interval midpoint, rounded half-up to the configured decimal precision.
These are numerical interfaces in the regional profile, not inferred geology.

Transition velocities default to `transition_velocity_method=equal_steps`.
For N transition layers between adjacent ordinary-layer speeds A and B,
use A + j(B-A)/(N+1), j=1..N, separately for P and S. Ordinary layers retain
native-sample medians. Transition samples do not determine these speeds and
are excluded from the residual tie-break. All transition layers have equal
minimum thickness. Speeds are rounded only after interpolation; rounding to
0.01 km/s can make successive displayed increments differ by 0.01 km/s.
The JSON records anchors and unrounded increments. `median` restores the former
transition behavior. This choice does not change extraction or the dense profile.

Around each interface, center a group of fixed transition layers, each exactly
`min_thickness_km` thick: normally 2, or 3 when the peak exceeds 1 s^-1.
If any internal rounded P/S velocity contrast fails the minimum contrast rule,
replace that group with one centered layer of minimum thickness. Record this
mismatch in the model metadata. Overlapping zones, too-small gaps, or edges
outside the sampled range produce an explicit error.

Between transition zones, dynamic programming searches the depth lattice to
maximize layer count subject to minimum thickness and strict adjacent contrast
in P OR S. Fixed zone boundaries are exempt from contrast checks. With
`allow_low_velocity_zone=false`, rounded P AND S must be nondecreasing across
EVERY boundary, including transition zones. Ordinary-layer sample medians are never
adjusted. If necessary, `monotonic_transition_fallback=true` collapses the fewest
transition groups to one centered minimum-thickness layer to achieve feasibility.
Then maximize total layer count and minimize residuals. Remaining infeasibility
produces an explicit error. Fallback enumeration may be costly for many interfaces.
When true, either speed may decrease, but the contrast criterion still controls
subdivision. Small reversals need not produce their own layer.

Ordinary-layer medians use native detailed samples in each closed interval, rounded half-up.
The search retains previous-layer velocity states across regions so monotonicity
is global. Ties in layer count minimize summed squared P/S sample residuals,
then follow ascending boundary order. No smoothing of the detailed input occurs.

| Setting | Default / units / validation |
| --- | --- |
| `layers.method` | `interfaces`; `legacy` retains the previous schedule/splitting method |
| `interface_gradient_per_sec` | 0.2 s^-1; finite positive, strict positive-gradient threshold |
| `min_thickness_km` | 1.0 km; finite positive, applies to every layer |
| `min_velocity_change_kmps` | 0.1 km/s; finite positive, strict P OR S contrast within major intervals |
| `depth_decimals` | 1; integer 0–3, sets boundary lattice and depth rounding |
| `velocity_decimals` | 2; integer 0–3, rounded medians must retain Vp > Vs > 0 |
| `transition_velocity_method` | `equal_steps`; alternatively `median` for historical behavior |
| `allow_low_velocity_zone` | `false`; boolean, prohibits decreases in both rounded P and S |
| `monotonic_transition_fallback` | `true`; boolean, allow one-layer fallback to resolve reversal conflicts |
| `smooth_interfaces` | `true`; boolean, use centered transition zones; false retains sharp interfaces |
| `transition_layers` | 2; positive integer, normal transition layer count |
| `strong_transition_layers` | 3; positive integer, strong transition layer count |
| `strong_interface_gradient_per_sec` | 1.0 s^-1; finite positive, strict threshold |
| `plot.gradient_output` | `cvms_mean1d_gradient.png`; additional plot with interface depths |

The JSON records peak intervals, original midpoints, rounded interface depths,
transition bounds/counts, contrast-mismatch flags, and each sublayer's region index.
`transition_layer` identifies fixed layers; `major_layer_index` now indexes the
regular regions and individual fixed transition layers. Step 04 marks interfaces on both figures.
Profile endpoints must lie on the chosen lattice. A finer lattice increases
search cost and does not add information to the original sampled model.

Legacy-only controls below (`schedule_km`, `adaptive`, variation/gradient limits,
`average_method`) are retained for reproducibility and ignored by the default
interface method. The halfspace setting applies to both methods.

## Case settings and their meaning

| Section / setting | Default and meaning |
| --- | --- |
| `case_name` | eg - regional 1D velocity model; descriptive label for plots/provenance |
| `overwrite` | `false`; replacing existing outputs must be enabled explicitly |
| `output_dir` | `output/eg`, relative to this config |
| `extract.ucvm_query`, `ucvm_conf` | Paths to installed UCVM executable and configuration |
| `extract.model` | `cvms`; an installed example model, not a claim that it is the best regional model |
| `extract.value_source` | `crustal`; alternatively `combined` to use UCVM's final combined columns |
| `extract.depth_reference` | `local_surface`; alternatively `sea_level` |
| `longitude_deg` | `[-119.5, -119.4, 0.05]`, minimum/maximum/step in degrees |
| `latitude_deg` | `[35.0, 35.1, 0.05]` |
| `depth_m` | `[0, 50000, 500]`, positive-down depths in meters |
| `batch_size`, `timeout_sec` | `50000` points per query; `600` seconds per query |
| `average.statistic` | `median`, preserving the original actual behavior; `mean` and harmonic velocity averaging are available |
| `require_full_depth_coverage` | `true`; require every configured horizontal point at every configured depth |
| `allow_legacy_input_without_metadata` | `false`; explicit opt-in for old seven-column extraction files after independently checking their datum, model, and column choice |
| `smoothing_window_points` | `1` disables smoothing; positive odd values use a centered, edge-padded moving average over the uniformly sampled depth axis |
| `layers.schedule_km` | Contiguous depth intervals and base thicknesses; last interval now ends at 50 km, matching extraction |
| `layers.adaptive` | `true`; split layers if variation/optional gradient thresholds are exceeded |
| `min_thickness_km` | `1.0`; subdivision cannot make thinner children; invalid base layers are rejected |
| `vp_variation_kmps`, `vs_variation_kmps` | `0.35`, `0.20` km/s, maximum-minus-minimum variation within a candidate layer |
| `use_gradient_criterion` | `false` |
| `vp_gradient_per_sec`, `vs_gradient_per_sec` | `0.60`, `0.35` (km/s)/km; variation divided by layer thickness |
| `layers.average_method` | `mean`: depth-integrated velocity divided by thickness; `harmonic`: thickness divided by integrated slowness; `sample_median`: median of included sample values, sensitive to their spacing |
| `extend_last_layer_as_halfspace` | `false`; canonical model ends at its stated bottom unless explicitly enabled |
| `plot.output`, `dpi` | `velocity_model_eg.png`, `180`; another supported image/PDF suffix can be used |
| `plot.reference_file` | `null`; no external reference needed to plot the prepared model |
| `plot.reference_format` | `top_vp_vpvs` for whitespace-separated depth/Vp/ratio, or `depth_vp_vs` for comma-separated depth/Vp/Vs |
| `cre_export.shift_nonfirst_layer_tops_km` | `0.0`, retained as an **optional export-only** legacy transform |
| `cre_export.acknowledge_halfspace_extension` | `false`; must be true to export a finite model as CRE, which treats the final layer as a halfspace |
| Stage `input`/`output`, `output_full`, `model_output`, `output_p`, `output_s` | Configurable filenames; outputs must be distinct |
| `cre_export.model_name` | `EG_MEAN_1D`; CRE header truncated to 30 characters |

Axes use fixed increments and stop at the last grid point within the configured
maximum; they do not append a shorter final interval. Bounds/steps must be finite,
steps positive, and longitude/latitude within their valid ranges. Sampling finer
than UCVM's displayed coordinate precision is rejected. Batch size, timeout,
smoothing window, layer thresholds, and plot DPI are validated. Model velocities
must be finite with `Vp > Vs > 0`; extracted density must be positive and finite.

Lateral statistics currently give each horizontal sample equal weight. They are
not area-weighted estimates or calibrated effective travel-time velocities.
Smoothing can blur interfaces: compare with `smoothing_window_points=1` before
adopting a larger window. The full dense table retains unsmoothed density and
per-depth sample counts. Velocity reversals are preserved, not forced monotonic;
the eventual travel-time solver must handle the resulting model appropriately.
Unresolved variation at the minimum layer thickness is flagged in each layer's
`variation_limit_exceeded`, rather than subdividing below the specified minimum.

## Depth reference is part of the model

UCVM distinguishes geo-depth (`-c gd`) from geo-elevation (`-c ge`). Crustal
Vp/Vs/density are columns 6/7/8 (zero based); combined values are 14/15/16.
The parser verifies row count, order, coordinates, and material values and fails
on malformed/missing responses instead of silently skipping them. See the
[official UCVM query documentation](https://github.com/SCECcode/ucvm/wiki/ucvm_query).

- `local_surface`: queries use positive depth below each local surface. Averaging
  these profiles does **not** make them sea-level-referenced. Surface elevations
  are retained in the extracted file, and the canonical model carries this label.
- `sea_level`: configured positive-down depth is sent to UCVM as its negative
  elevation; the returned profile remains positive-down relative to sea level.
  Invalid/unsupported points fail explicitly.

The later travel-time module must reconcile model datum, event depths, and station
elevations. Do not silently add an elevation shift or equate these two references.
Re-query with the desired reference where appropriate. The old +1.5 km operation
is preserved only for optional CRE export; it is not a general datum conversion.

## Output Files

All example products are under `output/eg/`:

| File | Purpose |
| --- | --- |
| `ucvm_3d_eg.txt` and `.meta.json` | UCVM grid, elevation and provenance |
| `mean1d_eg.csv` | Headerless depth_km, Vp_km/s, Vs_km/s |
| `mean1d_eg_full.txt` | Dense profile with density and sample counts |
| `layered_eg.csv` and `.meta.json` | Headerless top_depth_km, Vp, Vs, Vp/Vs and layer metadata |
| `velocity_model_1d.json` | **Canonical PAL travel-time-builder input**, with explicit layer bottoms and datum |
| `velocity_model_eg.png`, `velocity_gradient_eg.png` | Model diagnostics |
| `major_layer_velocity_maps.png` | Optional lateral velocity maps |
| `hypoinverse_datum.json` | Optional step-3 datum report; not a velocity-model input |
| `hypoinverse_p.cre`, `hypoinverse_s.cre`, `hypoinverse_models.hyp` | Optional acknowledged CRE export only |

Tests in `tests/` use synthetic profiles
and verify the exported JSON with both packages' PAL table readers; they do not
validate regional scientific suitability or perform a live UCVM extraction.
