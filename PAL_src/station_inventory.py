"""Shared waveform-selection policy and selector-aware gain epochs."""
import csv
import math

import numpy as np
from obspy import UTCDateTime

CHANNEL_PRIORITY = ('HH', 'BH', 'EH', 'HN', 'EN', 'SH')
LOCATION_PRIORITY = ('10', '20', '01', '02', '00', '')


def location_code(value):
    value = str(value or '').strip(' _')
    return '' if value == '--' else value


def selection_rank(band, location, channel_priority=CHANNEL_PRIORITY,
                   location_priority=LOCATION_PRIORITY, order='channel_first'):
    def rank(value, priorities):
        return (0, priorities.index(value)) if value in priorities else (1, value)
    ch = rank(str(band).upper(), tuple(str(x).upper() for x in channel_priority))
    loc = rank(location_code(location), tuple(location_code(x) for x in location_priority))
    if order == 'channel_first':
        return ch, loc
    if order == 'location_first':
        return loc, ch
    raise ValueError('station_selection_order must be channel_first or location_first')


def read_inventory(path):
    """Group detailed and legacy rows by NET.STA, retaining every epoch."""
    stations = {}
    with open(path, newline='', encoding='utf-8-sig') as stream:
        for line, row in enumerate(csv.reader(stream), 1):
            if not row or not row[0].strip() or row[0].lstrip().startswith('#'):
                continue
            row = [x.strip() for x in row]
            parts = row[0].split('.')
            if len(parts) not in (2, 3, 4) or not all(parts[:2]):
                raise ValueError('{}:{}: invalid station selector'.format(path, line))
            if len(parts) >= 3 and len(parts[2]) != 2:
                raise ValueError('{}:{}: band must be a two-character prefix'.format(path, line))
            net_sta = '.'.join(parts[:2])
            coords = [float(x) for x in row[1:4]]
            if len(coords) != 3 or not all(math.isfinite(x) for x in coords):
                raise ValueError('{}:{}: invalid station coordinates'.format(path, line))
            if len(row) in (4, 5, 7):
                gains = [float(x) for x in row[4:]] or [1.0]
                blocks = [(gains * 3)[:3] + [-float('inf'), float('inf')]]
            elif len(row) >= 9 and (len(row) - 4) % 5 == 0:
                blocks = [[float(x) for x in row[i:i+3]] +
                          [float(UTCDateTime(x)) for x in row[i+3:i+5]]
                          for i in range(4, len(row), 5)]
            else:
                raise ValueError('{}:{}: expected 4/5/7 or 4+5*N columns'.format(path, line))
            info = stations.setdefault(net_sta, coords + [{'epochs': []}])
            for block in blocks:
                if not all(math.isfinite(x) and x != 0 for x in block[:3]) or block[3] >= block[4]:
                    raise ValueError('{}:{}: invalid gain or epoch'.format(path, line))
                epoch = dict(band=parts[2] if len(parts) >= 3 else None,
                             location=location_code(parts[3]) if len(parts) == 4 else None,
                             gains=block[:3], start=block[3], end=block[4], coordinates=coords)
                epochs = info[3]['epochs']
                if epoch not in epochs:
                    for other in epochs:
                        if (other['band'], other['location']) == (epoch['band'], epoch['location']) and max(other['start'], epoch['start']) < min(other['end'], epoch['end']):
                            raise ValueError('{}:{}: conflicting overlapping epochs for {}'.format(path, line, row[0]))
                    epochs.append(epoch)
    return stations


def calibrate_trace(trace, metadata, allow_fallback=False):
    """Apply exact selector/time gains before component replication or merging.

    Strict by default; local picking can allow same-band location/time fallback.
    Work by epoch spans, avoiding a full-length timestamp array per trace.
    """
    epochs = metadata['epochs']
    band, loc = trace.stats.channel[:2], location_code(trace.stats.location)
    component = {'1': 'E', '2': 'N', '3': 'Z'}.get(trace.stats.channel[-1], trace.stats.channel[-1])
    if component not in 'ENZ':
        raise ValueError('unknown gain component: {}'.format(trace.id))
    start, rate, count = float(trace.stats.starttime), float(trace.stats.sampling_rate), len(trace)
    candidates = [e for e in epochs if e['band'] in (None, band)
                  and (allow_fallback or e['location'] in (None, loc))]
    boundaries = {0, count}
    for e in candidates:
        for value in (e['start'], e['end']):
            if math.isfinite(value):
                boundaries.add(max(0, min(count, math.ceil((value - start) * rate - 1e-5))))
    if allow_fallback:
        # The nearest epoch changes at the midpoint of a metadata gap.
        ends = sorted({e['end'] for e in candidates if math.isfinite(e['end'])})
        starts = sorted({e['start'] for e in candidates if math.isfinite(e['start'])})
        for end in ends:
            following = next((s for s in starts if s > end), None)
            if following is not None:
                midpoint = (end + following) / 2
                boundaries.add(max(0, min(count, math.floor((midpoint - start) * rate) + 1)))
    spans = []
    warned = set()
    edges = sorted(boundaries)
    for left, right in zip(edges, edges[1:]):
        when = start + left / rate
        active = [e for e in candidates if e['start'] <= when + 1e-7 and when < e['end'] - 1e-7
                  and e['location'] in (None, loc)]
        if not active and not allow_fallback:
            raise ValueError('missing gain for {} at {}'.format(trace.id, UTCDateTime(when)))
        if active:
            active.sort(key=lambda e: (e['band'] is not None, e['location'] is not None), reverse=True)
            selected = active[0]
        else:
            selected = min(candidates, key=lambda e: (
                max(e['start'] - when, when - e['end'], 0.0),
                e['location'] not in (None, loc), e['band'] is None,
                e['start'], str(e['location'])), default=None)
            source = ((selected['location'], selected['start'], selected['end'])
                      if selected else None)
            if source not in warned:
                print("[WARN] gain fallback for {} at {}: {}; amplitude/magnitude unreliable".format(
                    trace.id, UTCDateTime(when),
                    "same-band location={!r}, epoch=[{}, {})".format(*source)
                    if selected else "no same-band gain; using 1 (uncalibrated counts)"), flush=True)
                warned.add(source)
            trace.stats.gain_fallback = True
        if selected is None:
            trace.stats.gain_missing = True
        gain = selected['gains']['ENZ'.index(component)] if selected else 1.0
        # Unit gain is a missing-calibration placeholder, not an SI response.
        if gain == 1.0:
            trace.stats.gain_missing = True
        spans.append((left, right, gain))
    trace.data = np.asarray(trace.data, dtype=np.float64)
    for left, right, gain in spans:
        trace.data[left:right] /= gain
    return trace
