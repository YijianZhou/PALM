#!/usr/bin/env python3
"""Associate existing PAL picks for one station subnet and date range."""

import os
import sys
from datetime import timedelta
from pathlib import Path

from data_pipeline_aws import to_associator_sta_dict
from phase_merge import phase_file_counts
from run_pick_aws import parse_time_range


ASSOC_PARAM_NAMES = (
    "xy_margin", "xy_grid", "z_grids", "min_sta",
    "ot_dev", "max_res", "max_drop", "vp",
)


def geometry_key(sta_dict):
    return tuple(
        sorted((net_sta, row[0], row[1], row[2]) for net_sta, row in sta_dict.items())
    )


def get_assoc_params(cfg, subnet_name=None):
    configured = getattr(cfg, "subnet_assoc_params", None)
    if configured is None:
        return {name: getattr(cfg, name) for name in ASSOC_PARAM_NAMES}

    params = dict(configured.get("default", {}))
    if subnet_name:
        params.update(configured.get(subnet_name, {}))
        params.update(configured.get(subnet_name.split("_")[-1], {}))
    missing = [name for name in ASSOC_PARAM_NAMES if name not in params]
    if missing:
        raise KeyError(
            "missing association parameters for {}: {}".format(
                subnet_name or "default", ", ".join(missing)
            )
        )
    return {name: params[name] for name in ASSOC_PARAM_NAMES}


def run_assoc(
    run_time_range, station_file, input_pick_dir,
    output_catalog, output_phase, pal_source_dir, cfg,
    subnet_name=None,
):
    sys.path.insert(0, str(Path(pal_source_dir).expanduser().resolve()))
    import associator_pal

    start, end = parse_time_range(run_time_range)
    output_catalog = Path(output_catalog)
    output_phase = Path(output_phase)
    output_catalog.parent.mkdir(parents=True, exist_ok=True)
    output_phase.parent.mkdir(parents=True, exist_ok=True)
    ctlg_partial = output_catalog.with_suffix(output_catalog.suffix + ".partial")
    pha_partial = output_phase.with_suffix(output_phase.suffix + ".partial")
    assoc_params = get_assoc_params(cfg, subnet_name)
    associators = {}
    num_input_picks = 0
    num_days_with_picks = 0

    with ctlg_partial.open("w", encoding="utf-8") as ctlg_fp, \
            pha_partial.open("w", encoding="utf-8") as pha_fp:
        current = start
        while current < end:
            active = cfg.get_sta_dict(station_file, current)
            picks = cfg.get_picks(current, input_pick_dir)
            if len(picks):
                picks = picks[[net_sta in active for net_sta in picks["net_sta"]]]
            num_input_picks += len(picks)
            if len(picks):
                num_days_with_picks += 1
            if active and len(picks):
                pal_stations = to_associator_sta_dict(active)
                key = geometry_key(pal_stations)
                associator = associators.get(key)
                if associator is None:
                    associator = associator_pal.PS_Pair_Assoc(
                        pal_stations, **assoc_params
                    )
                    associators[key] = associator
                associator.associate(picks, ctlg_fp, pha_fp)
            print("assoc {} {}: {} stations, {} picks".format(
                subnet_name or "subnet", current, len(active), len(picks),
            ))
            current += timedelta(days=1)

    os.replace(ctlg_partial, output_catalog)
    os.replace(pha_partial, output_phase)
    counts = phase_file_counts(output_phase)
    counts.update({
        "num_input_picks": num_input_picks,
        "num_days": (end - start).days,
        "num_days_with_picks": num_days_with_picks,
        "num_station_geometries": len(associators),
    })
    return counts
