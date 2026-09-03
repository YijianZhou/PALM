"""Run independent-day PAL association on existing local pick files."""

import argparse
from pathlib import Path

import config_pal
from association_runner import run_buffered_association


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pick_dir", type=str, default="output/eg/picks")
    parser.add_argument("--time_range", type=str, default="20171003-20171004")
    parser.add_argument("--sta_file", type=str, default="input/example_pal_format1.sta")
    parser.add_argument("--out_ctlg", type=str, default="output/eg/catalog.dat")
    parser.add_argument("--out_pha", type=str, default="output/eg/phase.dat")
    parser.add_argument("--out_assoc_root", type=str, default=None)
    parser.add_argument("--num_workers", type=int, default=1)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    assoc_root = Path(args.out_assoc_root) if args.out_assoc_root else (
        Path(args.out_ctlg).parent / "daily_assoc"
    )
    run_buffered_association(
        subnet_station_files={"full": args.sta_file},
        pick_dir=args.pick_dir,
        assoc_root=assoc_root,
        time_range=args.time_range,
        num_workers=args.num_workers,
        config_factory=config_pal.Config,
        overwrite=args.overwrite,
        association_buffer_enabled=False,
        output_catalog=args.out_ctlg,
        output_phase=args.out_pha,
    )


if __name__ == "__main__":
    main()

