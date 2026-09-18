# Illustrative PAL Outputs

These files demonstrate the **format, content, and layout** of current PAL
outputs. They are NOT the exact results of running the bundled example code,
station file, or configuration. No waveform picking or PAL association was run
to produce them. Do not use this directory as a resume cache or training dataset.

The example uses three events from the saved realtime file
`3_run_ai_pal/run_ai_pal_realtime/output/phase_final_20260909T112456Z_20260909T115502Z.dat`.
Origin times, locations, magnitudes, arrival times, and amplitudes were retained
for selected station rows (at most eight distinct NET.STA per event). Channel
families were collapsed to NET.STA, retaining the first row per station.
AI ensemble metadata was removed and replaced with the current PAL defaults.
Thus these are PAL-format illustrations derived from AI-PAL data, not PAL detections.
The example dates intentionally differ from the launcher's placeholder dates.

## Files

```text
catalog_20260909-20260910.dat
phase_20260909-20260910.dat
picks/
  2026-09-09.pick
  2026-09-09.trigger_counts.csv
association/
  merged/
    catalog_2026-09-09.dat
    phase_2026-09-09.dat
  association_rates/
    association_rate_2026-09-09.csv
```

The root catalog and phase file are the direct user-facing results. Here they
match the single daily merged result. Actual runs also create logs, subnet
intermediates, merge diagnostics, and resume status files; these are omitted.
There are 3 events, 20 associated station P/S pairs, and 21 accepted picks.
The extra unassociated pick is synthetic: one pair shifted forward ten minutes.

## Row Formats

All times are UTC. DAT and PICK files are comma-separated without column headers.

- Catalog rows and phase event headers:
  `ot,latitude,longitude,depth_km,magnitude`.
- Daily PAL pick rows:
  `net_sta,tp,ts,s_amp,p_snr,amp_ratio,A12,A13`.
  There is **no estimated origin-time column** in pick files.
  The four diagnostic values are illustrative constants
  `20.0,2.0,2.0,1.0`, not measurements from the source waveforms.
- Canonical merged phase station rows (19 columns):
  `net_sta,tp,ts,s_amp,quality,p_prob,s_prob,tp_std,ts_std,p_prob_std,s_prob_std,num_support,sources,picker_window_vote_ratios,picker_uncertainties,pick_provenance,p_snr_e,p_snr_n,p_snr_z`.
  Plain PAL rows use quality/probabilities/channel SNR = -1 (unavailable),
  STD = 0, support = 1, empty AI metadata, and provenance = `initial`.
  These defaults are not measured zero uncertainty or AI quality assessments.

The trigger-count and association-rate CSVs have column headers. Trigger totals
are illustrative: accepted picks plus two rejected triggers per station.
In the association-rate CSV, `num_picks` means raw trigger count;
`num_unassociated_picks = num_picks - num_associated_picks`, and
`association_ratio = num_associated_picks / num_picks`.
Counts are internally consistent, but do not describe measured PAL performance.

Daily filenames refer to nominal date D. With `data_buffer_sec=60`, ownership
is the shifted interval [D - 60 s, D + 1 day - 60 s); all sample arrivals and
event origins fall comfortably inside that interval.
