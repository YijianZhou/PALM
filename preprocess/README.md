# Preparing Continuous Waveforms for AI-PAL

This directory contains an example (`eg`) workflow for preparing a local,
daily-archived continuous-waveform data set that PAL and AI-PAL can read. The
scripts are intentionally case-neutral: edit the **USER SETTINGS** near the top
of each script for a new study area and time range.

Run the scripts from this directory in numeric order. Relative input and output
paths are resolved from this directory, so the workflow also works when a script
is launched from elsewhere.

## Requirements

- Python 3
- ObsPy
- NumPy
- Matplotlib (for the two diagnostic plots)
- Network access to the selected FDSN providers during metadata and waveform
  download

## Output contracts

The station file used by PAL and AI-PAL has no header and contains nine CSV
columns:

```text
NET.STA.BAND.LOC,latitude,longitude,elevation_m,gain_E,gain_N,gain_Z,start,end
```

Each row describes one station/channel epoch. `BAND` is the two-character
channel prefix such as `HH` or `BH`. A blank location is represented by an
empty field after the final dot. Times are treated as half-open intervals:
`start <= time < end`.

The cleaned waveform archive is:

```text
DAILY_ROOT/
  YYYYMMDD/
    NET.STA.LOC.BANDE.mseed
    NET.STA.LOC.BANDN.mseed
    NET.STA.LOC.BANDZ.mseed
```

Use `--` in filenames for a blank location code. Configure the PAL or AI-PAL
`DATA_DIR` to point to `DAILY_ROOT`. Because this workflow has already selected
and merged the components, use `to_prep = False`; choose `to_filter` separately
according to whether these files have already received the model's configured
frequency filter. The example merger preserves raw counts, so runtime gain and
unit conversion still occur.

## Workflow

### 0.1 Download station metadata

Edit and run `0.1_download_station_metadata_eg.py`. It downloads FDSN text
metadata for each configured network into `input/`. Choose networks, geographic
bounds, and the complete study interval before continuing.

### 0.2 Select station and channel epochs

Edit and run `0.2_format_station_file_eg.py`. It filters channel families by
priority, selects metadata overlapping the study bounds, and writes:

- `output/station_eg_raw.csv`: selected station epochs before temporal gap
  normalization
- `output/station_eg_metadata_audit.csv`: channel conflicts, missing component
  gains, and duplicate components

When only one or two component gains exist, the formatter fills the absent gain
with an available component gain and records that choice in the audit. Review
all audit rows before a production run.

### 0.3 Normalize gain intervals

Edit and run `0.3_normalize_station_gain_intervals_eg.py`. It fills every
internal metadata gap at the temporal midpoint between adjacent gain epochs.
`STUDY_START` and `STUDY_END` optionally extend the first and last epochs to
cover the complete study interval. Static one-gain and three-gain rows pass
through unchanged.

This step writes the canonical `output/station_eg.csv` consumed by all later
scripts and records every adjustment in
`output/station_eg_gain_interval_audit.csv`. Review that audit before waveform
download.

### 1 Download raw daily waveforms

Edit and run `1_download_continuous_data_eg.py`. For every station epoch and UTC
day, it requests the selected band from the configured FDSN providers in order.
Raw channel streams are retained separately under `RAW_ROOT/YYYYMMDD/`.

The downloader is restartable. A day is skipped only when it has a
`download_complete.json` marker. Failed selectors are recorded in the daily
`download_report.csv` and leave an `download_incomplete.json` marker, allowing
the day to be retried. Set `OVERWRITE = True` only when existing raw files must
be replaced.

### 2 Validate and merge the raw data

Edit and run `2_merge_raw_data_eg.py`. This is the publication step. It applies
the same structural safeguards used by the AWS PAL reader:

1. Select only the requested network, station, location, band, and component.
2. Prefer canonical `E/N/Z` channels over `1/2/3` alternatives.
3. Reject components with excessive miniSEED fragmentation.
4. Reject streams whose summed sample coverage indicates severe duplication or
   overlap.
5. Interpolate fragments to the sampling rate of the longest fragment.
6. Merge the fragments, fill gaps with zero, and require exactly one trace.
7. Trim to the exact UTC day and reject empty, NaN, or infinite output.
8. Try lower-priority location/channel alternatives after a rejection.

Only accepted streams are written to `CLEAN_ROOT`. Missing components, rejected
streams, unreadable files, selected fallbacks, and coverage ratios are recorded
in `output/merge_eg_report.csv`. Review every `missing` or `rejected*` row.

The merger deliberately preserves raw instrument counts. Gain correction and
acceleration-to-velocity conversion belong to the PAL/AI-PAL runtime and must
not be applied twice.

### 3 Check continuity

Run `3_check_data_continuity_eg.py` after merging. It compares the cleaned
archive with station epochs and reports both any-component and complete
three-component availability:

- `output/data_continuity_eg.csv`
- `output/data_continuity_low_eg.csv`
- `output/data_continuity_read_errors_eg.csv`
- `output/data_continuity_eg.png`

One- or two-component days can still be visible in the first ratio, while the
three-component ratio identifies days that provide the preferred input.

### 4 Plot station distribution

Run `4_plot_station_distribution_eg.py` to inspect station coverage. The plot is
written to `output/station_distribution_eg.png`. An optional event catalog CSV
can be overlaid by setting `CATALOG_FILE` and its latitude/longitude columns.

## Operational guidance

- Use UTC day boundaries consistently in all scripts.
- Keep raw downloads until the merge report and continuity diagnostics pass.
- Retain the cleaned daily archive for PAL/AI-PAL; raw downloads may then be
  removed according to the project's data-retention policy.
- Start with modest `NUM_WORKERS` values. FDSN services may throttle aggressive
  parallel requests, while local merging is usually limited by storage I/O.
- Rerunning a script with `OVERWRITE = False` preserves published files and is
  the normal recovery path after interruption.
