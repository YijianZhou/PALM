"""Deterministic, bounded station-aware grouping of loaded MFT templates."""
from numbers import Integral
import numpy as np


def validate_group_count(value):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
        raise ValueError('template_num_groups must be a positive integer')
    return int(value)


def partition_templates(templates, num_groups=1):
    """Return index lists, preserving every loaded template and its station set.

    Farthest-first geographic seeds use at most 50 Lloyd passes, followed by
    at most three station-union refinement passes. These are bounded algorithm
    internals, not detection settings. Group count is the user control.
    """
    requested = validate_group_count(num_groups)
    n = len(templates)
    if not n:
        return []
    k = min(requested, n)
    if k == 1:
        return [list(range(n))]
    xy = np.array([[float(t[1][2]), float(t[1][1])] for t in templates])
    if not np.isfinite(xy).all():
        raise ValueError('Template grouping requires finite event coordinates')
    xy[:, 0] *= np.cos(np.radians(xy[:, 1].mean()))
    first = int(np.argmin(((xy-xy.mean(axis=0))**2).sum(axis=1)))
    seeds = [first]
    distance = ((xy-xy[first])**2).sum(axis=1)
    for _ in range(k-1):
        distance[seeds] = -1
        new = int(np.argmax(distance))
        seeds.append(new)
        distance = np.minimum(distance, ((xy-xy[new])**2).sum(axis=1))
    centers = xy[seeds].copy()
    previous = None
    for _ in range(50):
        costs = ((xy[:, None]-centers[None, :])**2).sum(axis=2)
        labels = costs.argmin(axis=1)
        sizes = np.bincount(labels, minlength=k)
        for g in np.flatnonzero(sizes == 0):
            # Co-located templates still need deterministic nonempty groups.
            candidates = np.flatnonzero(sizes[labels] > 1)
            chosen = int(candidates[np.argmax(costs[candidates, labels[candidates]])])
            sizes[labels[chosen]] -= 1
            labels[chosen] = g
            sizes[g] += 1
        if previous is not None and np.array_equal(previous, labels):
            break
        centers = np.array([xy[labels == g].mean(axis=0) for g in range(k)])
        previous = labels.copy()
    station_ids = {s: i for i, s in enumerate(sorted({s for t in templates for s in t[2]}))}
    memberships = [np.array([station_ids[s] for s in t[2]], dtype=int) for t in templates]
    counts = np.zeros((k, len(station_ids)), dtype=int)
    for g, stations in zip(labels, memberships):
        counts[g, stations] += 1
    sizes = np.count_nonzero(counts, axis=1)
    cap = int(sizes.max())
    population = np.bincount(labels, minlength=k)
    for _ in range(3):
        moves = 0
        for i, stations in enumerate(memberships):
            source = labels[i]
            if population[source] == 1:
                continue
            removed = np.count_nonzero(counts[source, stations] == 1)
            best = source
            score = (int(sizes.sum()), int(sizes.max()))
            for target in range(k):
                if target == source:
                    continue
                proposed = sizes.copy()
                proposed[source] -= removed
                proposed[target] += np.count_nonzero(counts[target, stations] == 0)
                candidate = (int(proposed.sum()), int(proposed.max()))
                if proposed[target] <= cap and candidate < score:
                    best, best_sizes, score = target, proposed, candidate
            if best != source:
                counts[source, stations] -= 1
                counts[best, stations] += 1
                population[source] -= 1
                population[best] += 1
                labels[i], sizes = best, best_sizes
                moves += 1
        if not moves:
            break
    order = sorted(range(k), key=lambda g: (-xy[labels == g, 1].mean(), g))
    return [np.flatnonzero(labels == g).tolist() for g in order]


def group_station_dicts(templates, groups, stations):
    return [{s: stations[s] for s in sorted({s for i in group for s in templates[i][2]})
             if s in stations} for group in groups]


def scan_grouped_day(date, data_dir, groups, station_groups, previous_tails,
                     buffer_seconds, read_data, scan_group, release_cache):
    """All groups see yesterday's tails; only publish today's tails at the end."""
    next_tails = {}
    for group, stations in zip(groups, station_groups):
        data = None
        try:
            data, tails = read_data(date, data_dir, stations,
                buffer_seconds=buffer_seconds, previous_raw_tails=previous_tails)
            next_tails.update(tails)
            scan_group(group, data)
        finally:
            del data
            release_cache()
    return next_tails
