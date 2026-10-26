# PAL Source

Shared current rule-based PAL implementation used by the local and AWS
executables in `../1_run_pal/`. It contains waveform readers, STA/LTA and
kurtosis picking, association, phase merging, trigger inventories, and reusable
daily orchestration.

The source is synchronized from AI-PAL's rule-based PAL modules. PALM does not
duplicate AI model packages, training code, or real-time AI inference here.
`pick_ensemble.py` is retained because current phase merging understands the
extended pick schema even when a PAL-only workflow produces legacy PAL picks.

## Console Reporting

Console detail is controlled by the existing `console_verbosity` setting:
`default` retains workflow progress and warnings but hides detailed `[assoc]`
attempt/cache/per-call completion diagnostics; `debug` displays them; `quiet`
retains warnings and final output/completion messages. Association results are
unchanged. No additional logging parameter is required.

Missing event waveform files are expected for inactive stations and are shown
only with the existing `console_verbosity = "debug"` setting. Default and quiet
console modes suppress these messages; gain fallback, malformed-channel and
other warnings remain visible.

## Origin-time and spatial association

For each origin-time cluster, `PS_Pair_Assoc` searches up to ten consecutive
origin times at 0.1-second spacing, on a lattice anchored at the actual median
of all clustered `sta_ot` values. Candidates remain within the inclusive 5th
and 95th percentiles. The ten-point window is centered as closely as possible
to the median (earlier window on a tie), shifted inward at asymmetric bounds;
narrow intervals use fewer points, and a collapsed interval uses the median.

Each candidate searches the full configured 3D spatial grid. A P/S pair qualifies
only when **both** absolute arrival residuals are strictly below their respective
`max_res=[res_p, res_s]` limits in seconds. Phases from different candidate pairs
cannot be combined to qualify a station. A station's score is the mean of its
accepted pair's absolute P and S residuals, in seconds. Selection across time
and space maximizes the associated count, then minimizes the median of these
scores. Scores are compared before rounding; returned `res` retains its existing
one-decimal format. Exact ties favor the earlier origin time, then the first
spatial cell in [z, x, y] order.

### P/S spatial configuration

Set `"velo_mod": [5.9, 3.5]` and `"max_res": [1.2, 1.2]` in
`subnet_assoc_params["default"]`, with subnet overrides as needed. `velo_mod`
accepts only homogeneous [Vp, Vs] in km/s with finite Vp > Vs > 0. `max_res`
accepts two finite positive numbers [P, S] in seconds, or one positive scalar
that applies to both phases (for example `max_res=1.2` means `[1.2, 1.2]`). These are the associator
constructor defaults; shipped regional configs retain their previous P tolerance
and initially use that same tolerance for S (for example [0.8, 0.8]).

Migration: replace the association dictionary's scalar `vp` with `velo_mod` and
optionally write scalar `max_res=r` as `[r, r]`. Scalar `max_res` remains supported;
the old association `vp` keyword must be replaced.
Separate top-level `vp`/`vs` and rule-picker `picker_vp`/`picker_vs` still control
pick-pair origin-time estimates and AI repick predictions. They are distinct
from the spatial model and have not been renamed. Local, AWS, realtime, training,
CEED preprocessing/benchmark, and post-repick PAL association forward the new
parameters. Explicit S gating can change associations even with a homogeneous
model, especially when the origin-time trial or pairing velocity ratio differs.
Recompute outputs to apply it. Set `travel_time_table` to a self-contained `.paltt` file to load precomputed
P/S times; `None` or numeric NaN builds times from `velo_mod`. The saved grid
overrides spatial bounds, spacing and depth settings, and `velo_mod` is ignored
in table mode. Pair-origin-time vp/vs and temporal controls remain active.
Paths expand `~` and otherwise resolve from the process working directory;
absolute paths are recommended. Copy the same file onto each worker host.
Missing station geometries or cells are skipped for both phases; no homogeneous
fallback is used. Geometry matching uses the current station latitude, longitude
and elevation exactly. Corrupt tables fail explicitly. The reader is mirrored
in both packages and loads station arrays lazily, with no preprocessing-module
dependency. Tables from the workspace `build_travel_time_table` workflow include
all model metadata and station epochs in the one file.

`unique_stations=True` is the default and counts each station identifier once.
All candidate pairs remain available until spatial search selects the
lowest-residual pair per station. Set it to `False` for legacy pair counting. Temporal trials
do not consume picks or increment drop counters; only the winning solution is
passed to the existing association/drop loop. Median scoring uses bounded cell
batches to avoid storing a complete residual grid for every pick and time.

### Temporal-grid configuration

Set these keys in `subnet_assoc_params["default"]` in the source config or
workflow example config; individual subnet entries can override them:

```python
"unique_stations": True,
"ot_grid_step_sec": 0.1,
"ot_grid_max_width_sec": 1.0,
"ot_grid_max_points": 10,
```

