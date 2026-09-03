# PAL Source

Shared current rule-based PAL implementation used by the local and AWS
executables in `../1_run_pal/`. It contains waveform readers, STA/LTA and
kurtosis picking, association, phase merging, trigger inventories, and reusable
daily orchestration.

The source is synchronized from AI-PAL's rule-based PAL modules. PALM does not
duplicate AI model packages, training code, or real-time AI inference here.
`pick_ensemble.py` is retained because current phase merging understands the
extended pick schema even when a PAL-only workflow produces legacy PAL picks.
