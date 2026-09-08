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

### 0.2 Build the station file

Edit and run `0.2_format_station_file_eg.py`. It groups metadata by `NET.STA`
and divides each station history at all location and channel epoch boundaries.
For every interval it first selects the preferred active location from
`loc_codes`, then selects the preferred active channel band from `chn_codes`.
This allows borehole locations to take priority over surface locations without
changing the final `NET.STA.BAND.LOC` selector.

The script also normalizes gain epochs into one canonical station file.
Internal gaps are divided at their temporal midpoint, while the first and last
epochs are extended when necessary to cover `t_min` through `t_max`. It writes:

- `output/station_eg.csv`: canonical station and gain epochs
- `output/station_eg_metadata_audit.csv`: location and channel choices, missing
  component gains, and duplicate components
- `output/station_eg_gain_interval_audit.csv`: every extended boundary and
  filled internal gap

When only one or two component gains exist, the formatter fills the absent gain
with an available component gain and records that choice in the metadata audit.
Review both audit files before waveform download.
Fullfed inputs are named `input/station_<network>.fullfed` and may be reused
by multiple cases.

### 1.1 Download raw daily waveforms

Edit and run `1.1_download_continuous_data_eg.py`. It uses ObsPy
`MassDownloader` and applies the same two-level priority as the station
formatter: location first, then channel band. It searches for a complete E/N/Z
combination first, then accepts a genuine single-component station when no
complete combination exists. Two-component combinations are treated as
incomplete data. Only one location-band combination is retained per `NET.STA`
and day. `PROVIDERS` remains an ordered priority list,
and `NUM_WORKERS` controls the concurrent download threads per provider.
Raw channel streams are stored under `RAW_ROOT/YYYYMMDD/`, while downloaded
StationXML is reused from `RAW_ROOT/_stationxml/`.

The downloader is restartable. A day is skipped only when it has a
`download_complete.json` marker. For an incomplete day, MassDownloader checks
the existing miniSEED files and downloads missing data. Failed selectors are
recorded in the daily `download_report.csv` and leave a
`download_incomplete.json` marker. Set `OVERWRITE = True` only when existing
raw files must be replaced.

### 1.2 Reconcile downloaded data and station gains

Run `1.2_reconcile_station_file_eg.py` after downloading. It checks that every
retained `NET.STA` daily waveform combination has matching, continuous gain
coverage in `output/station_eg.csv`. When the downloaded location or band
differs from the metadata-preferred choice, the script reconstructs that day
from the matching fullfed component gains and updates only the affected station
interval. If the exact downloaded selector has no overlapping fullfed epoch,
its nearest available fullfed gain epoch is used and identified in the audit.

The original station file is preserved once as
`output/station_eg_before_download_reconciliation.csv`. All checks, updates,
unresolved metadata, incomplete components, and removed non-selected waveform
files are recorded in `output/station_eg_download_reconciliation.csv`.
`NUM_WORKERS` controls concurrent daily miniSEED-header scans; reduce it if the
archive server becomes I/O saturated.

### 2 Validate and merge the raw data

Edit and run `2_merge_raw_data_eg.py`. This is the publication step. It applies
the same structural safeguards used by the AWS PAL reader:

1. Select only the requested network, station, location, band, and component.
2. Accept either one component or a complete E/N/Z set, preferring lettered
   orientations over `1/2/3` alternatives.
3. Reject components with excessive miniSEED fragmentation.
4. Reject streams whose summed sample coverage indicates severe duplication or
   overlap.
5. Interpolate fragments to the sampling rate of the longest fragment.
6. Merge the fragments, fill gaps with zero, and require exactly one trace.
7. Trim to the exact UTC day and reject empty, NaN, or infinite output.
8. Enforce the single location-band selector reconciled for that station-day.

Only accepted streams are written to `CLEAN_ROOT`. Missing components, rejected
streams, unreadable files, selected fallbacks, and coverage ratios are recorded
in `output/merge_eg_report.csv`. Review every `missing` or `rejected*` row.

The merger deliberately preserves raw instrument counts. Gain correction and
acceleration-to-velocity conversion belong to the PAL/AI-PAL runtime and must
not be applied twice.

### 3 Check continuity

Run `3_check_data_continuity_eg.py` after downloading or merging. Set
`DATA_SOURCE` to `"raw"` or `"clean"`. Thick gray lines show operational
intervals read directly from the network fullfed files; thin blue lines show
the actual miniSEED trace intervals read from the selected archive.
`NUM_WORKERS` controls concurrent daily miniSEED-header scans, and progress is
reported every `PROGRESS_EVERY_DAYS` completed directories.

- `output/data_continuity_eg_<source>.csv`
- `output/data_continuity_eg_<source>_low.csv`
- `output/data_continuity_eg_<source>_read_errors.csv`
- `output/data_continuity_eg_<source>_observed_intervals.csv`
- `output/data_continuity_eg_<source>.png`

The summary reports duration-based study coverage and coverage within expected
fullfed operation. Raw data reveals acquisition gaps; clean data describes the
post-merge archive, where short gaps may already have been filled with zeros.
Single-channel and three-component stations are both supported.

### 4 Plot station distribution

Run `4_plot_station_distribution_eg.py` to inspect station coverage. The plot is
written to `output/station_distribution_eg.png`. An optional event catalog CSV
can be overlaid by setting `CATALOG_FILE` and its latitude/longitude columns.

## Operational guidance

- Use UTC day boundaries consistently in all scripts.
- Keep raw downloads until the merge report and continuity diagnostics pass.
- Retain the cleaned daily archive for PAL/AI-PAL; raw downloads may then be
  removed according to the project's data-retention policy.
- Start with a modest `NUM_WORKERS` value. MassDownloader applies it to each
  provider, and FDSN services may throttle aggressive parallel requests.
- Rerunning a script with `OVERWRITE = False` preserves published files and is
  the normal recovery path after interruption.
