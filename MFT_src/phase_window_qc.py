"""Station-specific P templates and relocation lag checks."""
import math

import numpy as np


def validate_phase_qc(cfg):
    for name in ('adaptive_p_window', 'reject_boundary_phase_shifts'):
        if not isinstance(getattr(cfg, name, True), bool):
            raise ValueError(name + ' must be boolean')


def trim_p_template(template, norm, pre_samples, sp_samples, enabled=True):
    """Stop the half-open P template before the template S arrival."""
    if not enabled:
        return template, norm
    if not math.isfinite(sp_samples) or sp_samples <= 0:
        raise ValueError('Adaptive P window requires S arrival after P')
    stop = min(template.shape[-1], pre_samples + int(math.ceil(sp_samples)))
    if stop == template.shape[-1]:
        return template, norm
    trimmed = template[:, :stop]
    return trimmed, np.sqrt(np.sum(trimmed ** 2, axis=1))


def reliable_phase_shift(shift, search_window, rate, enabled=True):
    """Check the original lag, before event-origin correction, in sample units."""
    if not math.isfinite(shift):
        return False
    if not enabled:
        return True
    before, after = [int(rate * value) for value in search_window]
    lag = shift * rate
    # Small numerical tolerance excludes endpoints, not adjacent samples.
    return -before + 1e-6 < lag < after - 1e-6
