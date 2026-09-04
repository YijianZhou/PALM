"""Cut quality-controlled MFT templates into portable NPY shards."""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch.multiprocessing as mp
from obspy import UTCDateTime
from torch.utils.data import DataLoader, Dataset

import config
from dataset import preprocess, read_stream, resample_stream
from template_store import (
    FORMAT_NAME,
    FORMAT_VERSION,
    INDEX_NAME,
    MANIFEST_NAME,
    RESAMPLING_METHOD,
    event_storage_name,
    read_ftemp,
)


cfg = config.Config()
num_workers = int(cfg.num_workers)
win_snr = [float(value) for value in cfg.win_snr]
win_sta_lta = [float(value) for value in cfg.win_sta_lta]
temp_win_det = [float(value) for value in cfg.temp_win_det]
temp_win_p = [float(value) for value in cfg.temp_win_p]
temp_win_s = [float(value) for value in cfg.temp_win_s]
samp_rate = float(cfg.samp_rate)
phase_samp_rate = float(cfg.phase_samp_rate)
detection_template_npts = int(round(sum(temp_win_det) * samp_rate))
phase_detection_template_npts = int(
    round(sum(temp_win_det) * phase_samp_rate)
)
p_template_npts = int(round(sum(temp_win_p) * phase_samp_rate))
s_template_npts = int(round(sum(temp_win_s) * phase_samp_rate))
win_sta_lta_npts = [
    int(round(value * phase_samp_rate)) for value in win_sta_lta
]
min_snr = float(cfg.min_snr)
preprocess_padding = float(
    getattr(cfg, "template_preprocess_padding_sec", 15.0)
)
shard_size = int(getattr(cfg, "template_shard_size", 512))
log_interval = int(getattr(cfg, "template_log_interval", 10))
get_data_dict = cfg.get_data_dict


def save_npy_atomic(path, value):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  partial = path.with_name(path.name + ".partial")
  with partial.open("wb") as fp:
    np.save(fp, value)
  os.replace(partial, path)


def write_json_atomic(path, value):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  partial = path.with_name(path.name + ".partial")
  partial.write_text(
      json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )
  os.replace(partial, path)


def calc_sta_lta(data, win_lta_npts, win_sta_npts):
  """Return the legacy energy STA/LTA statistic with finite output."""
  data = np.asarray(data, dtype=np.float64)
  npts = data.size
  if npts < win_lta_npts + win_sta_npts:
    return np.zeros(1, dtype=np.float64)
  cumulative = np.cumsum(data)
  sta = np.zeros(npts, dtype=np.float64)
  lta = np.ones(npts, dtype=np.float64)
  sta[:-win_sta_npts] = (
      cumulative[win_sta_npts:] - cumulative[:-win_sta_npts]
  ) / win_sta_npts
  lta[win_lta_npts:] = (
      cumulative[win_lta_npts:] - cumulative[:-win_lta_npts]
  ) / win_lta_npts
  with np.errstate(divide="ignore", invalid="ignore"):
    ratio = sta / lta
  ratio[:win_lta_npts] = 0.0
  ratio[~np.isfinite(ratio)] = 0.0
  return ratio


def station_day_items(template_list):
  """Group templates so each station-day waveform span is prepared once."""
  grouped = {}
  for id_name, _, pick_dict in template_list:
    event_name = event_storage_name(id_name)
    for net_sta, (tp, ts) in pick_dict.items():
      key = (net_sta, str(tp.date))
      grouped.setdefault(key, []).append((event_name, tp, ts))
  return sorted(grouped.items())


def station_paths(data_dir, net_sta, start_time, end_time):
  paths = []
  date = UTCDateTime(str(start_time.date))
  while date < end_time:
    paths.extend(get_data_dict(date, data_dir).get(net_sta, []))
    date += 86400
  return list(dict.fromkeys(paths))


def template_array(stream, center, window, sample_rate, template_npts):
  start_time = center - window[0]
  end_time = start_time + (template_npts - 1) / sample_rate
  selected = stream.slice(start_time, end_time, nearest_sample=True)
  if len(selected) != 3 or any(len(trace) != template_npts for trace in selected):
    return None
  for trace in selected:
    data = np.asarray(trace.data)
    if not np.all(np.isfinite(data)) or not np.any(data):
      return None
  return np.stack(
      [np.asarray(trace.data, dtype=np.float32) for trace in selected],
      axis=0,
  )


