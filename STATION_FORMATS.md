# Station File Formats

Station files are headerless CSV (`.sta` and `.csv` are equivalent). Coordinates
are latitude, longitude in degrees and elevation in metres. Gains are instrument
sensitivities: counts per physical unit, normally counts/(m/s) or counts/(m/s^2).

## Default: Complete Band/Location Inventory

```text
NET.STA.BAND.LOC,lat,lon,elevation_m,gain_E,gain_N,gain_Z,start,end
XX.TEST.HH.,34.0,-118.0,100,1000000000,1000000000,1000000000,2020-01-01,2021-01-01
XX.TEST.HN.10,34.0,-118.0,100,200000,200000,200000,2020-01-01,2021-01-01
```

These values are illustrative. `BAND` is two characters, not a full component
code. An empty location is a trailing dot; `--` also means empty location.
Waveform filenames instead use `NET.STA.LOC.CHANNEL`.

Retain **all candidate band/location combinations**, with one row per gain
epoch. Alternative selectors may overlap in time. Conflicting overlapping
epochs for the same selector are rejected. Epochs are half-open
(`start <= sample time < end`); dates/timestamps are UTC. Repeated rows retain
all epochs rather than overwriting previous rows. Station geometry uses the
first row's coordinates; resolve station relocations explicitly.

PAL, AI-PAL local/AWS/realtime, and PALM MFT identify stations by `NET.STA`.
The suffix specifies gain metadata, not a forced waveform selector. The actual
available waveform combination is selected independently for each station-day
(or input segment in realtime). Gain lookup then uses the selected original
band, location, component, and sample time. Calibration occurs before fragment
merging or single-channel replication, including gain changes within a trace.
Detailed metadata missing for the selected stream is an error, not permission
to borrow another instrument's sensitivity. Unfiltered rolling buffers retain
their own calibration when the next day's selected instrument changes.

## Selection Settings

Use the same settings in preprocessing and inference:

```python
self.station_selection_order = "channel_first"  # or "location_first"
self.channel_priority = ["HH", "BH", "EH", "HN", "EN", "SH"]
self.location_priority = ["10", "20", "01", "02", "00", ""]
```

The order is configurable because borehole strong motion need not be preferred
over surface broadband. There is no component-count-first ranking: a usable
higher-priority single-channel station can outrank a lower-priority three-channel
combination. Three-component model inputs retain the existing replication rules.
Preprocess scripts expose the corresponding `chn_codes`/`loc_codes` or priority
lists and `station_selection_order` near the top. Unknown codes rank after the
listed ones. FDSN requests/metadata export remain limited to configured bands.

Selection is based on available file/trace metadata; it does not add a second
waveform download. AWS chooses from the existing S3 listing before fetching the
selected files. Local standard filenames can be selected before decoding;
nonstandard filenames require trace-header-based selection.

## Simplified Inputs Remain Supported

| Layout | Columns |
| --- | --- |
| Common gain | `NET.STA,lat,lon,elevation_m,gain` |
| Component gains | `NET.STA,lat,lon,elevation_m,gain_E,gain_N,gain_Z` |
| Gain epoch | `NET.STA,lat,lon,elevation_m,gain_E,gain_N,gain_Z,start,end` |

`NET.STA.BAND` is also supported: its location is unspecified. Missing suffixes
act as generic metadata, so these files cannot distinguish alternative
instruments' sensitivities. Exact band/location metadata takes precedence over
generic entries. Existing local unqualified epoch files retain their legacy
nearest-epoch fallback; use the detailed inventory for strict time coverage.
Do not combine conflicting static and dated rows for one selector.

Four-column geometry-only rows imply gain=1 (already calibrated data only).
The inventory reader also accepts `4 + 5*N` columns containing repeated
`gain_E,gain_N,gain_Z,start,end` blocks. Blank lines and `#` comments are allowed.

## Preprocessing and Migration

`preprocess/0.2_format_station_file_eg.py` converts channel-level, pipe-separated
FDSN fullfed metadata (`input/station_<network>.fullfed`) to the complete nine-column
inventory. It retains original operational intervals: no gap filling, extension,
or pruning to one preferred stream. Missing component gains use the documented
fill rules and are listed in the audit, including single-channel instruments.

`1.1` downloads one available combination per station-day. `2` applies the same
configurable priorities to available raw data and writes the clean archive.
Neither requires a post-merge station-file rewrite. `1.2` is an **optional,
read-only coverage audit by default**. Missing metadata should be corrected by
regenerating the complete inventory from fullfed; its opt-in legacy repair mode
is intended only for old single-selector inventories.

Changing priorities does not overwrite already completed downloads, merged
files, or picking outputs. Explicitly rerun the affected stages with their
existing overwrite controls when rebuilding data. Runtime selection cannot
recover alternatives already discarded from a cleaned archive.

## Packaged Examples and Exceptions

Local PAL/AI-PAL and PALM MFT executables default to
`input/example_pal_format4.sta`; format1/2/3 remain compatibility examples.
The format4 examples use **illustrative HH/blank-location selectors** derived
from the older examples, not verified fullfed inventories. Replace them with
real metadata for your data. Existing SoCal AWS and six realtime subnet files
keep their case-specific names and values; their simplified band-qualified
metadata remains usable, but only regenerated fullfed inventories can provide
exact gains for all available locations/bands.

Local training uses `input/eg_station.csv`; AWS training shares the AWS metadata
reader. Locator station exporters still require a deduplicated `NET.STA`
geometry list, not the multi-selector inventory. The supplied simple locator
station files remain unchanged. Resolve coordinate conflicts before export.
