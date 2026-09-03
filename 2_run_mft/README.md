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

   `CUT_METHOD = "intense"` stores compact phase-centered templates;
   `CUT_METHOD = "long"` retains the legacy long-window representation.

3. Run one matched-filter implementation:

   ```bash
   python 3.1_run_mft_gpu_eg.py
   # or
   python 3.2_run_mft_cpu_eg.py
   ```

Both launchers divide the requested half-open time range into contiguous
`SEGMENT_DAYS` blocks and write one catalog and phase file per block. Do not run
the CPU and GPU alternatives into the same output directory concurrently.

Each target UTC day reads `data_buffer_sec` of waveform context from both the
previous and following day (default 30 seconds in `config_<CASE_CODE>.py`). The
combined stream is preprocessed and searched as one interval, which preserves
templates and phase-pick windows across midnight. Only detections whose origin
time is in the target half-open interval `[day_start, next_day_start)` are
written, so adjacent daily runs do not duplicate events.

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