def passes_snr(stream, tp):
  if not min_snr:
    return True
  snr_stream = stream.slice(
      tp - win_sta_lta[0] - win_snr[0],
      tp + win_sta_lta[1] + win_snr[1],
  )
  if len(snr_stream) != 3:
    return False
  energy = np.asarray(snr_stream[2].data, dtype=np.float64) ** 2
  snr = calc_sta_lta(energy, win_sta_lta_npts[0], win_sta_lta_npts[1])
  return np.max(snr) >= min_snr


def safe_group_name(index, key, samples):
  net_sta, date = key
  fingerprint = {
      "station": net_sta,
      "date": date,
      "samples": [
          [event_name, str(tp), str(ts)] for event_name, tp, ts in samples
      ],
      "detection_sample_rate": samp_rate,
      "phase_sample_rate": phase_samp_rate,
      "frequency_band_hz": [float(value) for value in cfg.freq_band],
      "resampling_method": RESAMPLING_METHOD,
      "detection_window": temp_win_det,
      "p_window": temp_win_p,
      "s_window": temp_win_s,
      "padding": preprocess_padding,
      "frequency_band": list(cfg.freq_band),
      "minimum_snr": min_snr,
  }
  digest = hashlib.sha256(
      json.dumps(fingerprint, sort_keys=True).encode("utf-8")
  ).hexdigest()[:12]
  station = net_sta.replace(".", "_").replace("/", "_")
  return "{:07d}_{}_{}_{}".format(index, station, date, digest)


def read_completed_group(path, output_root):
  if not path.is_file():
    return None
  rows = np.load(path, allow_pickle=False)
  if rows.size == 0:
    return []
  if rows.ndim != 2 or rows.shape[1] != 7:
    return None
  shard_paths = set(rows[:, 2:6].reshape(-1))
  if not all((output_root / shard).is_file() for shard in shard_paths):
    return None
  return rows.tolist()


def write_group(output_root, group_name, records):
  rows = []
  for shard_index, start in enumerate(range(0, len(records), shard_size)):
    chunk = records[start:start + shard_size]
    detection_data = np.stack([record[2] for record in chunk]).astype(
        np.float32, copy=False
    )
    phase_detection_data = np.stack([record[3] for record in chunk]).astype(
        np.float32, copy=False
    )
    p_data = np.stack([record[4] for record in chunk]).astype(
        np.float32, copy=False
    )
    s_data = np.stack([record[5] for record in chunk]).astype(
        np.float32, copy=False
    )
    detection_path = Path("detection_shards") / (
        "{}_shard_{:06d}.npy".format(group_name, shard_index)
    )
    phase_detection_path = Path("phase_detection_shards") / (
        "{}_shard_{:06d}.npy".format(group_name, shard_index)
    )
    p_path = Path("p_shards") / (
        "{}_shard_{:06d}.npy".format(group_name, shard_index)
    )
    s_path = Path("s_shards") / (
        "{}_shard_{:06d}.npy".format(group_name, shard_index)
    )
    save_npy_atomic(output_root / detection_path, detection_data)
    save_npy_atomic(
        output_root / phase_detection_path, phase_detection_data
    )
    save_npy_atomic(output_root / p_path, p_data)
    save_npy_atomic(output_root / s_path, s_data)
    for row_index, record in enumerate(chunk):
      event_name, net_sta = record[:2]
      rows.append((
          event_name, net_sta, detection_path.as_posix(),
          phase_detection_path.as_posix(), p_path.as_posix(),
          s_path.as_posix(), str(row_index)
      ))
  return rows


