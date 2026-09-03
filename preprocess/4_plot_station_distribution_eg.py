#!/usr/bin/env python3
"""Plot the geographic distribution of stations in an AI-PAL station CSV."""

import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt

from preprocess_common import read_station_epochs, resolve_path


# ============================================================================
# USER SETTINGS
# ============================================================================
CASE_CODE = "eg"
STATION_FILE = Path("output/station_%s.csv" % CASE_CODE)
OUTPUT_FIGURE = Path("output/station_distribution_%s.png" % CASE_CODE)
LONGITUDE_RANGE = (-117.8, -117.3)
LATITUDE_RANGE = (35.5, 36.0)
FIGURE_SIZE = (9, 8)

# Optional event overlay. Set to a CSV path and adjust the column names below.
CATALOG_FILE = None
CATALOG_LONGITUDE_COLUMN = "longitude"
CATALOG_LATITUDE_COLUMN = "latitude"


def unique_stations(epochs):
    stations = {}
    for epoch in epochs:
        key = epoch["net"], epoch["sta"], epoch["location"]
        stations.setdefault(key, epoch)
    return [stations[key] for key in sorted(stations)]


def read_events(path):
    if path is None:
        return []
    path = resolve_path(path)
    events = []
    with path.open(newline="", encoding="utf-8-sig") as fp:
        for row in csv.DictReader(fp):
            longitude = float(row[CATALOG_LONGITUDE_COLUMN])
            latitude = float(row[CATALOG_LATITUDE_COLUMN])
            if (
                LONGITUDE_RANGE[0] <= longitude <= LONGITUDE_RANGE[1]
                and LATITUDE_RANGE[0] <= latitude <= LATITUDE_RANGE[1]
            ):
                events.append((longitude, latitude))
    return events


def main():
    stations = unique_stations(read_station_epochs(STATION_FILE))
    stations = [
        station for station in stations
        if LONGITUDE_RANGE[0] <= station["longitude"] <= LONGITUDE_RANGE[1]
        and LATITUDE_RANGE[0] <= station["latitude"] <= LATITUDE_RANGE[1]
    ]
    if not stations:
        raise RuntimeError("no stations fall inside the configured map bounds")
    events = read_events(CATALOG_FILE)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    if events:
        ax.scatter(
            [event[0] for event in events], [event[1] for event in events],
            s=5, color="0.65", alpha=0.45, linewidths=0, label="Events",
        )
    networks = sorted({station["net"] for station in stations})
    colors = plt.get_cmap("tab10")
    for index, network in enumerate(networks):
        selected = [station for station in stations if station["net"] == network]
        ax.scatter(
            [station["longitude"] for station in selected],
            [station["latitude"] for station in selected],
            marker="^", s=55, color=colors(index % 10),
            edgecolor="black", linewidth=0.5, label=network, zorder=2,
        )
    mean_latitude = sum(LATITUDE_RANGE) / 2.0
    ax.set_aspect(1.0 / max(0.1, abs(math.cos(math.radians(mean_latitude)))))
    ax.set_xlim(*LONGITUDE_RANGE)
    ax.set_ylim(*LATITUDE_RANGE)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("AI-PAL station distribution")
    ax.grid(True, linewidth=0.4, color="0.85")
    ax.legend(loc="best", frameon=True)
    fig.tight_layout()
    output = resolve_path(OUTPUT_FIGURE)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=250)
    plt.close(fig)
    print("plotted {} stations from {} networks -> {}".format(
        len(stations), len(networks), output
    ))


if __name__ == "__main__":
    main()
