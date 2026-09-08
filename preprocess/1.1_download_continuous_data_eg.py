#!/usr/bin/env python3
"""Download one preferred daily waveform combination per station."""

import csv
import json
from collections import defaultdict
from pathlib import Path

from obspy.clients.fdsn.mass_downloader import (
    GlobalDomain,
    MassDownloader,
    Restrictions,
)

from preprocess_common import (
    active_epochs,
    compact_date,
    iter_days,
    normalized_location,
    preferred_waveform_combinations,
    prune_waveform_combinations,
    read_station_epochs,
    resolve_path,
    waveform_combinations,
)


# ============================================================================
# USER SETTINGS
# ============================================================================
CASE_CODE = "eg"
STATION_FILE = Path("output/station_%s.csv" % CASE_CODE)
RAW_ROOT = Path("/data/ai_pal_%s_raw" % CASE_CODE)
TIME_RANGE = "20190704-20190707"  # Exclusive end date.
PROVIDERS = ("IRIS", "SCEDC", "NCEDC")
loc_codes = ("10", "20", "01", "02", "00", "")
chn_codes = ("HH", "BH", "EH", "HN")
NUM_WORKERS = 5  # Concurrent download threads per provider.
OVERWRITE = False


_STATIONS_PER_REQUEST = 100
_DOWNLOAD_CHUNK_SIZE_MB = 20
_REQUIRED_COMPONENTS = {"E", "N", "Z"}


def station_batches(stations):
    grouped = defaultdict(list)
    for net, sta in sorted(stations):
        grouped[net].append(sta)
    for net in sorted(grouped):
        values = sorted(set(grouped[net]))
        for offset in range(0, len(values), _STATIONS_PER_REQUEST):
            yield net, values[offset:offset + _STATIONS_PER_REQUEST]


def raw_mseed_storage(day_dir, day_code):
    """Return a MassDownloader callback preserving the established filenames."""
    def storage(network, station, location, channel, starttime, endtime):
        location_name = normalized_location(location) or "--"
        filename = "{}.{}.{}.{}__{}.mseed".format(
            network, station, location_name, channel, day_code
        )
        return str(day_dir / filename)

    return storage


def selected_complete_stations(combinations, locations, bands):
    selected = preferred_waveform_combinations(
        combinations, locations, bands
    )
    return {
        net_sta for net_sta, (_, details) in selected.items()
        if details["components"] == _REQUIRED_COMPONENTS
    }


def selected_usable_stations(combinations, locations, bands):
    selected = preferred_waveform_combinations(
        combinations, locations, bands
    )
    return {
        net_sta for net_sta, (_, details) in selected.items()
        if len(details["components"]) in (1, 3)
    }


def download_attempt(
    downloader, day, day_dir, stationxml_dir, stations, location, band
):
    errors = []
    storage = raw_mseed_storage(day_dir, compact_date(day))
    for net, station_names in station_batches(stations):
        print("  MassDownloader request: {} stations for {}.{}.{}".format(
            len(station_names), net, band, location or "--"
        ))
        restrictions = Restrictions(
            starttime=day,
            endtime=day + 86400,
            chunklength_in_sec=86400,
            network=net,
            station=",".join(station_names),
            location=location,
            channel=band + "*",
            reject_channels_with_gaps=False,
            minimum_length=0.0,
            minimum_interstation_distance_in_m=0.0,
            sanitize=False,
        )
        try:
            downloader.download(
                domain=GlobalDomain(),
                restrictions=restrictions,
                mseed_storage=storage,
                stationxml_storage=str(stationxml_dir),
                download_chunk_size_in_mb=_DOWNLOAD_CHUNK_SIZE_MB,
                threads_per_client=NUM_WORKERS,
                print_report=True,
            )
        except Exception as exc:
            message = "{}.{}.{}: {}".format(
                net, location or "--", band, exc
            )
            errors.append(message)
            print("  request failed: {}".format(message))
    return errors


def download_day(
    downloader, day, day_dir, stations, stationxml_dir, locations, bands
):
    combinations, _ = waveform_combinations(day_dir, stations)
    unresolved = set(stations) - selected_complete_stations(
        combinations, locations, bands
    )
    errors = []

    for location in locations:
        for band in bands:
            if not unresolved:
                return errors
            errors.extend(download_attempt(
                downloader, day, day_dir, stationxml_dir,
                unresolved, location, band,
            ))
            combinations, _ = waveform_combinations(day_dir, stations)
            unresolved -= selected_complete_stations(
                combinations, locations, bands
            )
    return errors


def selector_text(key):
    net, sta, location, band = key
    return "{}.{}.{}.{}".format(net, sta, band, location)