class TemplateCutter(Dataset):
  def __init__(self, items, data_dir, output_root, overwrite=False):
    self.items = items
    self.data_dir = data_dir
    self.output_root = Path(output_root)
    self.overwrite = overwrite

  def __getitem__(self, index):
    key, samples = self.items[index]
    net_sta, _ = key
    group_name = safe_group_name(index, key, samples)
    group_index = self.output_root / ".groups" / (group_name + ".npy")
    if not self.overwrite:
      completed = read_completed_group(group_index, self.output_root)
      if completed is not None:
        return completed

    first_start = min(
        min(
            tp - temp_win_det[0], tp - temp_win_p[0],
            ts - temp_win_s[0],
            tp - win_sta_lta[0] - win_snr[0],
        )
        for _, tp, ts in samples
    )
    last_end = max(
        max(
            tp + temp_win_det[1], tp + temp_win_p[1],
            ts + temp_win_s[1],
            tp + win_sta_lta[1] + win_snr[1],
        )
        for _, tp, ts in samples
    )
    read_start = first_start - preprocess_padding
    read_end = last_end + preprocess_padding
    paths = station_paths(self.data_dir, net_sta, read_start, read_end)
    records = []
    if paths:
      stream = read_stream(
          paths, None, start_time=read_start, end_time=read_end
      )
      if len(stream) == 3:
        phase_stream = preprocess(stream, phase_samp_rate)
        if len(phase_stream) == 3:
          detection_stream = resample_stream(phase_stream, samp_rate)
          for event_name, tp, ts in samples:
            if tp > ts:
              continue
            if not passes_snr(phase_stream, tp):
              continue
            detection_data = template_array(
                detection_stream, tp, temp_win_det, samp_rate,
                detection_template_npts,
            )
            phase_detection_data = template_array(
                phase_stream, tp, temp_win_det, phase_samp_rate,
                phase_detection_template_npts,
            )
            p_data = template_array(
                phase_stream, tp, temp_win_p, phase_samp_rate,
                p_template_npts,
            )
            s_data = template_array(
                phase_stream, ts, temp_win_s, phase_samp_rate,
                s_template_npts,
            )
            arrays = (detection_data, phase_detection_data, p_data, s_data)
            if all(data is not None for data in arrays):
              records.append((
                  event_name, net_sta, *arrays
              ))
    rows = write_group(self.output_root, group_name, records)
    save_npy_atomic(group_index, np.asarray(rows, dtype=str).reshape(-1, 7))
    return rows

  def __len__(self):
    return len(self.items)


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--data_dir", required=True)
  parser.add_argument("--temp_pha", required=True)
  parser.add_argument("--out_root", required=True)
  parser.add_argument("--overwrite", action="store_true")
  args = parser.parse_args()

  if preprocess_padding < 0:
    raise ValueError("template_preprocess_padding_sec must be nonnegative")
  if phase_samp_rate < samp_rate:
    raise ValueError("phase_samp_rate must be at least samp_rate")
  if shard_size < 1:
    raise ValueError("template_shard_size must be at least 1")
  if log_interval < 1:
    raise ValueError("template_log_interval must be at least 1")
  output_root = Path(args.out_root).expanduser().resolve()
  output_root.mkdir(parents=True, exist_ok=True)
  print(
      "template rates: {} Hz detection, {} Hz phase".format(
          samp_rate, phase_samp_rate
      ),
      flush=True,
  )
  items = station_day_items(read_ftemp(args.temp_pha))
  dataset = TemplateCutter(
      items, args.data_dir, output_root, overwrite=args.overwrite
  )
  loader = DataLoader(dataset, num_workers=num_workers, batch_size=None)
  rows = []
  for index, group_rows in enumerate(loader, start=1):
    rows.extend(group_rows)
    if index % log_interval == 0 or index == len(dataset):
      print(
          "{}/{} station-day groups; {} templates accepted".format(
              index, len(dataset), len(rows)
          ),
          flush=True,
      )
  rows.sort(key=lambda row: (row[0], row[1]))
  keys = [(row[0], row[1]) for row in rows]
  if len(keys) != len(set(keys)):
    raise ValueError("duplicate event/station entries in template index")
  save_npy_atomic(
      output_root / INDEX_NAME,
      np.asarray(rows, dtype=str).reshape(-1, 7),
  )
  shard_pair_count = len(set(row[2] for row in rows))
  write_json_atomic(output_root / MANIFEST_NAME, {
      "format": FORMAT_NAME,
      "format_version": FORMAT_VERSION,
      "created_at": datetime.now(timezone.utc).isoformat(),
      "detection_sample_rate": samp_rate,
      "phase_sample_rate": phase_samp_rate,
      "frequency_band_hz": [float(value) for value in cfg.freq_band],
      "resampling_method": RESAMPLING_METHOD,
      "detection_window_sec": temp_win_det,
      "p_window_sec": temp_win_p,
      "s_window_sec": temp_win_s,
      "detection_sample_npts": detection_template_npts,
      "phase_detection_sample_npts": phase_detection_template_npts,
      "p_sample_npts": p_template_npts,
      "s_sample_npts": s_template_npts,
      "template_count": len(rows),
      "shard_set_count": shard_pair_count,
      "shard_size": shard_size,
      "source_phase_file": str(Path(args.temp_pha).expanduser().resolve()),
  })
  print("template store: {}".format(output_root))
  print(
      "{} templates in {} synchronized compact shard sets".format(
          len(rows), shard_pair_count
      )
  )


if __name__ == "__main__":
  mp.set_start_method("spawn", force=True)
  main()
