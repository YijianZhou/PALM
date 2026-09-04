# MFT Source

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
