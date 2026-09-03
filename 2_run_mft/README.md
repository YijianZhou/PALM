# Run Matched Filter

This directory contains case-level executables for the MFT matched-filter
implementation in `../MFT_src/`. PAL implementation and waveform metadata
helpers are shared from `../PAL_src/`.

## Configuration

The packaged example uses `CASE_CODE = "eg"` and `config_eg.py`. For a new case,
copy the numbered scripts and config with a common suffix, then change
`CASE_CODE`, paths, time ranges, and model parameters in their user-settings
blocks.

`PALM_ROOT` defaults to the parent of this directory. It can instead point to an
installed PALM package when the executable directory is copied elsewhere.

## Workflow

1. Select located PAL or AI-PAL events as templates:

   ```bash
   python 1_select_templates_eg.py
   ```

   Set `TEMPLATE_SOURCE` to `"pal"` or `"ai-pal"`. Then set that source's
   `detection` and `located` paths in `TEMPLATE_INPUTS`. The two files must
   come from the same catalog and retain matching event IDs. The selector
   writes the source-neutral `input/eg_mft.temp`, which is consumed by all
   later MFT launchers.

   `"ai-pal"` is the recommended source after self-supervised training and
   enhanced continuous detection. Use the final postprocessed AI-PAL phase
   catalog when repicking/reassociation was enabled, run it through location,
   and pair that located result with the exact pre-location phase catalog.
   `"pal"` provides the conservative direct PALM path and a useful baseline.

2. Cut and quality-control template waveforms:

   ```bash
   python 2_cut_templates_eg.py
   ```

   The unified cutter groups work by station and UTC day, reads each required
   waveform span once with preprocessing padding, and writes synchronized
   compact `float32` NPY shard sets instead of individual SAC files. It stores
   only four windows used later: the detection window at `samp_rate` (50 Hz by
   default), the same detection window at `phase_samp_rate` (100 Hz by default),
   and the P and S windows at `phase_samp_rate`. It validates three finite
   components and exact sample counts before applying the configured P-wave
   STA/LTA SNR threshold. `template_shard_size` controls the maximum templates
   per shard set.

   The resulting template store contains:

   ```text
   Example_templates/
     template_manifest.json
     template_index.npy
     detection_shards/*.npy
     phase_detection_shards/*.npy
     p_shards/*.npy
     s_shards/*.npy
   ```

   CPU and GPU MFT memory-map these shards and resolve synchronized
   event/station rows through the portable index. Earlier stores containing
   full single- or dual-rate template spans must be recut. Completed
   station-day groups are reused unless
   `OVERWRITE_TEMPLATES = True`.

   Use an empty `OUTPUT_ROOT` for the first NPY-shard run. Template directories
   produced by the former SAC cutter are not read by the new MFT workflow and
   can be archived or removed after the NPY store passes an MFT startup test.

3. Run one matched-filter implementation:

   ```bash
   python 3.1_run_mft_gpu_eg.py
   # or
   python 3.2_run_mft_cpu_eg.py
   ```

Both launchers divide the requested half-open time range into contiguous
`SEGMENT_DAYS` blocks and write one catalog and phase file per block. Do not run
the CPU and GPU alternatives into the same output directory concurrently.

With `RUN_ASSOCIATION = True`, the launcher performs one final association pass
after every segment finishes. It reads all segment phase files together, sorts
the detections globally by origin time, and combines detections of the same
physical event made by different templates. Running this once over the complete
time range prevents an event near a segment boundary from being split between
two independent association jobs.

The association settings live in `config_<CASE_CODE>.py`:

```text
association_origin_time_tolerance_sec
association_detection_cc_min
association_phase_cc_min
association_max_phase_shift_sec
association_min_neighbor_templates
association_max_neighbor_templates
association_start_event_id
hypodd_depth_offset_km
```

Raw per-segment `catalog_<start>-<end>.dat` and
`phase_<start>-<end>.dat` files are retained for diagnostics. The final products
in `OUTPUT_ROOT` are:

```text
catalog.csv  associated event catalog with a header
phase.csv    associated events and representative P/S picks
event.dat    hypoDD event input
dt.cc        cross-correlation differential-time input
```

Known template self-detections retain the template event ID. New detections use
IDs beginning at `association_start_event_id`, skipping any template IDs. The
catalog's `template_count` is the total number of detections in the event's
origin-time cluster; the configured neighbor maximum applies only to the
template pairs retained for relocation. `event.dat` depths include
`hypodd_depth_offset_km`, and the relocation stage removes the same offset from
the resulting depths.

Each target UTC day reads `data_buffer_sec` of waveform context from both the
previous and following day (default 30 seconds in `config_<CASE_CODE>.py`). The
combined stream is preprocessed and searched as one interval, which preserves
templates and phase-pick windows across midnight. Only detections whose origin
time is in the target half-open interval `[day_start, next_day_start)` are
written, so adjacent daily runs do not duplicate events.

The continuous waveform is prepared at `phase_samp_rate` once, retained in CPU
memory for P/S cross-correlation, differential-time measurement, and amplitude
measurement, and downsampled to `samp_rate` for the full-day matched-filter
scan. The GPU launcher transfers only detection-rate data and normalization
arrays to GPU memory. Thus the expensive scan stays at 50 Hz while `dt_p` and
`dt_s` retain 0.01-second sampling at the default 100 Hz phase rate. Input
waveforms must have a native rate at least as high as `phase_samp_rate`; a
lower-rate station is skipped rather than upsampled and presented as a
high-resolution phase measurement.

After a 50 Hz event detection, phase picking also correlates the complete
detection window at `phase_samp_rate` against the retained high-rate continuous
data. It searches over the same `pick_win_p` range, averages all three component
CC traces, and reports the maximum as `cc_det_phase`. Each station row in the
MFT phase output is therefore:

```text
net_sta,tp,ts,dt_p,dt_s,s_amp,cc_p,cc_s,cc_det_phase
```

`cc_det_phase` is a diagnostic and does not currently reject a station pick or
alter the event detection threshold.

`taper_max_length_sec` explicitly controls the preprocessing taper at each
outer edge of that buffered stream. Its default is 5 seconds, matching the
previous MFT behavior. With the default 30-second buffer, the target day begins
and ends 25 seconds inside the untapered portion of the search stream.

## Source Isolation

The launchers use `MFT_src/workflow.py` to prepend `2_run_mft/`, `MFT_src/`,
and `PAL_src/` to the child process import path and set `PALM_MFT_CONFIG` to
the selected case module. The executable directory therefore contains only
numbered case launchers, the user configuration, input data, and this README.
No launcher copies or modifies files under either shared source directory.
