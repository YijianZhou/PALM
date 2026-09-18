"""Rolling raw-tail context for sequential daily waveform processing."""

import numpy as np
from obspy import Stream


def processing_bounds(day_start, buffer_sec):
    buffer_sec = float(buffer_sec)
    return day_start - buffer_sec, day_start + 86400 - buffer_sec


def raw_tail(stream, day_end, buffer_sec):
    """Copy the final unfiltered 2*buffer interval for the next date."""
    buffer_sec = float(buffer_sec)
    if not stream or buffer_sec <= 0:
        return Stream()
    return stream.slice(
        day_end - 2.0 * buffer_sec,
        day_end,
        nearest_sample=True,
    ).copy()


def merge_cached_tail(current, previous_tail, start_time, end_time):
    """Prepend a prior calibrated tail and return one E/N/Z stream."""
    if not current:
        return Stream()
    current_by_component = {
        str(trace.stats.channel)[-1].upper(): trace for trace in current
    }
    previous_by_component = {
        str(trace.stats.channel)[-1].upper(): trace
        for trace in previous_tail or []
    }
    output = Stream()
    for component in "ENZ":
        current_trace = current_by_component.get(component)
        if current_trace is None:
            return Stream()
        pieces = Stream()
        previous_trace = previous_by_component.get(component)
        if previous_trace is not None:
            previous_trace = previous_trace.copy()
            previous_trace.stats.network = current_trace.stats.network
            previous_trace.stats.station = current_trace.stats.station
            previous_trace.stats.location = current_trace.stats.location
            previous_trace.stats.channel = current_trace.stats.channel
            current_rate = float(current_trace.stats.sampling_rate)
            if not np.isclose(
                float(previous_trace.stats.sampling_rate), current_rate
            ):
                previous_trace.interpolate(
                    sampling_rate=current_rate, method="lanczos", a=12
                )
            pieces += previous_trace
        pieces += current_trace
        pieces.merge(method=1, fill_value=0)
        if len(pieces) != 1:
            return Stream()
        pieces[0].trim(
            start_time, end_time, nearest_sample=True, pad=False
        )
        if not len(pieces[0]):
            return Stream()
        output += pieces[0]
    return output
