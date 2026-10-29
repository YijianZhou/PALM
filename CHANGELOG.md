# PALM Changelog

Major user-facing changes only. Implementation notes and the maintenance
procedure are in [CHANGELOG_INTERNAL.md](CHANGELOG_INTERNAL.md).

## Unreleased

Future changes will be recorded relative to v5.2.

## v5.2 - Release Preparation

Changes since v5.1. Prepared 2026-10-29; GitHub publication/tagging is performed separately.

- Improve PAL association with distinct-station counting, P/S residual checks,
  configurable origin-time grid search, adaptive spatial coverage, and faster
  candidate searches with reuse of failed-search results.
- Support precomputed travel-time archives built from JSON, combined Vp/Vs CSV,
  or paired CRE velocity models. Include example 1D model preparation and local
  table-building launchers for local and AWS workflows.
- Store travel-time arrays in float32 and share read-only tables across offline
  date workers. Existing float64 archives remain readable. Shared stores use
  executable `ASSOCIATION_TABLE_DIR`, never system temp, with checked writes
  and normal-run cleanup.
- Dynamically schedule unfinished PAL picking and association dates, retaining
  resume checks, waveform buffers, and persistent workers.
- Use `nan` for missing station gains; gain `1` is valid for calibrated data.
  Missing-gain stations remain usable for picking but not magnitude.
- Simplify default progress output while retaining detailed debug diagnostics.
  Multi-control launchers use stage-specific `XXX_NUM_WORKERS` names.
- Add automatic GPU template grouping through `template_num_groups` (default 1),
  scanning groups sequentially and loading only their required stations.
- Adapt MFT P correlation windows to template S arrivals and exclude P/S
  differential times at search boundaries from relocation by default.
  Both options are configurable and aligned across CPU/GPU paths.

### Migration Notes

- Replace old gain-1 placeholders with `nan` only where gains are unavailable.
- Runtime association uses homogeneous `velo_mod=[Vp, Vs]` or a saved
  `travel_time_table`; build layered-model tables before running association.
- Ensure `ASSOCIATION_TABLE_DIR` has enough disk space for shared tables.
  Per-worker search workspaces still require additional RAM.




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
