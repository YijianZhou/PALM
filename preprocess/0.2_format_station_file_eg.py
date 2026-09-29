""" Format station file in Fullfed format, e.g. http://www.fdsn.org/networks/detail/7D_2011/
"""
import os
from collections import defaultdict
from obspy import UTCDateTime

from preprocess_common import resolve_path


# i/o paths
CASE_CODE = 'eg'
networks = ['ci']
fsta_template = str(resolve_path('input/station_%s.fullfed'))
fout = str(resolve_path('output/station_%s.csv' % CASE_CODE))
fsummary = str(resolve_path('output/station_%s_metadata_audit.csv' % CASE_CODE))
fcoverage = str(resolve_path(
    'output/station_%s_gain_interval_audit.csv' % CASE_CODE))
# Retain all location/band epochs; select priorities at waveform reading.
loc_codes = ['10', '20', '01', '02', '00', '']
chn_codes = ['HH', 'BH', 'EH', 'HN', 'EN', 'SH']
lat_min, lat_max = 35.5, 36.0
lon_min, lon_max = -117.8, -117.3
t_min, t_max = UTCDateTime('20190701'), UTCDateTime('20190801')


def comp_key(chn):
    comp = chn[-1].upper()
    if comp == 'E' or comp == '1': return 'E'
    if comp == 'N' or comp == '2': return 'N'
    if comp == 'Z' or comp == '3': return 'Z'
    return comp


def time_str(t):
    return t.strftime('%Y%m%d')


def read_fullfed(fsta):
    sta_dict = defaultdict(list)
    with open(fsta, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        codes = line.strip().split('|')
        if len(codes) < 17:
            continue
        net, sta, loc, chn = codes[0:4]
        chn0 = chn[0:2]
        if chn0 not in chn_codes:
            continue
        try:
            lat, lon, ele = [float(code) for code in codes[4:7]]
            gain = float(codes[11])
            t0 = UTCDateTime(codes[-2])
            t1 = UTCDateTime(codes[-1]) if codes[-1] else t_max
        except (TypeError, ValueError):
            continue
        if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
            continue
        if t1 <= t_min or t0 >= t_max:
            continue
        sta_dict[(net,sta)].append({
            'net': net, 'sta': sta, 'loc': loc, 'chn': chn, 'chn0': chn0,
            'comp': comp_key(chn), 'lat': lat, 'lon': lon, 'ele': ele,
            'gain': gain, 't0': t0, 't1': t1})
    return sta_dict


def gain_code(recs):
    comp_gains = {}
    for rec in sorted(recs, key=lambda x: x['chn']):
        if rec['comp'] not in comp_gains:
            comp_gains[rec['comp']] = rec['gain']
    if not comp_gains:
        raise ValueError('selected channel has no usable component gain')
    fallback = next(iter(comp_gains.values()))
    gains = [comp_gains.get(comp, fallback) for comp in ['E', 'N', 'Z']]
    missing = [comp for comp in ['E', 'N', 'Z'] if comp not in comp_gains]
    note = ''
    if missing:
        note = 'missing_{}_gain_filled'.format(''.join(missing))
    return '{},{},{}'.format(*gains), note


def period_active_records(recs, t0, t1):
    return [rec for rec in recs if rec['t0']<=t0 and rec['t1']>=t1]


def choose_channel(active_recs):
    active_chns = sorted(set([rec['chn0'] for rec in active_recs]))
    for chn0 in chn_codes:
        if chn0 in active_chns:
            return chn0, active_chns
    return None, active_chns


def choose_location(active_recs):
    active_locs = sorted(set([rec['loc'] for rec in active_recs]))
    for loc in loc_codes:
        if loc in active_locs:
            return loc, active_locs
    return active_locs[0], active_locs


def normalize_coverage(periods, station):
    """Fill gain-interval gaps using the nearest selected epoch."""
    if not periods:
        return periods, []
    periods = sorted(periods, key=lambda item: (item['t0'], item['t1']))
    audit = []
    if t_min < periods[0]['t0']:
        old_start = periods[0]['t0']
        periods[0]['t0'] = t_min
        audit.append([
            station, 'extend_start', str(t_min), str(old_start),
            str(t_min), str(t_min)])
    if t_max > periods[-1]['t1']:
        old_end = periods[-1]['t1']
        periods[-1]['t1'] = t_max
        audit.append([
            station, 'extend_end', str(old_end), str(t_max),
            str(t_max), str(t_max)])
    for left, right in zip(periods, periods[1:]):
        if left['t1'] >= right['t0']:
            continue
        gap_start, gap_end = left['t1'], right['t0']
        midpoint = gap_start + (gap_end - gap_start) / 2.0
        left['t1'] = midpoint
        right['t0'] = midpoint
        audit.append([
            station, 'fill_internal_gap', str(gap_start), str(gap_end),
            str(midpoint), str(midpoint)])
    return periods, audit


def format_station(sta_dict):
    """Keep every selector and its true epochs; never fill operational gaps."""
    out_lines, summary_lines = [], []
    for (net, sta), records in sorted(sta_dict.items()):
        groups = defaultdict(list)
        for record in records:
            groups[(record['chn0'], record['loc'])].append(record)
        for (band, loc), recs in sorted(groups.items()):
            edges = [UTCDateTime(t) for t in sorted({float(r[k]) for r in recs for k in ('t0', 't1')})]
            for start, end in zip(edges, edges[1:]):
                active = period_active_records(recs, start, end)
                if not active:
                    continue
                gains, note = gain_code(active)
                coords = [sum(r[k] for r in active) / len(active) for k in ('lat', 'lon', 'ele')]
                out_lines.append('{0}.{1}.{2}.{3},{4:.6f},{5:.6f},{6:.1f},{7},{8},{9}\n'.format(
                    net, sta, band, loc, *coords, gains, start, end))
                if note:
                    summary_lines.append('{},{},{},{},{},{},{},{},{},\n'.format(
                        net, sta, time_str(start), time_str(end), loc, loc, band, band, note))
    return out_lines, summary_lines, []



def write_lines(fout, lines, header=None):
    out_dir = os.path.dirname(fout)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)
    partial = fout + '.partial'
    with open(partial, 'w', encoding='utf-8') as f:
        if header:
            f.write(header)
        for line in lines:
            f.write(line)
    os.replace(partial, fout)


def main():
    all_rows, all_summary, all_coverage = [], [], []
    for net in networks:
        fsta = fsta_template % net
        if not os.path.exists(fsta): continue
        sta_dict = read_fullfed(fsta)
        out_lines, summary_lines, coverage_rows = format_station(sta_dict)
        all_rows.extend(out_lines)
        all_summary.extend(summary_lines)
        all_coverage.extend(coverage_rows)
    if not all_rows:
        raise RuntimeError('no station epochs matched the example settings')
    unique_rows = sorted(set(all_rows))
    write_lines(fout, unique_rows)
    write_lines(fsummary, all_summary,
        header='net,sta,t0,t1,active_locs,selected_loc,active_chns,selected_chn,gain_note,duplicate_components\n')
    write_lines(fcoverage, [','.join(row) + '\n' for row in all_coverage],
        header='station,action,gap_start,gap_end,new_left_end,new_right_start\n')
    print('%s %s stations/periods'%(fout,len(unique_rows)))
    print('%s %s audit rows'%(fsummary,len(all_summary)))
    print('%s %s coverage adjustments'%(fcoverage,len(all_coverage)))


if __name__ == '__main__':
    main()
