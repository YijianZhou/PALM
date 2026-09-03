# Location

`hypodd/` relocates the event set finalized by `../2_run_mft/`. MFT association
and differential-time construction are completed by the MFT launcher, which
writes `catalog.csv`, `phase.csv`, `event.dat`, and `dt.cc` to its configured
output directory.

Set `mft_output_root` in `hypodd/config.py` to that directory, then configure the
hypoDD executable, station file, time and geographic ranges, and relocation
grid. Running `run_hypoDD.py` copies `event.dat` and `dt.cc` into its local
working input directory, prepares station and grid files, and runs relocation.
It does not repeat or modify MFT association.

Keep `hypodd_depth_offset_km` equal to the MFT setting that generated
`event.dat`; the offset keeps initial hypoDD depths below the surface and is
removed from relocated output depths.
