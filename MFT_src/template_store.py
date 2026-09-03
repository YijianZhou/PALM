"""Portable NPY-shard storage and dataset access for MFT templates."""

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from obspy import UTCDateTime
from torch.utils.data import Dataset


FORMAT_NAME = "palm-mft-template-npy"
FORMAT_VERSION = 3
INDEX_NAME = "template_index.npy"
MANIFEST_NAME = "template_manifest.json"


def read_ftemp(path):
  """Read the source-neutral MFT template phase file."""
  templates = []
  with open(path, encoding="utf-8") as fp:
    for line_number, line in enumerate(fp, start=1):
      fields = [value.strip() for value in line.split(",")]
      if not fields or not fields[0] or fields[0].startswith("#"):
        continue
      if len(fields[0]) >= 14:
        if len(fields) < 6:
          raise ValueError(
              "{}:{} incomplete template event row".format(path, line_number)
          )
        event_location = [
            UTCDateTime(fields[1]),
            *[float(value) for value in fields[2:6]],
        ]
        templates.append([fields[0], event_location, {}])
        continue
      if not templates or len(fields) < 3:
        raise ValueError(
            "{}:{} phase row has no preceding event or is incomplete".format(
                path, line_number
            )
        )
      templates[-1][2][fields[0]] = [
          UTCDateTime(fields[1]), UTCDateTime(fields[2])
      ]
  return templates


def event_storage_name(id_name):
  fields = str(id_name).split("_", 1)
  if len(fields) != 2 or not fields[1]:
    raise ValueError(
        "template event name must contain an underscore: {}".format(id_name)
    )
  return fields[1]


@lru_cache(maxsize=64)
def _open_shard(path):
  return np.load(path, mmap_mode="r", allow_pickle=False)


class TemplateStore:
  def __init__(
      self, root, detection_rate, phase_rate, detection_window,
      p_window, s_window,
  ):
    self.root = Path(root).expanduser().resolve()
    manifest_path = self.root / MANIFEST_NAME
    index_path = self.root / INDEX_NAME
    if not manifest_path.is_file() or not index_path.is_file():
      raise FileNotFoundError(
          "NPY template store is incomplete at {}; run 2_cut_templates first".format(
              self.root
          )
      )
    self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        self.manifest.get("format") != FORMAT_NAME
        or int(self.manifest.get("format_version", -1)) != FORMAT_VERSION
    ):
      raise ValueError(
          "unsupported MFT template store format; recut templates with the "
          "current compact dual-rate 2_cut_templates workflow"
      )
    stored_detection_rate = float(self.manifest["detection_sample_rate"])
    stored_phase_rate = float(self.manifest["phase_sample_rate"])
    if not np.isclose(stored_detection_rate, float(detection_rate)):
      raise ValueError(
          "template detection rate {} does not match config {}".format(
              stored_detection_rate, detection_rate
          )
      )
    if not np.isclose(stored_phase_rate, float(phase_rate)):
      raise ValueError(
          "template phase rate {} does not match config {}".format(
              stored_phase_rate, phase_rate
          )
      )
    configured_windows = {
        "detection_window_sec": detection_window,
        "p_window_sec": p_window,
        "s_window_sec": s_window,
    }
    for field, configured in configured_windows.items():
      stored = [float(value) for value in self.manifest[field]]
      if len(stored) != 2 or not np.allclose(stored, configured):
        raise ValueError(
            "template {} {} does not match config {}".format(
                field, stored, list(configured)
            )
        )
    self.detection_rate = stored_detection_rate
    self.phase_rate = stored_phase_rate
    self.detection_npts = int(self.manifest["detection_sample_npts"])
    self.phase_detection_npts = int(
        self.manifest["phase_detection_sample_npts"]
    )
    self.p_npts = int(self.manifest["p_sample_npts"])
    self.s_npts = int(self.manifest["s_sample_npts"])
    rows = np.load(index_path, allow_pickle=False)
    if rows.size == 0:
      rows = np.empty((0, 7), dtype=str)
    if rows.ndim != 2 or rows.shape[1] != 7:
      raise ValueError("{} must have seven string columns".format(index_path))
    self.entries = {}
    for row in rows:
      (event_name, net_sta, detection_path, phase_detection_path,
       p_path, s_path, row_index) = row
      key = (str(event_name), str(net_sta))
      if key in self.entries:
        raise ValueError("duplicate template index entry: {}".format(key))
      self.entries[key] = (
          self.root / str(detection_path),
          self.root / str(phase_detection_path),
          self.root / str(p_path),
          self.root / str(s_path),
          int(row_index),
      )

  def read(self, event_name, net_sta):
    entry = self.entries.get((str(event_name), str(net_sta)))
    if entry is None:
      return None
    detection_path, phase_detection_path, p_path, s_path, row_index = entry
    detection_shard = _open_shard(str(detection_path))
    phase_detection_shard = _open_shard(str(phase_detection_path))
    p_shard = _open_shard(str(p_path))
    s_shard = _open_shard(str(s_path))
    for shard_path, shard in (
        (detection_path, detection_shard),
        (phase_detection_path, phase_detection_shard),
        (p_path, p_shard),
        (s_path, s_shard),
    ):
      if shard.ndim != 3 or shard.shape[1] != 3:
        raise ValueError("invalid template shard shape at {}".format(shard_path))
      if not 0 <= row_index < shard.shape[0]:
        raise IndexError("template row outside shard at {}".format(shard_path))
    if detection_shard.shape[2] != self.detection_npts:
      raise ValueError(
          "detection template length does not match manifest at {}".format(
              detection_path
          )
      )
    expected_lengths = (
        (phase_detection_path, phase_detection_shard,
         self.phase_detection_npts),
        (p_path, p_shard, self.p_npts),
        (s_path, s_shard, self.s_npts),
    )
    for shard_path, shard, expected_npts in expected_lengths:
      if shard.shape[2] != expected_npts:
        raise ValueError(
            "template length does not match manifest at {}".format(shard_path)
        )
    return tuple(
        np.asarray(shard[row_index], dtype=np.float32).copy()
        for shard in (
            detection_shard, phase_detection_shard, p_shard, s_shard
        )
    )


