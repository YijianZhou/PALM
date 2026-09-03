#!/usr/bin/env python3
"""Select located PAL or AI-PAL events as matched-filter templates."""

import sys
from pathlib import Path

from obspy import UTCDateTime

RUN_DIR = Path(__file__).resolve().parent
PALM_ROOT = RUN_DIR.parent
sys.path.insert(0, str(PALM_ROOT / "MFT_src"))

from workflow import resolve_run_path


def resolve_path(path):
  return resolve_run_path(RUN_DIR, path)

# ============================================================================
# USER SETTINGS
# ============================================================================
TEMPLATE_SOURCE = "ai-pal"  # "pal" or "ai-pal"
TEMPLATE_INPUTS = {
    "pal": {
        "detection": Path("input/eg_pal.pha"),
        "located": Path("input/eg_pal_hyp_full.pha"),
    },
    "ai-pal": {
        "detection": Path("input/eg_ai_pal.pha"),
        "located": Path("input/eg_ai_pal_hyp_full.pha"),
    },
}
OUTPUT_TEMPLATE_PHASE_FILE = Path("input/eg_mft.temp")
ORIGIN_TIME_RANGE = "20190704-20190707"  # Exclusive bounds.
LATITUDE_RANGE = (35.5, 36.0)
LONGITUDE_RANGE = (-117.8, -117.3)


def event_name(origin_time):
  value = str(origin_time)
  date = value.split("T")[0].replace("-", "")
  time = value.split("T")[1].replace(":", "")[:9]
  return date + time


def detection_event_names(path):
  names = {}
  with path.open(encoding="utf-8") as fp:
    for line in fp:
      fields = line.split(",")
      if len(fields[0]) < 10:
        continue
      names[str(len(names))] = event_name(UTCDateTime(fields[0]))
  return names


def selected_template_inputs():
  source = TEMPLATE_SOURCE.strip().lower().replace("_", "-")
  if source not in TEMPLATE_INPUTS:
    raise ValueError(
        "TEMPLATE_SOURCE must be one of: {}".format(
            ", ".join(sorted(TEMPLATE_INPUTS))
        )
    )
  inputs = TEMPLATE_INPUTS[source]
  return source, resolve_path(inputs["detection"]), resolve_path(inputs["located"])


def main():
  source_name, detection_path, located_path = selected_template_inputs()
  output_path = resolve_path(OUTPUT_TEMPLATE_PHASE_FILE)
  for label, path in (
      ("detection phase file", detection_path),
      ("located phase file", located_path),
  ):
    if not path.is_file():
      raise FileNotFoundError("{} for {} templates: {}".format(
          label, source_name, path
      ))
  output_path.parent.mkdir(parents=True, exist_ok=True)
  start, end = [UTCDateTime(value) for value in ORIGIN_TIME_RANGE.split("-")]
  names = detection_event_names(detection_path)

  selected = 0
  keep_event = False
  with located_path.open(encoding="utf-8") as source, output_path.open(
      "w", encoding="utf-8"
  ) as output:
    for line in source:
      fields = line.split(",")
      if len(fields[0]) >= 14:
        origin_time = UTCDateTime(fields[0])
        latitude, longitude = [float(value) for value in fields[1:3]]
        event_id = fields[-1].strip()
        keep_event = (
            start < origin_time < end
            and LATITUDE_RANGE[0] < latitude < LATITUDE_RANGE[1]
            and LONGITUDE_RANGE[0] < longitude < LONGITUDE_RANGE[1]
        )
        if keep_event:
          if event_id not in names:
            raise KeyError(
                "located event ID {} is absent from {} detections".format(
                    event_id, source_name
                )
            )
          origin, lat, lon, depth, magnitude = fields[0:5]
          output.write(
              "{}_{},{},{},{},{},{}\n".format(
                  event_id, names[event_id], origin, lat, lon, depth, magnitude
              )
          )
          selected += 1
      elif keep_event:
        output.write(line)

  print("template source: {}".format(source_name))
  print("selected {} templates -> {}".format(selected, output_path))


if __name__ == "__main__":
  main()
