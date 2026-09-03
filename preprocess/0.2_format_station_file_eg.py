""" Format station file in Fullfed format, e.g. http://www.fdsn.org/networks/detail/7D_2011/
"""
import os
from collections import defaultdict
from obspy import UTCDateTime

from preprocess_common import resolve_path


# i/o paths
CASE_CODE = 'eg'
networks = ['ci']
fsta_template = str(resolve_path('input/station_%s_%s.fullfed'))
fout = str(resolve_path('output/station_%s_raw.csv' % CASE_CODE))
fsummary = str(resolve_path('output/station_%s_metadata_audit.csv' % CASE_CODE))
# channel priority, selected independently for each net.sta.loc time period
chn_codes = ['HH', 'BH', 'EH', 'NH']
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
        sta_dict[(net,sta,loc)].append({
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


def format_station(sta_dict):
    out_lines, summary_lines = [], []
    for (net,sta,loc), recs in sorted(sta_dict.items()):
        edge_dict = {}
        for rec in recs:
            edge_dict[float(rec['t0'])] = rec['t0']
            edge_dict[float(rec['t1'])] = rec['t1']
        edges = [edge_dict[key] for key in sorted(edge_dict)]
        periods = []
        for idx in range(len(edges)-1):
            t0, t1 = edges[idx], edges[idx+1]
            if t0>=t1: continue
            active_recs = period_active_records(recs, t0, t1)
            if len(active_recs)==0: continue
            chn0, active_chns = choose_channel(active_recs)
            if chn0 is None: continue
            sel_recs = [rec for rec in active_recs if rec['chn0']==chn0]
            gain_str, gain_note = gain_code(sel_recs)
            lat = sum([rec['lat'] for rec in sel_recs]) / len(sel_recs)
            lon = sum([rec['lon'] for rec in sel_recs]) / len(sel_recs)
            ele = sum([rec['ele'] for rec in sel_recs]) / len(sel_recs)
            comp_counts = defaultdict(int)
            for rec in sel_recs:
                comp_counts[rec['comp']] += 1
            dup_comps = sorted([comp for comp,count in comp_counts.items() if count>1])
            periods.append({
                't0': t0, 't1': t1, 'chn0': chn0,
                'active_chns': active_chns, 'gain_str': gain_str,
                'gain_note': gain_note, 'dup_comps': dup_comps,
                'lat': lat, 'lon': lon, 'ele': ele})

        for period in sorted(periods, key=lambda item: (item['t0'], item['t1'])):
            net_sta_chn_loc = '%s.%s.%s.%s'%(net,sta,period['chn0'],loc)
            out_lines.append('{},{:.6f},{:.6f},{:.1f},{},{},{}\n'.format(
                net_sta_chn_loc, period['lat'], period['lon'], period['ele'],
                period['gain_str'], time_str(period['t0']), time_str(period['t1'])))
            if (len(period['active_chns'])>1 or period['gain_note'] or
                    len(period['dup_comps'])>0):
                summary_lines.append('{},{},{},{},{},{},{},{},{}\n'.format(
                    net, sta, loc, time_str(period['t0']), time_str(period['t1']),
                    ';'.join(period['active_chns']), period['chn0'],
                    period['gain_note'], ';'.join(period['dup_comps'])))
    return out_lines, summary_lines


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
    all_rows, all_summary = [], []
    for net in networks:
        fsta = fsta_template % (net, CASE_CODE)
        if not os.path.exists(fsta): continue
        sta_dict = read_fullfed(fsta)
        out_lines, summary_lines = format_station(sta_dict)
        all_rows.extend(out_lines)
        all_summary.extend(summary_lines)
    if not all_rows:
        raise RuntimeError('no station epochs matched the example settings')
    unique_rows = sorted(set(all_rows))
    write_lines(fout, unique_rows)
    write_lines(fsummary, all_summary,
        header='net,sta,loc,t0,t1,active_chns,selected_chn,gain_note,duplicate_components\n')
    print('%s %s stations/periods'%(fout,len(unique_rows)))
    print('%s %s audit rows'%(fsummary,len(all_summary)))


if __name__ == '__main__':
    main()
