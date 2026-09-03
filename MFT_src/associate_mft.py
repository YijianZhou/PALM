"""Associate duplicate MFT template detections and write final products."""

import argparse
import csv
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from obspy import UTCDateTime

import config
from template_store import read_ftemp


@dataclass
class Pick:
  net_sta: str
  tp: UTCDateTime
  ts: UTCDateTime
  dt_p: float
  dt_s: float
  s_amp: float
  cc_p: float
  cc_s: float
  cc_det_phase: float = float("nan")


@dataclass
class Detection:
  template_id: int
  template_name: str
  origin_time: UTCDateTime
  location: tuple
  detection_cc: float
  picks: dict = field(default_factory=dict)


@dataclass
class AssociatedEvent:
  event_id: int
  origin_time: UTCDateTime
  location: tuple
  magnitude: float
  representative: Detection
  neighbors: list
  detection_count: int
  is_self_detection: bool


def atomic_text_path(path):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  return path, path.with_name(path.name + ".partial")


def template_metadata(path):
  metadata = {}
  for template_name, location, _ in read_ftemp(path):
    template_id_text = str(template_name).split("_", 1)[0]
    try:
      template_id = int(template_id_text)
    except ValueError as exc:
      raise ValueError(
          "hypoDD requires numeric template IDs; found {}".format(
              template_id_text
          )
      ) from exc
    if template_id in metadata:
      raise ValueError("duplicate template ID {}".format(template_id))
    metadata[template_id] = {
        "name": str(template_name),
        "origin_time": location[0],
        "location": tuple(float(value) for value in location[1:4]),
        "magnitude": float(location[4]),
    }
  return metadata


def parse_pick(fields, path, line_number):
  if len(fields) < 8:
    raise ValueError("{}:{} incomplete MFT pick row".format(path, line_number))
  cc_det_phase = float(fields[8]) if len(fields) > 8 else float("nan")
  return Pick(
      net_sta=fields[0],
      tp=UTCDateTime(fields[1]),
      ts=UTCDateTime(fields[2]),
      dt_p=float(fields[3]),
      dt_s=float(fields[4]),
      s_amp=float(fields[5]),
      cc_p=float(fields[6]),
      cc_s=float(fields[7]),
      cc_det_phase=cc_det_phase,
  )


def read_detections(paths, start_time, end_time):
  detections = []
  for path in paths:
    path = Path(path)
    current = None
    keep = False
    with path.open(encoding="utf-8") as source:
      for line_number, line in enumerate(source, start=1):
        fields = [value.strip() for value in line.split(",")]
        if not fields or not fields[0]:
          continue
        if len(fields) == 6:
          template_name = fields[0]
          try:
            template_id = int(template_name.split("_", 1)[0])
          except ValueError as exc:
            raise ValueError(
                "{}:{} nonnumeric template ID".format(path, line_number)
            ) from exc
          origin_time = UTCDateTime(fields[1])
          keep = start_time <= origin_time < end_time
          current = Detection(
              template_id=template_id,
              template_name=template_name,
              origin_time=origin_time,
              location=tuple(float(value) for value in fields[2:5]),
              detection_cc=float(fields[5]),
          )
          if keep:
            detections.append(current)
          continue
        if len(fields) < 8:
          raise ValueError(
              "{}:{} unrecognized MFT phase row".format(path, line_number)
          )
        if current is None:
          raise ValueError(
              "{}:{} pick row has no preceding detection".format(
                  path, line_number
              )
          )
        if keep:
          pick = parse_pick(fields, path, line_number)
          current.picks[pick.net_sta] = pick
  return detections


def event_magnitude(detection, station_dict, fallback):
  event_lat, event_lon, event_depth = detection.location
  magnitudes = []
  for net_sta, pick in detection.picks.items():
    if net_sta not in station_dict or not np.isfinite(pick.s_amp):
      continue
    amplitude = pick.s_amp * 1e6
    if amplitude <= 0:
      continue
    station_lat, station_lon, station_elevation = station_dict[net_sta][:3]
    cosine_latitude = np.cos(station_lat * np.pi / 180.0)
    distance_latitude = 111.0 * (station_lat - event_lat)
    distance_longitude = 111.0 * (station_lon - event_lon) * cosine_latitude
    distance_depth = event_depth + station_elevation / 1e3
    distance = np.sqrt(
        distance_longitude ** 2
        + distance_latitude ** 2
        + distance_depth ** 2
    )
    if distance > 0:
      magnitudes.append(np.log10(amplitude) + np.log10(distance) + 1.0)
  if not magnitudes:
    return float(fallback)
  magnitudes = np.asarray(magnitudes, dtype=float)
  if len(magnitudes) >= 3:
    deviation = np.abs(magnitudes - np.median(magnitudes))
    magnitudes = np.delete(magnitudes, np.argmax(deviation))
  return float(np.median(magnitudes))