The width caps the first-to-last candidate span, not the distance from the
median. The effective count is limited by both `ot_grid_max_points` and
`floor(ot_grid_max_width_sec / ot_grid_step_sec) + 1`, then by P5-P95.
Defaults preserve the previous ten-point, 0.9-second span. Step and width must
be finite and positive; count must be a positive integer. A count of one
uses only the median. Older configs omitting the keys use these same defaults.
Settings apply to local and AWS association, plus AI-PAL realtime and
post-repick association. Recompute existing outputs to apply changed settings.

Temporal settings are also exposed in active training/preprocessing, AI detection,
realtime, AWS, and benchmark configs throughout AI-PAL, PALM, and SoCal_workdir.
The workspace AGENTS.md requires this config coverage for future tunable features;
regional/model/path overrides are preserved and historical backups stay frozen.

### Retry policy and coordinate precision

A completely failed spatial search marks its candidate neighborhood as tried,
without charging individual picks' drop counters. The next attempt uses the
highest-density untried neighborhood, with earliest-center tie breaking. Centers
with identical candidate membership share one failed-neighborhood record.
After a detection removes picks, failed records are cleared and counts updated.
If every eligible neighborhood fails on an unchanged pick set, processing stops.
`max_drop` remains the nonnegative integer allowance for a pair rejected from a
successful detection; removal occurs when its rejection count exceeds that value.
The defensive iteration bound is `N * N * (max_drop + 1)`: multiple neighborhoods
may be attempted per pick-set state. Normal completion uses the conditions above.

`unique_stations` must be boolean and supports subnet overrides across local,
AWS, realtime, and repicking workflows. Older configs default to `True`.
Coordinates retain full grid precision through association and magnitude
calculation. Catalog/phase output uses four decimal places for latitude/longitude
and one for depth in km, including merged and AI realtime products. Adaptive
horizontal grids now include every grid point within their upper bounds, just
like explicit ranges. Existing association outputs must be regenerated to adopt
these behavior changes; historical snapshots are unchanged.

### Association performance

Origin-time neighborhoods use two moving boundaries on the sorted picks. A
station-frequency map provides distinct-station counts in a linear sweep;
pair-count mode uses the active pair total. Strict time comparisons retain the
original numeric/UTCDateTime subtraction semantics. Following removal, only
centers whose windows could contain the removed indices are refreshed, using
the same sliding-window calculation. Surviving alternative pairs keep their
station represented in each window.

Picks and drop counters retain stable indices throughout a run. An active mask
replaces repeated array deletion. Density ordering is reused while trying failed
neighborhoods on an unchanged pick set, and rebuilt after removals. Per-cluster
station groups and flattened travel-time views are prepared once; observed
P/S travel times are calculated once per temporal candidate and reused across
counting and median-scoring batches. No pick-by-entire-grid residual cache is
introduced. These are automatic implementation optimizations, with no new tuning
parameters; all existing config settings and deterministic tie rules remain.

Association also remembers failed location searches by exact ordered pick
membership for the duration of each association call. Removing picks invalidates
only failures containing those picks; unchanged failures are reused. Spatial
search bounds and scientific settings are unchanged. The diagnostic
`failure_cache_hits` counts candidate neighborhoods skipped using this cache
(including duplicate centers), not distinct cached clusters.

Saved-table association retains decoded P/S arrays on first station use and
reuses them across searches and dates with unchanged station geometry. Each
worker has its own cache; budget about 625 MiB for 182 stations on a 225,216-cell
float64 P/S grid, in addition to other worker memory. The buffered runner retires
old geometry caches per subnet. No association thresholds or config defaults
change. With debug console verbosity, daily logs include input counts, exponentially
spaced attempt diagnostics, and a completion summary. Table-load time is cumulative for the
current cache; location-search time includes table loading.

## Precomputed layered-model travel times

Preparation accepts layered JSON, combined depth/Vp/Vs CSV, or paired P/S CRE
models. See [Velocity Model Input Formats](VELOCITY_MODEL_FORMATS.md) for
launcher-only settings, units, model-zero elevation and bottom policy. UCVM
extraction is optional; runtime association still takes only the saved .paltt.

Association has two modes: homogeneous `velo_mod=[Vp, Vs]` in km/s, or
`travel_time_table="path/to/table.paltt"`. Default is homogeneous [5.9, 3.5].
Model-file paths in `velo_mod` and `layered_model_options` have been removed.
Build layered-model travel times separately, then distribute the saved table
to local/AWS workers. The table is self-contained and takes precedence over
`velo_mod` and spatial grid settings; missing coverage has no fallback.

Local examples include `0_build_travel_time_table_eg.py` with paths and all
grid/datum/solver controls at the top. The helper lives here in
`PAL_src/travel_time_table.py`; no separate builder config is needed.
The Central California case has the matching `0_build_travel_time_table_cc.py`
and local simplified CVM-S JSON model. Ray-solver/datum options belong only
to the preparation script, not the association config.
Existing .paltt files remain compatible and do not need to be rebuilt.
