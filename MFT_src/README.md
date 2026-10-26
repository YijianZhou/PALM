# MFT Source

## GPU Template Groups

`template_groups.py` plans deterministic geographic/station-aware groups from
the template dictionaries returned by `read_temp`, after existing max_sta and
waveform validity selection. The user setting `template_num_groups` is a
positive integer, default 1. No events or stations are dropped to satisfy a
memory target. More groups than templates produce one group per template;
empty template input produces no scans. Only each group's station union is
passed to the daily loader; unused inventory stations are not loaded.

The bounded heuristic uses farthest-first seeds, at most 50 geographic Lloyd
iterations, and at most three single-template refinement passes. Refinement
minimizes summed station unions, then peak union, without exceeding the initial
geographic peak. Co-located events are handled deterministically. This is not
a guaranteed optimal partition or an automatic GPU memory-fit mechanism.
`scan_grouped_day` freezes previous-day tails across groups, combines next-day
tails afterward, and releases daily arrays/cache even on scan failure.
Per-group CPU phase arrays are also released; loaded template snippets remain
available for the job. CPU scanning and shared PAL association are unchanged.

Shared CPU and GPU implementation of the PALM MFT matched-filter algorithm.
Run it through a numbered launcher in `../2_run_mft/`; those launchers select a
case config and provide the shared PAL data-pipeline modules.

Modules, entry points, functions, logs, and executable workflows consistently
use the generic `mft` name.

`cut_template.py` is the single template-cutting implementation. It batches
phase windows by station-day to avoid repeated waveform reads and stores four
compact three-component `float32` NPY shard arrays: low-rate detection,
high-rate detection verification, high-rate P, and high-rate S. No surrounding
full-template waveform span is persisted.
`template_store.py` validates both rates in the store manifest, memory-maps the
paired shards, and constructs the detection, P-pick, and S-pick windows
consumed by both CPU and GPU MFT without opening intermediate SAC files.

Continuous waveforms follow the same split. The high-rate data remain in host
memory; the detection-rate data and sliding norms are the only full-day arrays
placed on the GPU. `samp_rate` configures detection and
`phase_samp_rate` configures phase refinement and amplitude measurement.
The high-rate detection window is correlated on CPU over `pick_win_p`; its
three-component maximum is written as `cc_det_phase` on each phase row.

Native rates at or above `phase_samp_rate` are handled adaptively with
zero-phase polyphase FIR resampling; lower-rate inputs are rejected. The
configured high-rate bandpass defaults to 1-16 Hz, after which the same
polyphase path creates the 50 Hz detection representation. Local inputs must
already satisfy the cleaned daily archive contract from `preprocess/`.
Single-day files are not merged or repaired here; adjacent clean days are
strictly stitched only when a padded or buffered read crosses midnight.

`associate_mft.py` is the final shared stage used by both numbered MFT
launchers. It associates duplicate detections across all launcher segments and
writes `catalog.csv`, `phase.csv`, `event.dat`, and `dt.cc`. Association belongs
here rather than in the location workflow so the event definition and
differential-time observations are fixed before any relocation program is run.
# Phase-Window QC

Both MFT scanners use `phase_window_qc.py` to shorten P correlation templates
at the station's template S arrival (`adaptive_p_window=True`). Stored template
shards and detection windows are unchanged. `associate_mft.py` excludes original
P/S lags on the configured search boundaries from relocation (`reject_boundary_phase_shifts=True`),
before applying event-origin corrections. Raw measurements are retained.
See `../2_run_mft/README.md` for parameters and rerun guidance.