def choose_event_id(next_id, reserved_ids, used_ids):
  while next_id in reserved_ids or next_id in used_ids:
    next_id += 1
  return next_id, next_id + 1


def associate_detections(detections, templates, station_dict, cfg):
  detections = [
      detection for detection in detections
      if detection.template_id in templates
      and detection.detection_cc >= cfg.association_detection_cc_min
  ]
  detections.sort(key=lambda detection: detection.origin_time.timestamp)
  associated = []
  reserved_ids = set(templates)
  used_ids = set()
  next_id = int(cfg.association_start_event_id)
  cursor = 0
  while cursor < len(detections):
    anchor_time = detections[cursor].origin_time
    stop = cursor + 1
    while (
        stop < len(detections)
        and detections[stop].origin_time - anchor_time
        < cfg.association_origin_time_tolerance_sec
    ):
      stop += 1
    cluster = detections[cursor:stop]
    self_candidates = [
        detection for detection in cluster
        if abs(
            templates[detection.template_id]["origin_time"]
            - detection.origin_time
        ) <= cfg.association_origin_time_tolerance_sec
    ]
    if self_candidates:
      representative = max(
          self_candidates,
          key=lambda detection: (
              len(detection.picks), detection.detection_cc,
              -detection.template_id,
          ),
      )
      event_id = representative.template_id
      self_candidate_ids = {id(detection) for detection in self_candidates}
      neighbors = [
          detection for detection in cluster
          if id(detection) not in self_candidate_ids
      ]
      is_self = True
    else:
      representative = max(
          cluster,
          key=lambda detection: (
              detection.detection_cc, len(detection.picks),
              -detection.template_id,
          ),
      )
      event_id, next_id = choose_event_id(
          next_id, reserved_ids, used_ids
      )
      neighbors = list(cluster)
      is_self = False
    neighbors.sort(
        key=lambda detection: detection.detection_cc, reverse=True
    )
    neighbors = neighbors[:cfg.association_max_neighbor_templates]
    if len(neighbors) >= cfg.association_min_neighbor_templates or is_self:
      if is_self:
        location = templates[representative.template_id]["location"]
      else:
        location = tuple(np.mean([
            templates[detection.template_id]["location"]
            for detection in neighbors
        ], axis=0))
      fallback_magnitude = templates[representative.template_id]["magnitude"]
      magnitude = event_magnitude(
          representative, station_dict, fallback_magnitude
      )
      associated.append(AssociatedEvent(
          event_id=event_id,
          origin_time=representative.origin_time,
          location=location,
          magnitude=magnitude,
          representative=representative,
          neighbors=neighbors,
          detection_count=len(cluster),
          is_self_detection=is_self,
      ))
      used_ids.add(event_id)
    cursor = stop
  return associated


def differential_observations(event, cfg):
  pairs = []
  for detection in event.neighbors:
    origin_correction = detection.origin_time - event.origin_time
    observations = []
    for pick in detection.picks.values():
      station = pick.net_sta.split(".", 1)[-1]
      dt_p = pick.dt_p + origin_correction
      dt_s = pick.dt_s + origin_correction
      if (
          abs(dt_p) <= cfg.association_max_phase_shift_sec[0]
          and pick.cc_p >= cfg.association_phase_cc_min
      ):
        observations.append((station, dt_p, math.sqrt(pick.cc_p), "P"))
      if (
          abs(dt_s) <= cfg.association_max_phase_shift_sec[1]
          and pick.cc_s >= cfg.association_phase_cc_min
      ):
        observations.append((station, dt_s, math.sqrt(pick.cc_s), "S"))
    if observations:
      pairs.append((event.event_id, detection.template_id, observations))
  return pairs


def unique_differential_pairs(events, cfg):
  selected = {}
  for event in events:
    for event_id, template_id, observations in differential_observations(
        event, cfg
    ):
      key = tuple(sorted((event_id, template_id)))
      previous = selected.get(key)
      if previous is None or len(observations) >= len(previous[2]):
        selected[key] = (event_id, template_id, observations)
  output = []
  for pair in selected.values():
    stations = {observation[0] for observation in pair[2]}
    if len(stations) >= cfg.min_sta:
      output.append(pair)
  return sorted(output, key=lambda pair: (pair[0], pair[1]))


def write_catalog(events, path):
  final_path, partial_path = atomic_text_path(path)
  with partial_path.open("w", newline="", encoding="utf-8") as output:
    writer = csv.writer(output)
    writer.writerow([
        "origin_time", "latitude", "longitude", "depth_km", "magnitude",
        "event_id", "template_count", "best_detection_cc",
        "self_detection",
    ])
    for event in events:
      writer.writerow([
          str(event.origin_time), *event.location, event.magnitude,
          event.event_id, event.detection_count,
          event.representative.detection_cc, int(event.is_self_detection),
      ])
  os.replace(partial_path, final_path)


