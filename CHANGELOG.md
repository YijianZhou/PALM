# PALM Changelog

Major user-facing changes only. Implementation notes and the maintenance
procedure are in [CHANGELOG_INTERNAL.md](CHANGELOG_INTERNAL.md).

## Unreleased

- 2026-10-01: PAL range exports include one integrated association-rate CSV
  beside phase/catalog outputs, ready for training sample preparation.

### 2026-09-30: Unit-Gain Placeholder

- Treat gain 1.0 as missing calibration in local/AWS PAL and AI-PAL realtime
  processing, including legacy gain layouts. Retain waveform picking but output
  NaN station amplitude and exclude it from magnitude calculation if a selected
  component is uncalibrated. Existing amplitudes require reprocessing.
- AI-PAL repick version is advanced to invalidate previous repick completion
  records. Regression tests cover unit gain on all or one component and both
  AI-PAL/PALM calibration implementations.

### 2026-09-30: PAL Magnitude QC

- Require three distinct valid station magnitude estimates and population std
  <= 1.0, configurable with `mag_min_stations` and `mag_max_std`.
- Replace unconditional worst-station removal with a spread check followed by
  the median. Failed magnitude is -1 without discarding arrival picks or events;
  missing-gain amplitudes remain NaN. Magnitude merging excludes the -1 sentinel
  and retains other negative values. This supersedes the earlier NaN event-mag
  convention for PAL; MFT magnitude estimation is unchanged.
- Shared helper and parameter propagation cover local/AWS PAL and AI-PAL
  realtime/reassociation. Repick completion checks include magnitude thresholds.
  Tests cover the reported M6.56 case, missing amplitudes, distinct stations,
  negative magnitudes, threshold boundaries and AI-PAL/PALM source parity.

- 2026-09-29: Removed redundant waveform download examples from `2_run_mft`.
  Use the shared `preprocess/` workflow before template cutting and MFT.

- Missing instrument gains no longer remove arrival-time picks. Same-band
  location/epoch fallback is allowed; no cross-band gain borrowing. Uncalibrated
  amplitudes are nan and excluded from event magnitude.

- 2026-09-24: PAL amplitude QC no longer crashes on incomplete component
  windows. Unavailable checks are recorded as `nan` and bypassed; available
  failing checks still reject picks.

- Local picking exposes `threads_per_worker` for native numerical libraries,
  matching the AWS control; example launchers default to 2.

- Local PAL picking now uses independent date-block processes instead of
  station threads. Worker count controls date blocks; daily outputs and resume
  checks are unchanged, with per-block logs and aggregate console progress.

- Simplified local PAL entry points to `1_run_pal_pick_eg.py` and
  `2_run_pal_assoc_eg.py`; removed the redundant combined launcher.

- Local picking resumes without reading waveform tails for every skipped day;
  previous-day context is loaded only when picking actually resumes.

- Fixed daily trigger-count ownership at buffered boundaries: pre-QC candidates
  now use refined P time like accepted picks, avoiding false count failures.

- Local picking retains waveforms with missing gain locations/epochs, using
  warned same-band gain fallback or uncalibrated counts as a last resort.

- Local PAL picking now prints live day/station progress and a 30-second
  heartbeat while retaining detailed output in a line-buffered log.

- Daily PAL association now shares buffered picks across all subnets inside
  parallel contiguous date blocks, eliminating per-subnet pick-file rereads.

- PAL configs explicitly use a 30 s association buffer. Local PAL examples
  now enable cross-day association and pick halos, with bounded per-worker
  daily pick caching. Association resumes detect buffer-policy changes.

- Renamed PAL raw-waveform cleaning control to `to_clean`; legacy `to_prep`
  configs and reader calls remain supported. Filtering is unchanged.

- Unified configurable band/location selection (band first by default) and
  fullfed-derived, time-dependent `NET.STA.BAND.LOC` gain inventories across
  local and AWS PAL, AI-PAL realtime, and PALM MFT. Simplified files remain
  supported; routine post-merge station-file reconciliation is unnecessary.

- Fixed PAL event merging that discarded valid negative magnitudes. Missing
  magnitudes now use `nan`, not the physically valid value -1. Existing
  affected catalogs require magnitude recalculation; they are not auto-repaired.

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