def write_report(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ("date", "selector", "status", "provider", "files", "message")
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_report(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def clear_day_downloads(day_dir):
    for pattern in ("*.mseed", "*.part"):
        for path in day_dir.glob(pattern):
            path.unlink()


def main():
    if NUM_WORKERS < 1:
        raise ValueError("NUM_WORKERS must be positive")
    if not PROVIDERS:
        raise ValueError("PROVIDERS must not be empty")
    if not loc_codes or not chn_codes:
        raise ValueError("loc_codes and chn_codes must not be empty")

    station_epochs = read_station_epochs(STATION_FILE)
    raw_root = resolve_path(RAW_ROOT)
    stationxml_dir = raw_root / "_stationxml"
    raw_root.mkdir(parents=True, exist_ok=True)
    stationxml_dir.mkdir(parents=True, exist_ok=True)
    downloader = MassDownloader(providers=list(PROVIDERS))
    all_rows = []

    for day in iter_days(TIME_RANGE):
        day_code = compact_date(day)
        day_dir = raw_root / day_code
        day_dir.mkdir(parents=True, exist_ok=True)
        done_path = day_dir / "download_complete.json"
        failed_path = day_dir / "download_incomplete.json"
        day_report = day_dir / "download_report.csv"
        epochs = active_epochs(station_epochs, day, day + 86400)
        stations = {(epoch["net"], epoch["sta"]) for epoch in epochs}
        locations = tuple(dict.fromkeys(
            tuple(loc_codes) + tuple(sorted({
                epoch["location"] for epoch in epochs
                if epoch["location"] not in loc_codes
            }))
        ))
        bands = tuple(dict.fromkeys(
            tuple(chn_codes) + tuple(sorted({
                epoch["band"] for epoch in epochs
                if epoch["band"] not in chn_codes
            }))
        ))
        if done_path.exists() and not OVERWRITE:
            existing, _ = waveform_combinations(day_dir, stations)
            complete = selected_usable_stations(
                existing, locations, bands
            )
            if stations <= complete:
                selected = preferred_waveform_combinations(
                    existing, locations, bands
                )
                prune_waveform_combinations(existing, selected)
                print("skip completed day {}".format(day_code))
                all_rows.extend(read_report(day_report))
                continue
            print("{}: completed marker failed waveform validation; retrying".format(
                day_code
            ))
        if OVERWRITE:
            clear_day_downloads(day_dir)

        before, _ = waveform_combinations(day_dir, stations)
        before_complete = selected_usable_stations(
            before, locations, bands
        )
        print("{}: downloading {} stations with location/channel fallback".format(
            day_code, len(stations)
        ))
        download_errors = download_day(
            downloader, day, day_dir, stations, stationxml_dir,
            locations, bands,
        )

        combinations, read_errors = waveform_combinations(day_dir, stations)
        selected = preferred_waveform_combinations(
            combinations, locations, bands
        )
        removed = prune_waveform_combinations(combinations, selected)
        if removed:
            print("{}: removed {} non-selected waveform files".format(
                day_code, len(removed)
            ))
        combinations, extra_read_errors = waveform_combinations(day_dir, stations)
        read_errors.extend(extra_read_errors)
        selected = preferred_waveform_combinations(
            combinations, locations, bands
        )

        rows = []
        for net_sta in sorted(stations):
            choice = selected.get(net_sta)
            if choice is None:
                selector = "{}.{}".format(*net_sta)
                status = "failed"
                files = 0
                message = "no supported waveform data returned"
            else:
                key, details = choice
                selector = selector_text(key)
                files = len(details["paths"])
                component_count = len(details["components"])
                if component_count not in (1, 3):
                    status = "failed"
                    message = "unsupported component set: {}".format(
                        ",".join(sorted(details["components"]))
                    )
                else:
                    status = (
                        "existing" if net_sta in before_complete
                        else "downloaded"
                    )
                    message = (
                        "single-component station: {}".format(
                            next(iter(details["components"]))
                        ) if component_count == 1 else ""
                    )
            rows.append({
                "date": day_code,
                "selector": selector,
                "status": status,
                "provider": "MassDownloader" if files else "",
                "files": files,
                "message": message,
            })
            print("{} {}: {}".format(day_code, selector, status))

        failed = sum(row["status"] == "failed" for row in rows)
        all_rows.extend(rows)
        write_report(day_report, rows)
        status_path = failed_path if failed else done_path
        stale_path = done_path if failed else failed_path
        status_path.write_text(json.dumps({
            "date": day_code,
            "stations": len(rows),
            "failed": failed,
            "download_errors": download_errors,
            "unreadable_files": sorted(set(read_errors)),
        }, indent=2) + "\n", encoding="utf-8")
        if stale_path.exists():
            stale_path.unlink()

    report = resolve_path(Path("output/download_%s_report.csv" % CASE_CODE))
    write_report(report, all_rows)
    print("wrote download report: {}".format(report))


if __name__ == "__main__":
    main()
