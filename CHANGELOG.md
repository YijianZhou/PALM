# PALM Changelog

Major user-facing changes only. Implementation notes and the maintenance
procedure are in [CHANGELOG_INTERNAL.md](CHANGELOG_INTERNAL.md).

## Unreleased

No changes recorded after the v5.0 release baseline.

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
