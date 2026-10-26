# PALM Changelog

Major user-facing changes only. Implementation notes and the maintenance
procedure are in [CHANGELOG_INTERNAL.md](CHANGELOG_INTERNAL.md).

## Unreleased

- Hide detailed PAL association attempt/cache counters at normal console
  verbosity; retain them with console_verbosity="debug".

- Default routine picking and association console progress to 60 seconds,
  including rapid date-completion updates; preserve immediate failure reports.

- Balance offline association dynamically across pending dates, retain worker
  caches, report active dates, and fail promptly on unexpected worker exits.

- Avoid repeating failed association searches after unrelated events are found,
  without restricting the spatial search or changing association thresholds.

- Reuse decoded station travel-time tables during association instead of
  repeatedly loading them; add flushed search diagnostics to daily logs.

- Added optional local `0_build_travel_time_table_eg.py` preparation with
  editable settings at the top and a shared `PAL_src` helper; no separate
  builder configuration file is needed.

- Use `nan` for unavailable station gains; gain `1` now correctly permits
  already-calibrated data to contribute to magnitude. Missing-gain stations
  remain usable for picking. Migrate older gain-1 placeholders before rerunning.

- Adapt MFT P correlation windows to each station's template S arrival and
  exclude search-boundary P/S differential times from hypoDD relocation by
  default. Both behaviors are configurable; CPU and GPU paths are aligned.

- Association now accepts only homogeneous `velo_mod=[Vp, Vs]` or a saved
  `travel_time_table` (.paltt). Build layered-model tables separately; runtime
  model-file inputs and `layered_model_options` have been removed from active
  workflows. Existing saved tables and homogeneous settings remain compatible.

- Load precomputed P/S times from one `.paltt` file using optional
  `travel_time_table`; skip unavailable station/cell pairs safely.

- Accept scalar `max_res=r` as shorthand for equal P/S residual limits `[r, r]`.

- PAL spatial association now checks both P and S arrivals. Configure homogeneous
  `velo_mod=[5.9, 3.5]` (km/s) and `max_res=[res_p, res_s]` (seconds);
  active workflow configs are migrated, retaining regional tolerances.

- Speed up PAL association with sliding-window station counts, local count
  updates, stable pick indices, and reused per-cluster calculations. Existing
  association settings and selection rules are preserved.

- PAL now counts distinct stations by default (`unique_stations=True`) while
  retaining alternative P-S pairs for spatial selection. Failed neighborhoods
  yield to alternative centers; retries no longer discard their candidates.
  Fix adaptive grid coverage and preserve internal location precision. Catalog
  coordinates use four decimal places for lat/lon and one for depth.

- GPU MFT can scan automatic template groups sequentially with
  `template_num_groups` (default 1), loading only each group's required stations
  to reduce daily waveform memory. Detection and final association rules remain
  unchanged.

- PAL association now searches up to ten origin times at 0.1-second spacing
  near the cluster median, bounded by its 5th-95th percentiles. Solutions maximize
  associated count, then minimize median absolute P residual. Temporal step,
  maximum span, and point count are configurable per subnet (defaults: 0.1 s,
  1.0 s, and 10 points).




## v5.1 - Release Preparation

Changes since v5.0. Prepared 2026-10-03; GitHub publication/tagging is performed separately.

- Align shared PAL processing with current AI-PAL: configurable band/location
  selection, epoch-aware NET.STA.BAND.LOC station/gain inventories, and continued
  support for simplified station files. MFT also uses the unified station selection.
- Preserve arrival picks when calibration is unavailable. Permit warned same-band
  gain fallback, never cross-band borrowing, and treat unit-gain placeholders as
  missing. Uncalibrated amplitudes are NaN and excluded from PAL magnitude estimates.
- Add PAL magnitude QC: at least three distinct valid stations and population
  standard deviation <=1.0 before taking the median. Failed magnitude is -1;
  other negative magnitudes are retained. MFT magnitude estimation is unchanged.
- Improve local PAL date-block parallelism, live progress, numerical-thread
  controls and resume performance. Share buffered picks across association
  subnets and fix trigger-count ownership at daily boundaries.
- Simplify PAL picking/association entry points, make association buffering
  explicit, and publish an integrated association-rate CSV beside range-level
  catalog/phase outputs.
- Rename waveform cleaning to to_clean (legacy to_prep remains supported), improve
  incomplete-window amplitude handling, and consolidate waveform preparation
  under preprocess rather than duplicate MFT download launchers.

### Upgrade Notes

- Update source and workflow configs together. Review station gain inventories,
  buffer settings, and the current numbered launchers.
- Reprocess affected data to correct earlier calibration/magnitude results;
  existing products are not automatically repaired. Treat -1 as unavailable
  magnitude in PAL catalog analysis, while retaining other negative magnitudes.
- Future changes are recorded above under Unreleased, relative to v5.1. The
  source/config/documentation fingerprint is in RELEASE_BASELINE.json.

## v5.0 - Release Preparation

Compared with the supplied v4.1 release; reviewed 2026-09-17.

- Reorganized the former `1_PAL/` and `2_MESS/` workflows into
  `1_run_pal/`, `2_run_mft/`, `3_location/`, and shared
  `PAL_src/` and `MFT_src/` implementations.
- Updated PAL workflows alongside AI-PAL: local combined/split execution,
  resumable AWS processing, explicit association bounds, trigger diagnostics,
  and direct final catalog/phase products.
- Added station preparation, MassDownloader downloads, station-file
  reconciliation, validated daily merging, and continuity inspection.
- Replaced SAC template collections with compact, memory-mapped NPY shards.
  Both PAL and AI-PAL located detections can supply templates.
- Added separate detection and phase-refinement rates, shared anti-alias
  resampling, and buffered daily processing for CPU/GPU matched filtering.
- Added a common final MFT association pass across processing segments,
  publishing `catalog.csv`, `phase.csv`, `event.dat`, and `dt.cc`
  for catalog inspection and hypoDD relocation.

### Upgrade Notes

- Start from the new numbered launchers and configs rather than copying v4.1
  executables over shared source. The MESS name is now MFT; this does not
  introduce matched filtering or CPU/GPU execution for the first time.
- Recut old SAC templates into the current NPY store. Template and continuous
  processing settings must agree; incompatible stores are rejected.
- Daily ownership is shifted by `data_buffer_sec`; do not mix old calendar-day
  results with new shifted results. Update consumers for the current phase
  schema, including `cc_det_phase` in MFT station rows.
- See [PAL](1_run_pal/README.md), [MFT](2_run_mft/README.md), and
  [location](3_location/README.md) for paths, output formats, and migration.

## v4.1 - Comparison Baseline

The supplied release contains PAL detection/location and CPU/GPU MESS matched
filtering with cross-correlation phase refinement. v5.0 retains that scientific
workflow while revising its implementation, storage, and interfaces.