def write_phase(events, path):
  final_path, partial_path = atomic_text_path(path)
  with partial_path.open("w", encoding="utf-8") as output:
    for event in events:
      output.write("{},{},{},{},{},{},{},{:.3f}\n".format(
          event.origin_time, *event.location, event.magnitude, event.event_id,
          event.detection_count, event.representative.detection_cc,
      ))
      for pick in sorted(
          event.representative.picks.values(), key=lambda item: item.net_sta
      ):
        output.write(
            "{},{},{},{:.2f},{:.2f},{},{:.3f},{:.3f},{:.3f}\n".format(
                pick.net_sta, pick.tp, pick.ts, pick.dt_p, pick.dt_s,
                pick.s_amp, pick.cc_p, pick.cc_s, pick.cc_det_phase,
            )
        )
  os.replace(partial_path, final_path)


def write_event_dat(events, path, depth_offset):
  final_path, partial_path = atomic_text_path(path)
  with partial_path.open("w", encoding="utf-8") as output:
    for event in events:
      origin = event.origin_time
      latitude, longitude, depth = event.location
      date = "{:04d}{:02d}{:02d}".format(
          origin.year, origin.month, origin.day
      )
      time = "{:02d}{:02d}{:02d}{:02d}".format(
          origin.hour, origin.minute, origin.second,
          int(origin.microsecond / 1e4),
      )
      location = "{:7.4f}   {:8.4f}   {:8.3f}  {:4.2f}".format(
          latitude, longitude, depth + depth_offset, event.magnitude
      )
      output.write(
          "{}  {}   {}   0.00    0.00   0.0 {:>10}\n".format(
              date, time, location, event.event_id
          )
      )
  os.replace(partial_path, final_path)


def write_dt_cc(events, path, cfg):
  pairs = unique_differential_pairs(events, cfg)
  final_path, partial_path = atomic_text_path(path)
  with partial_path.open("w", encoding="utf-8") as output:
    for event_id, template_id, observations in pairs:
      output.write("# {:9d} {:9d} 0.0\n".format(event_id, template_id))
      for station, differential_time, weight, phase in observations:
        output.write("{:7} {:8.5f} {:.4f} {}\n".format(
            station, differential_time, weight, phase
        ))
  os.replace(partial_path, final_path)
  return len(pairs)


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--det_pha", nargs="+", required=True)
  parser.add_argument("--temp_pha", required=True)
  parser.add_argument("--sta_file", required=True)
  parser.add_argument("--time_range", required=True)
  parser.add_argument("--out_catalog", required=True)
  parser.add_argument("--out_phase", required=True)
  parser.add_argument("--out_event", required=True)
  parser.add_argument("--out_dt", required=True)
  args = parser.parse_args()

  cfg = config.Config()
  if cfg.association_origin_time_tolerance_sec <= 0:
    raise ValueError("association origin-time tolerance must be positive")
  if not 0 <= cfg.association_detection_cc_min <= 1:
    raise ValueError("association detection CC threshold must be in [0, 1]")
  if not 0 <= cfg.association_phase_cc_min <= 1:
    raise ValueError("association phase CC threshold must be in [0, 1]")
  if len(cfg.association_max_phase_shift_sec) != 2 or any(
      value <= 0 for value in cfg.association_max_phase_shift_sec
  ):
    raise ValueError("association P/S shift limits must be two positive values")
  if not (
      1 <= cfg.association_min_neighbor_templates
      <= cfg.association_max_neighbor_templates
  ):
    raise ValueError("association neighbor limits are inconsistent")
  if cfg.min_sta < 1:
    raise ValueError("min_sta must be positive")
  start_time, end_time = [
      UTCDateTime(value) for value in args.time_range.split("-")
  ]
  templates = template_metadata(args.temp_pha)
  station_dict = cfg.get_sta_dict(args.sta_file)
  detections = read_detections(args.det_pha, start_time, end_time)
  events = associate_detections(detections, templates, station_dict, cfg)
  write_catalog(events, args.out_catalog)
  write_phase(events, args.out_phase)
  write_event_dat(events, args.out_event, cfg.hypodd_depth_offset_km)
  pair_count = write_dt_cc(events, args.out_dt, cfg)
  print("raw MFT detections: {:,}".format(len(detections)))
  print("associated events:  {:,}".format(len(events)))
  print("unique dt.cc pairs:  {:,}".format(pair_count))
  print("catalog: {}".format(args.out_catalog))
  print("phase:   {}".format(args.out_phase))
  print("event:   {}".format(args.out_event))
  print("dt:      {}".format(args.out_dt))


if __name__ == "__main__":
  main()