class TemplateDataset(Dataset):
  """Build CPU template tensors from an indexed NPY shard store."""

  def __init__(
      self, template_list, template_root, max_stations, detection_rate,
      phase_rate, detection_window, p_window, s_window,
  ):
    self.template_list = template_list
    self.max_stations = int(max_stations)
    self.detection_rate = float(detection_rate)
    self.phase_rate = float(phase_rate)
    self.windows = [
        [float(value) for value in detection_window],
        [float(value) for value in p_window],
        [float(value) for value in s_window],
    ]
    self.store = TemplateStore(
        template_root, self.detection_rate, self.phase_rate,
        *self.windows,
    )

  def __getitem__(self, index):
    temp_name, temp_location, phase_picks = self.template_list[index]
    event_name = event_storage_name(temp_name)
    origin_time = temp_location[0]
    station_list = [
        net_sta
        for net_sta, _ in sorted(
            phase_picks.items(), key=lambda item: item[1][0]
        )[:self.max_stations]
    ]
    template_picks = {}
    for net_sta in station_list:
      tp, ts = phase_picks[net_sta]
      stored = self.store.read(event_name, net_sta)
      if stored is None:
        continue
      detection_data, phase_detection_data, p_data, s_data = stored
      # Preserve the legacy detection/P/S positions and append the high-rate
      # detection window used only by CPU phase verification.
      templates = [detection_data, p_data, s_data, phase_detection_data]
      if any(not np.any(template) for template in templates):
        continue
      norms = [
          np.sqrt(np.sum(template ** 2, axis=1)).astype(np.float32)
          for template in templates
      ]
      # Detection offsets are in detection samples; P/S offsets are in phase
      # samples. Each consumer uses the corresponding waveform rate.
      offsets = [
          int(round(
              (origin_time - tp + self.windows[0][0]) * self.detection_rate
          )),
          int(round((tp - origin_time) * self.phase_rate)),
          int(round((ts - origin_time) * self.phase_rate)),
      ]
      template_picks[net_sta] = [templates, norms, offsets]
    return temp_name, temp_location, template_picks

  def __len__(self):
    return len(self.template_list)
