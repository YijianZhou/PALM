"""Case-first UCVM -> validated dense profile -> explicit layered 1D model.

No travel-time calculation or PAL dependency. All user settings live in JSON.
"""
import argparse
import csv
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import subprocess
from subprocess import CalledProcessError
import tempfile

import numpy as np


DEFAULT_CONFIG = Path(__file__).with_name('config_eg.json')


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def positive(value, name, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive')
    if integer and not isinstance(value, int):
        raise ValueError(f'{name} must be an integer')


def axis(spec):
    """Regular samples within bounds; never append a shorter edge interval."""
    if len(spec) != 3 or not np.all(np.isfinite(spec)) or spec[1] < spec[0]:
        raise ValueError('Axis must be finite [minimum, maximum, step], minimum <= maximum')
    positive(spec[2], 'axis step')
    return spec[0] + np.arange(int(np.floor((spec[1] - spec[0]) / spec[2] + 1e-9)) + 1) * spec[2]


def load_config(path=DEFAULT_CONFIG):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text(encoding='utf-8'))
    cfg['_directory'] = str(path.parent)
    cfg['_config_path'] = str(path)
    ex, av, layers = cfg['extract'], cfg['average'], cfg['layers']
    h = layers.get('model_surface_elevation_km')
    if isinstance(h, bool) or not isinstance(h, (int, float)) or not np.isfinite(h):
        raise ValueError('layers.model_surface_elevation_km must be finite km above sea level')
    for key in ('longitude_deg', 'latitude_deg', 'depth_m'):
        values = axis(ex[key])
        if key == 'longitude_deg' and (values.min() < -180 or values.max() > 180):
            raise ValueError('Longitude outside [-180, 180]')
        if key == 'latitude_deg' and (values.min() < -90 or values.max() > 90):
            raise ValueError('Latitude outside [-90, 90]')
    if ex['depth_reference'] not in ('local_surface', 'sea_level'):
        raise ValueError('depth_reference must be local_surface or sea_level')
    if ex['depth_reference'] == 'local_surface' and ex['depth_m'][0] < 0:
        raise ValueError('Local-surface query depths must be nonnegative')
    if ex['value_source'] not in ('crustal', 'combined'):
        raise ValueError('value_source must be crustal or combined')
    if not isinstance(ex['model'], str) or not ex['model'].strip():
        raise ValueError('A UCVM model/model list is required')
    positive(ex['batch_size'], 'batch_size', True)
    positive(ex['timeout_sec'], 'timeout_sec')
    if av['statistic'] not in ('mean', 'median', 'harmonic'):
        raise ValueError('statistic must be mean, median, or harmonic')
    positive(av['smoothing_window_points'], 'smoothing_window_points', True)
    if av['smoothing_window_points'] % 2 == 0:
        raise ValueError('smoothing_window_points must be odd')
    for key in ('min_thickness_km', 'vp_variation_kmps', 'vs_variation_kmps',
                'vp_gradient_per_sec', 'vs_gradient_per_sec'):
        positive(layers[key], key)
    if layers['average_method'] not in ('mean', 'harmonic', 'sample_median'):
        raise ValueError('average_method must be mean, harmonic, or sample_median')
    if layers.get('method', 'legacy') not in ('legacy', 'interfaces'):
        raise ValueError('layers.method must be legacy or interfaces')
    if layers.get('method') == 'interfaces':
        validate_interface_options(layers)
    base_layers(layers['schedule_km'])
    for section, names in ((cfg, ('overwrite',)), (av, ('require_full_depth_coverage',
            'allow_legacy_input_without_metadata')), (layers, ('adaptive',
            'use_gradient_criterion', 'extend_last_layer_as_halfspace')),
            (cfg['cre_export'], ('acknowledge_halfspace_extension',))):
        for name in names:
            if not isinstance(section[name], bool):
                raise ValueError(f'{name} must be boolean')
    positive(cfg['plot']['dpi'], 'plot dpi', True)
    if cfg['plot']['reference_format'] not in ('top_vp_vpvs', 'depth_vp_vs'):
        raise ValueError('Unknown reference_format')
    shift = cfg['cre_export']['shift_nonfirst_layer_tops_km']
    if not isinstance(shift, (float, int)) or not np.isfinite(shift):
        raise ValueError('CRE depth shift must be finite')
    outputs = [ex['output'], av['output'], av['output_full'], layers['output'],
               layers['model_output'], cfg['plot']['output'],
               cfg['cre_export']['output_p'], cfg['cre_export']['output_s']]
    if cfg['plot'].get('gradient_output'):
        outputs.append(cfg['plot']['gradient_output'])
    if len({location(cfg,name).resolve() for name in outputs}) != len(outputs):
        raise ValueError('Stage output paths must be distinct')
    if 'maps' in cfg and 'hypoinverse' in cfg:
        validate_workflow_options(cfg)
        extra=[cfg['maps']['output']]+[cfg['hypoinverse'][k] for k in ('output','output_p','output_s','commands_output')]
        all_paths=[location(cfg,n).resolve() for n in outputs+extra]
        if len(set(all_paths))!=len(all_paths): raise ValueError('Workflow output paths must be distinct')
    return cfg


def location(cfg, name):
    return Path(cfg['_directory']) / cfg['output_dir'] / name


def public_config(cfg):
    return {key: value for key, value in cfg.items() if not key.startswith('_')}


def sidecar(path):
    return Path(str(path) + '.meta.json')


def require_outputs(paths, overwrite):
    resolved = [Path(p).resolve() for p in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError('Output paths must be distinct')
    for path in resolved:
        if path.exists() and not overwrite:
            raise FileExistsError(f'Output exists: {path}; enable overwrite in config to replace it')


def protect_input(source, targets):
    if Path(source).resolve() in [Path(p).resolve() for p in targets]:
        raise ValueError('An output must not overwrite its input')


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_metadata(path, metadata):
    metadata = dict(metadata, artifact_sha256=digest(path))
    atomic_text(sidecar(path), json.dumps(metadata, indent=2, allow_nan=False) + '\n')


def read_metadata(path):
    meta = json.loads(sidecar(path).read_text(encoding='utf-8'))
    if meta['artifact_sha256'] != digest(path):
        raise ValueError(f'Metadata checksum mismatch: {path}')
    return meta


def validate_material(values):
    values = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError('Vp, Vs and density must be finite and positive')
    if np.any(values[..., 0] <= values[..., 1]):
        raise ValueError('Vp must exceed Vs')


def parse_ucvm_output(text, expected, options):
    """Read documented UCVM columns, then verify every requested coordinate."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.lower().startswith('using geo'):
            continue
        parts = line.split()
        start = 6 if options['value_source'] == 'crustal' else 14
        try:
            lon, lat, z, elevation = map(float, parts[:4])
            vp, vs, rho = map(float, parts[start:start + 3])
            model = parts[5]
        except (ValueError, IndexError) as error:
            raise ValueError(f'Malformed UCVM output: {line[:160]}') from error
        if model.lower() in ('none', 'null') and options['value_source'] == 'crustal':
            raise ValueError('UCVM returned no crustal model for a requested point')
        validate_material([vp, vs, rho])
        if not np.all(np.isfinite([lon, lat, z, elevation])):
            raise ValueError('Nonfinite UCVM coordinates/elevation')
        depth = z if options['depth_reference'] == 'local_surface' else -z
        rows.append((lon, lat, depth, vp, vs, rho, model, elevation))
    if len(rows) != len(expected):
        raise ValueError(f'UCVM returned {len(rows)} rows for {len(expected)} requested points')
    # UCVM text output commonly has 4 decimal places for coordinates and 3 for Z.
    for row, point in zip(rows, expected):
        if not np.all(np.abs(np.asarray(row[:3]) - point) <= [0.000051, 0.000051, 0.00051]):
            raise ValueError('UCVM coordinate/order mismatch')
    # Persist exact query coordinates, avoiding precision loss in UCVM display.
    return [tuple(point) + row[3:] for point, row in zip(expected, rows)]


def extract(cfg):
    options = cfg['extract']
    query = Path(options['ucvm_query']).expanduser()
    conf = Path(options['ucvm_conf']).expanduser()
    if not query.is_absolute():
        query = Path(cfg['_directory']) / query
    if not conf.is_absolute():
        conf = Path(cfg['_directory']) / conf
    if not query.is_file() or not conf.is_file():
        raise FileNotFoundError(
            f'Configure existing UCVM paths in the selected JSON config.\n'
            f'ucvm_query: {query}\nucvm_conf: {conf}\n'
            'Use the original installation prefix; do not rename the installed tree.')
    target = location(cfg, options['output'])
    require_outputs([target, sidecar(target)], cfg['overwrite'])
    lons, lats, depths = [axis(options[k]) for k in ('longitude_deg','latitude_deg','depth_m')]
    for values, decimals in ((lons,4),(lats,4),(depths,3)):
        if len(np.unique(np.round(values, decimals))) != len(values):
            raise ValueError('Sampling is finer than UCVM text coordinate precision')
    points = ((lon, lat, depth) for depth in depths for lat in lats for lon in lons)
    cmd = [str(query), '-f', str(conf), '-m', options['model'], '-c',
           'gd' if options['depth_reference'] == 'local_surface' else 'ge']
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    models = set()
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write('# lon lat depth_m vp_mps vs_mps rho_kgm3 model surface_elevation_m\n')
            while True:
                batch = list(itertools.islice(points, options['batch_size']))
                if not batch:
                    break
                sign = 1 if options['depth_reference'] == 'local_surface' else -1
                payload = ''.join(f'{x:.9f} {y:.9f} {sign*z:.6f}\n' for x,y,z in batch)
                try:
                    response = subprocess.run(cmd, input=payload, text=True, capture_output=True,
                                              timeout=options['timeout_sec'], check=True)
                except CalledProcessError as exc:
                    raise RuntimeError(
                        f'UCVM failed with exit status {exc.returncode} while querying points '
                        f'{written+1}-{written+len(batch)}.\n'
                        f'ucvm_query: {query}\nucvm_conf: {conf}\n'
                        f'UCVM stderr:\n{exc.stderr or "(empty)"}\n'
                        'Activate the working UCVM runtime environment and source its '
                        'conf/ucvm_env.sh in this shell. Check shared libraries and model '
                        'paths; keep the installed tree at its original prefix. '
                        'Exit status alone does not establish the cause.') from exc
                rows = parse_ucvm_output(response.stdout, batch, options)
                for x,y,z,vp,vs,rho,model,elevation in rows:
                    stream.write(f'{x:.9f} {y:.9f} {z:.6f} {vp:.6f} {vs:.6f} {rho:.6f} {model} {elevation:.6f}\n')
                    models.add(model)
                written += len(rows)
                print(f'UCVM: {written}/{len(lons)*len(lats)*len(depths)} points', flush=True)
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    write_metadata(target, dict(schema_version=1, kind='ucvm_samples', settings=options,
        command=cmd, ucvm_conf_sha256=digest(conf), ucvm_query_sha256=digest(query),
        rows=written, actual_models=sorted(models), config=public_config(cfg)))
    return target


def read_samples(path):
    rows = []
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            fields = line.split()
            if len(fields) not in (7, 8):
                raise ValueError('UCVM sample table requires 7 legacy or 8 current columns')
            row = list(map(float, fields[:6]))
            if not np.all(np.isfinite(row)):
                raise ValueError('Nonfinite UCVM sample')
            validate_material(row[3:6])
            rows.append(row)
    if not rows:
        raise ValueError('Empty UCVM sample table')
    data = np.array(rows)
    if len(np.unique(data[:, :3], axis=0)) != len(data):
        raise ValueError('Duplicate lon/lat/depth samples')
    return data


def csv_text(rows):
    stream = io.StringIO(newline='')
    csv.writer(stream, lineterminator='\n').writerows(rows)
    return stream.getvalue()


def average(cfg):
    options, ex = cfg['average'], cfg['extract']
    source, target = location(cfg, options['input']), location(cfg, options['output'])
    full_target = location(cfg, options['output_full'])
    targets = [target, sidecar(target), full_target]
    protect_input(source, targets)
    require_outputs(targets, cfg['overwrite'])
    metadata = None
    if sidecar(source).exists():
        metadata = read_metadata(source)
        for key in ('longitude_deg','latitude_deg','depth_m','depth_reference','value_source','model'):
            if metadata['settings'][key] != ex[key]:
                raise ValueError(f'Extraction/config mismatch: {key}')
    elif not options['allow_legacy_input_without_metadata']:
        raise ValueError('Extraction metadata missing; explicitly enable legacy input only after checking its datum and columns')
    data = read_samples(source)
    axes = [axis(ex[k]) for k in ('longitude_deg','latitude_deg','depth_m')]
    indices = []
    for column, values in enumerate(axes):
        upper = np.searchsorted(values, data[:, column]).clip(0, len(values)-1)
        lower = (upper-1).clip(0, len(values)-1)
        nearest = np.where(abs(data[:,column]-values[lower]) <= abs(data[:,column]-values[upper]), lower, upper)
        tolerance = 5.1e-7 if column < 2 else 5.1e-4
        if np.any(abs(data[:, column] - values[nearest]) > tolerance):
            raise ValueError('Sample coordinate outside configured extraction grid')
        indices.append(nearest)
    grid_indices = np.column_stack(indices)
    if len(np.unique(grid_indices, axis=0)) != len(data):
        raise ValueError('Multiple samples map to the same grid point')
    expected = len(axes[0]) * len(axes[1])
    profile, counts, densities, missing_depths = [], [], [], []
    for iz, depth in enumerate(axes[2]):
        selected = data[grid_indices[:, 2] == iz, 3:6]
        if len(selected) != expected:
            missing_depths.append(float(depth))
            if options['require_full_depth_coverage']:
                raise ValueError(f'Incomplete coverage at depth {depth} m: {len(selected)}/{expected}')
        if not len(selected):
            raise ValueError(f'No samples at depth {depth} m')
        method = options['statistic']
        velocity = (np.median(selected[:, :2], axis=0) if method == 'median' else
                    1/np.mean(1/selected[:, :2], axis=0) if method == 'harmonic' else
                    np.mean(selected[:, :2], axis=0))
        profile.append([depth/1000, *(velocity/1000)])
        densities.append(float(np.median(selected[:,2]) if method == 'median' else np.mean(selected[:,2])))
        counts.append(len(selected))
    profile = np.asarray(profile)
    window = options['smoothing_window_points']
    if window > len(profile):
        raise ValueError('Smoothing window exceeds profile length')
    if window > 1:
        for col in (1,2):
            profile[:,col] = np.convolve(np.pad(profile[:,col], window//2, mode='edge'),
                                         np.ones(window)/window, mode='valid')
    validate_profile(profile)
    atomic_text(target, csv_text(profile.tolist()))
    atomic_text(full_target, 'depth_km\tvp_kmps\tvs_kmps\traw_density_kgm3\tnpts\n' +
        ''.join('\t'.join(map(str,[*row,density,count]))+'\n'
                for row,density,count in zip(profile,densities,counts)))
    write_metadata(target, dict(schema_version=1, kind='dense_1d', depth_reference=ex['depth_reference'],
        units=dict(depth='km', velocity='km/s'), input_sha256=digest(source),
        extraction_metadata=metadata, legacy_datum_unverified=metadata is None,
        horizontal_weighting='equal_sample', expected_points_per_depth=expected,
        sample_counts=counts, incomplete_depths_m=missing_depths,
        settings=options, config=public_config(cfg)))
    return target


def validate_profile(profile):
    if profile.ndim != 2 or profile.shape[1] != 3 or len(profile) < 2:
        raise ValueError('Profile requires at least two depth,Vp,Vs rows')
    if not np.all(np.isfinite(profile)) or np.any(np.diff(profile[:,0]) <= 0):
        raise ValueError('Depths must be finite, unique, and strictly increasing')
    validate_material(profile[:,1:3])


def read_profile(path):
    profile = np.loadtxt(path, delimiter=',', comments='#', ndmin=2)
    validate_profile(profile)
    return profile


def base_layers(schedule):
    result = []
    previous = None
    if not schedule:
        raise ValueError('Empty layer schedule')
    for top, bottom, step in schedule:
        if not np.all(np.isfinite([top,bottom,step])) or bottom <= top:
            raise ValueError('Layer schedule must have increasing finite bounds')
        positive(step, 'base layer thickness')
        if previous is not None and not np.isclose(top, previous, rtol=0, atol=1e-9):
            raise ValueError('Layer schedule has a gap or overlap')
        edges = list(axis([top,bottom,step]))
        if edges[-1] < bottom - 1e-9:
            edges.append(bottom)
        else:
            edges[-1] = bottom
        result.extend(zip(edges[:-1], edges[1:]))
        previous = bottom
    return result


def layer_summary(profile, top, bottom, method):
    if top < profile[0,0] - 1e-9 or bottom > profile[-1,0] + 1e-9:
        raise ValueError('Layer schedule exceeds sampled depths; extrapolation is disabled')
    inside = profile[(profile[:,0] > top) & (profile[:,0] < bottom), 0]
    z = np.concatenate(([top], inside, [bottom]))
    values = np.column_stack([np.interp(z, profile[:,0], profile[:,col]) for col in (1,2)])
    width = bottom-top
    if method == 'sample_median':
        representative = np.median(values, axis=0)
    elif method == 'mean':
        representative = np.sum(np.diff(z)[:,None]*(values[:-1]+values[1:])/2,axis=0)/width
    elif method == 'harmonic':
        # Exact integral of slowness for each linearly interpolated segment.
        a,b = values[:-1],values[1:]
        delta = b-a
        factor = np.divide(np.log1p(delta/a),delta,out=1/a.copy(),where=abs(delta)>1e-12)
        representative = width / np.sum(np.diff(z)[:,None]*factor,axis=0)
    else:
        raise ValueError('Unknown layer average method')
    variation = np.ptp(values,axis=0)
    return dict(top_depth_km=float(top), bottom_depth_km=float(bottom),
                vp_kmps=float(representative[0]), vs_kmps=float(representative[1]),
                vp_variation_kmps=float(variation[0]), vs_variation_kmps=float(variation[1]))


def build_layers(profile, options):
    validate_profile(profile)
    if options.get('method') == 'interfaces':
        return build(profile, options)[0]
    result = []
    def split(top,bottom):
        item = layer_summary(profile, top, bottom, options['average_method'])
        width = bottom-top
        excessive = (item['vp_variation_kmps'] > options['vp_variation_kmps'] or
                     item['vs_variation_kmps'] > options['vs_variation_kmps'])
        if options['use_gradient_criterion']:
            excessive |= (item['vp_variation_kmps']/width > options['vp_gradient_per_sec'] or
                          item['vs_variation_kmps']/width > options['vs_gradient_per_sec'])
        if options['adaptive'] and excessive and width/2 >= options['min_thickness_km']-1e-12:
            midpoint = (top+bottom)/2
            split(top,midpoint)
            split(midpoint,bottom)
        else:
            item['variation_limit_exceeded'] = bool(excessive)
            result.append(item)
    for top,bottom in base_layers(options['schedule_km']):
        if bottom-top < options['min_thickness_km']-1e-12:
            raise ValueError('Base layer is thinner than min_thickness_km')
        split(top,bottom)
    return result


def layer(cfg):
    options = cfg['layers']
    source = location(cfg,options['input'])
    metadata = read_metadata(source)
    profile = read_profile(source)
    interface_metadata=None
    if options.get('method') == 'interfaces':
        result,interface_metadata=build(profile,options)
    else:
        result = build_layers(profile,options)
    target = location(cfg,options['output'])
    model_path = location(cfg,options['model_output'])
    protect_input(source, [target,sidecar(target),model_path])
    require_outputs([target,sidecar(target),model_path],cfg['overwrite'])
    model = dict(schema_version=1, kind='layered_1d_velocity_model',
        case_name=cfg['case_name'], units=dict(depth='km',velocity='km/s'),
        depth_reference=metadata['depth_reference'], depth_positive='down',
        model_surface_elevation_km=options['model_surface_elevation_km'] if metadata['depth_reference']=='local_surface' else 0.0,
        interpolation='piecewise_constant',
        bottom_policy='last_layer_halfspace' if options['extend_last_layer_as_halfspace'] else 'finite',
        sampled_depth_range_km=[float(profile[0,0]),float(profile[-1,0])],
        model_depth_range_km=[result[0]['top_depth_km'],result[-1]['bottom_depth_km']],
        layers=result, dense_input_sha256=digest(source), dense_metadata=metadata,
        settings=options, config=public_config(cfg))
    if options.get('method') == 'interfaces':
        model['major_interfaces'] = interface_metadata
    if options.get('method') == 'interfaces':
        dp,vp=options['depth_decimals'],options['velocity_decimals']
        atomic_text(target,''.join(f"{r['top_depth_km']:.{dp}f},{r['vp_kmps']:.{vp}f},{r['vs_kmps']:.{vp}f},{r['vp_kmps']/r['vs_kmps']:.3f}\n" for r in result))
    else:
        atomic_text(target,csv_text([[r['top_depth_km'],r['vp_kmps'],r['vs_kmps'],f"{r['vp_kmps']/r['vs_kmps']:.3f}"] for r in result]))
    write_metadata(target,model)
    atomic_text(model_path,json.dumps(model,indent=2,allow_nan=False)+'\n')
    return model_path


def export_cre(cfg):
    options=cfg['cre_export']
    source=location(cfg,cfg['layers']['model_output'])
    model=json.loads(source.read_text(encoding='utf-8'))
    if model['bottom_policy']=='finite' and not options['acknowledge_halfspace_extension']:
        raise ValueError('CRE implies a final halfspace; explicitly acknowledge its extension in config')
    layers=model['layers']
    tops=np.array([row['top_depth_km'] for row in layers])
    if not np.isclose(tops[0],0):
        raise ValueError('CRE export requires a first layer top of zero')
    tops[1:]+=options['shift_nonfirst_layer_tops_km']
    if np.any(np.diff(np.round(tops,2))<=0):
        raise ValueError('CRE shift/rounding collapses or reverses layer tops')
    targets=[location(cfg,options[key]) for key in ('output_p','output_s')]
    require_outputs(targets+[sidecar(p) for p in targets],cfg['overwrite'])
    for target,key in zip(targets,('vp_kmps','vs_kmps')):
        text=f"{options['model_name'][:30]:<30}\n"
        for row,depth in zip(layers,tops):
            velocity=row[key]
            if velocity<=0 or len(f'{velocity:5.2f}')>5 or len(f'{depth:5.2f}')>5:
                raise ValueError('CRE value does not fit its 5.2 field')
            text+=f'{velocity:5.2f}{depth:5.2f}\n'
        atomic_text(target,text)
        write_metadata(target,dict(kind='cre_export',input_sha256=digest(source),
            original_depth_reference=model['depth_reference'],settings=options,
            note='Legacy nonfirst-top shift is an export transform, not a general datum conversion.'))
    return targets


def save_figure(fig, target, dpi):
    # Replace completed figures atomically, like the numeric output files.
    target=Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=target.suffix,dir=target.parent,delete=False) as stream:
        temporary=Path(stream.name)
    try:
        fig.savefig(temporary,dpi=dpi)
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)


def plot(cfg):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    profile=read_profile(location(cfg,cfg['average']['output']))
    model=json.loads(location(cfg,cfg['layers']['model_output']).read_text())
    target=location(cfg,cfg['plot']['output'])
    gradient_target = location(cfg,cfg['plot']['gradient_output']) if cfg['plot'].get('gradient_output') else None
    require_outputs([target]+([gradient_target] if gradient_target else []),cfg['overwrite'])
    fig,axes=plt.subplots(1,2,figsize=(9,7),sharey=True)
    z=np.array([[r['top_depth_km'],r['bottom_depth_km']] for r in model['layers']]).ravel()
    for col,(ax,key,label) in enumerate(zip(axes,('vp_kmps','vs_kmps'),('P','S')),start=1):
        ax.plot(profile[:,col],profile[:,0],label='Dense regional profile')
        ax.plot(np.repeat([r[key] for r in model['layers']],2),z,label='Layered model')
        reference=cfg['plot']['reference_file']
        if reference:
            refpath=Path(cfg['_directory'])/reference
            fmt=cfg['plot']['reference_format']
            ref=np.loadtxt(refpath,delimiter=',' if fmt=='depth_vp_vs' else None,ndmin=2)
            if fmt=='top_vp_vpvs':
                ref[:,2]=ref[:,1]/ref[:,2]
            validate_profile(ref)
            ax.plot(ref[:,col],ref[:,0],label='Reference',drawstyle='steps-post' if fmt=='top_vp_vpvs' else 'default')
        for interface in model.get('major_interfaces', []):
            depth=interface['depth_km']
            ax.axhline(depth,color='0.4',ls='--',lw=.8)
            if 'transition_bounds_km' in interface:
                ax.axhspan(*interface['transition_bounds_km'],color='gold',alpha=.15)
            ax.text(.02,depth,f'{depth:.1f} km',transform=ax.get_yaxis_transform(),va='bottom',fontsize=8)
        ax.set_xlabel(f'{label} velocity (km/s)')
        ax.grid(alpha=.2)
    axes[0].invert_yaxis()
    axes[0].set_ylabel(f"Depth (km; {model['depth_reference'].replace('_',' ')})")
    axes[0].legend()
    fig.suptitle(cfg['case_name'])
    fig.tight_layout()
    target.parent.mkdir(parents=True,exist_ok=True)
    save_figure(fig, target, cfg['plot']['dpi'])
    plt.close(fig)
    if gradient_target:
        found=model.get('major_interfaces', [])
        g=np.diff(profile[:,1:],axis=0)/np.diff(profile[:,0])[:,None]
        fig,axes=plt.subplots(1,2,figsize=(9,7),sharey=True)
        for j,ax in enumerate(axes):
            ax.plot(np.repeat(g[:,j],2),np.column_stack([profile[:-1,0],profile[1:,0]]).ravel())
            ax.axvline(cfg['layers']['interface_gradient_per_sec'],ls=':',color='black',label='Interface threshold')
            for item in found:
                d=item['depth_km'];ax.axhline(d,ls='--',color='0.4')
                if 'transition_bounds_km' in item:
                    ax.axhspan(*item['transition_bounds_km'],color='gold',alpha=.15)
                ax.text(.98,d,f'{d:.1f} km',transform=ax.get_yaxis_transform(),ha='right',va='bottom')
            ax.set_xlabel(('P' if j==0 else 'S')+' gradient [(km/s)/km]')
            ax.grid(alpha=.2)
        axes[0].invert_yaxis();axes[0].set_ylabel('Depth (km; '+model['depth_reference'].replace('_',' ')+')')
        axes[0].legend();fig.suptitle('Dense-profile gradients and major interfaces')
        fig.tight_layout();save_figure(fig, gradient_target, cfg['plot']['dpi']);plt.close(fig)
    return target



# Interface detection and constrained layered-model construction
def rounded(x, decimals):
    scale = 10**decimals
    return np.floor(np.asarray(x)*scale + 0.5)/scale


def validate_interface_options(options):
    for key in ('interface_gradient_per_sec', 'min_thickness_km', 'min_velocity_change_kmps'):
        v = options[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or v <= 0:
            raise ValueError(f'{key} must be finite and positive')
    if options.get('transition_velocity_method','median') not in ('median','equal_steps'):
        raise ValueError('transition_velocity_method must be median or equal_steps')
    for key in ('allow_low_velocity_zone', 'smooth_interfaces', 'monotonic_transition_fallback'):
        if key in options and type(options[key]) is not bool:
            raise ValueError(f'{key} must be boolean')
    if options.get('smooth_interfaces',False):
        value=options['strong_interface_gradient_per_sec']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not np.isfinite(value) or value<=0:
            raise ValueError('strong_interface_gradient_per_sec must be finite and positive')
        for key in ('transition_layers','strong_transition_layers'):
            if type(options[key]) is not int or options[key]<1:
                raise ValueError(f'{key} must be a positive integer')
    for key in ('depth_decimals', 'velocity_decimals'):
        if type(options[key]) is not int or not 0 <= options[key] <= 3:
            raise ValueError(f'{key} must be an integer in [0, 3]')


def interfaces(profile, options):
    g = np.diff(profile[:,1:], axis=0)/np.diff(profile[:,0])[:,None]
    score = g.max(axis=1)
    ids = np.flatnonzero(score > options['interface_gradient_per_sec'])
    runs = np.split(ids, np.flatnonzero(np.diff(ids)>1)+1)
    result = []
    for run in runs:
        if not len(run):
            continue
        # One dominant peak per contiguous above-threshold excursion; shallowest tie.
        i = int(run[np.argmax(score[run])])
        midpoint = float(np.mean(profile[i:i+2,0]))
        result.append(dict(depth_km=float(rounded(midpoint, options['depth_decimals'])),
                           peak_midpoint_km=midpoint,
                           interval_km=profile[i:i+2,0].tolist(),
                           vp_gradient_per_sec=float(g[i,0]), vs_gradient_per_sec=float(g[i,1])))
    return result


def representative(profile, top, bottom, options):
    samples=profile[(profile[:,0]>=top-1e-9)&(profile[:,0]<=bottom+1e-9),1:]
    if not len(samples):
        raise ValueError('Layer contains no detailed-profile samples')
    v=rounded(np.median(samples,axis=0),options['velocity_decimals'])
    if not v[0]>v[1]>0:
        raise ValueError('Rounded medians must satisfy Vp > Vs > 0')
    return v,float(np.square(samples-v).sum())


def regions(profile, options, collapse=()):
    found=interfaces(profile,options)
    width=options['min_thickness_km']
    start,end=profile[0,0],profile[-1,0]
    blocks=[]
    def regular(a,b):
        if abs(b-a)<1e-9:
            return
        if b-a<width-1e-9:
            raise ValueError('Transition zones overlap or leave a region thinner than minimum thickness')
        blocks.append((float(a),float(b),False))
    for interface_index,item in enumerate(found):
        depth=item['depth_km']
        if not options.get('smooth_interfaces',False):
            regular(start,depth);start=depth
            continue
        peak=max(item['vp_gradient_per_sec'],item['vs_gradient_per_sec'])
        number=options['strong_transition_layers'] if peak>options['strong_interface_gradient_per_sec'] else options['transition_layers']
        edges=depth+(np.arange(number+1)-number/2)*width
        if edges[0]<profile[0,0] or edges[-1]>end:
            raise ValueError('Centered transition zone extends beyond sampled depths')
        mismatch=False
        if options.get('transition_velocity_method','median') == 'median':
            values=np.array([representative(profile,a,b,options)[0] for a,b in zip(edges[:-1],edges[1:])])
            mismatch=bool(number>1 and np.any(np.max(np.abs(np.diff(values,axis=0)),axis=1)<=options['min_velocity_change_kmps']+1e-9))
        item.update(requested_transition_layers=number,contrast_mismatch=mismatch)
        reversal_fallback=interface_index in collapse
        item['reversal_fallback']=reversal_fallback
        if mismatch or reversal_fallback:
            number=1
            edges=np.array([depth-width/2,depth+width/2])
        if not np.allclose(rounded(edges,options['depth_decimals']),edges,atol=1e-9,rtol=0):
            raise ValueError('Centered transition edges do not fit depth precision; increase depth_decimals')
        regular(start,edges[0])
        for a,b in zip(edges[:-1],edges[1:]):
            blocks.append((float(a),float(b),True))
        item.update(transition_layers=number,transition_bounds_km=[float(edges[0]),float(edges[-1])])
        start=edges[-1]
    regular(start,end)
    return blocks,found


def _build_once(profile, options, collapse=()):
    blocks,found=regions(profile,options,collapse)
    scale=10**options['depth_decimals']
    if not np.allclose(rounded(profile[[0,-1],0],options['depth_decimals']),profile[[0,-1],0],atol=1e-9,rtol=0):
        raise ValueError('Profile endpoints must lie on the depth lattice')
    # State = last interval, retaining all last-velocity possibilities across regions.
    states=[]; ending={}
    origin=int(round(profile[0,0]*scale))
    for block_id,(top,bottom,fixed) in enumerate(blocks):
        lo,hi=int(round(top*scale)),int(round(bottom*scale))
        for j in ([hi] if fixed else range(lo+1,hi+1)):
            for i in ([lo] if fixed else range(lo,j)):
                if (j-i)/scale<options['min_thickness_km']-1e-9:
                    continue
                prev=ending.get(i,[])
                if i!=origin and not prev:
                    continue
                v,error=representative(profile,i/scale,j/scale,options)
                best=None
                for k in prev:
                    old=states[k]
                    delta=v-old['v']
                    if not options.get('allow_low_velocity_zone',False) and np.any(delta < -1e-9):
                        continue
                    if old['block']==block_id and np.max(np.abs(delta))<=options['min_velocity_change_kmps']+1e-9:
                        continue
                    if best is None or (-old['count'],old['cost'],k)<(-states[best]['count'],states[best]['cost'],best):
                        best=k
                if i!=origin and best is None:
                    continue
                state=dict(i=i,j=j,v=v,block=block_id,fixed=fixed,parent=best,
                           count=1 if best is None else states[best]['count']+1,
                           cost=error+(0 if best is None else states[best]['cost']))
                ending.setdefault(j,[]).append(len(states));states.append(state)
    candidates=ending.get(int(round(profile[-1,0]*scale)),[])
    if not candidates:
        raise ValueError('No feasible layering preserves original medians under thickness, transition and low-velocity constraints; adjust config')
    k=min(candidates,key=lambda k:(-states[k]['count'],states[k]['cost'],k))
    result=[]
    while k is not None:
        r=states[k]
        result.append(dict(top_depth_km=r['i']/scale,bottom_depth_km=r['j']/scale,
                           vp_kmps=float(r['v'][0]),vs_kmps=float(r['v'][1]),
                           major_layer_index=r['block'],transition_layer=r['fixed']))
        k=r['parent']
    return result[::-1],found


def build(profile, options):
    validate_interface_options(options)
    if options.get('smooth_interfaces',False) and options.get('transition_velocity_method','median') == 'equal_steps':
        return build_equal(profile,options)
    try:
        return _build_once(profile,options)
    except ValueError as error:
        if 'No feasible layering' not in str(error):
            raise
        if (options.get('allow_low_velocity_zone',False) or
                not options.get('smooth_interfaces',False) or
                not options.get('monotonic_transition_fallback',False)):
            raise
        original_error=error
    _,found=regions(profile,options)
    candidates=[i for i,item in enumerate(found) if item['transition_layers']>1]
    # Minimize the number of collapsed zones before maximizing total layer count.
    for count in range(1,len(candidates)+1):
        feasible=[]
        for subset in itertools.combinations(candidates,count):
            try:
                rows,metadata=_build_once(profile,options,subset)
            except ValueError as error:
                if ('No feasible layering' in str(error) or 'thinner than minimum' in str(error)):
                    continue
                raise
            cost=sum(representative(profile,r['top_depth_km'],r['bottom_depth_km'],options)[1] for r in rows)
            feasible.append(((-len(rows),cost,subset),rows,metadata))
        if feasible:
            _,rows,metadata=min(feasible,key=lambda result:result[0])
            return rows,metadata
    raise original_error


def equal_bridge(left, right, fixed, options):
    """N interior velocities divide the full outside-layer contrast into N+1 steps."""
    n=len(fixed)
    values=rounded(left+(right-left)*np.arange(1,n+1)[:,None]/(n+1),options['velocity_decimals'])
    all_values=np.vstack([left,values,right])
    changes=np.diff(all_values,axis=0)
    if not options.get('allow_low_velocity_zone',False) and np.any(changes < -1e-9):
        return None,'reversal'
    if n>1 and np.any(np.max(np.abs(np.diff(values,axis=0)),axis=1)<=options['min_velocity_change_kmps']+1e-9):
        return None,'contrast'
    if np.any(values[:,0]<=values[:,1]) or np.any(values<=0):
        return None,'material'
    return values,None


def solve_equal(profile, options, collapse=()):
    blocks,found=regions(profile,options,collapse)
    if blocks[0][2] or blocks[-1][2]:
        raise ValueError('Equal-step transitions require an ordinary layer above and below')
    scale=10**options['depth_decimals']
    if not np.allclose(rounded(profile[[0,-1],0],options['depth_decimals']),profile[[0,-1],0],atol=1e-9,rtol=0):
        raise ValueError('Profile endpoints must lie on the depth lattice')
    states=[];ending={};pending=[];origin=int(round(profile[0,0]*scale));failures=set()
    for block_id,(top,bottom,fixed) in enumerate(blocks):
        if fixed:
            pending.append((top,bottom,block_id));continue
        lo,hi=int(round(top*scale)),int(round(bottom*scale))
        bridge_start=int(round(pending[0][0]*scale)) if pending else lo
        for j in range(lo+1,hi+1):
            for i in range(lo,j):
                if (j-i)/scale<options['min_thickness_km']-1e-9:
                    continue
                bridge=pending if i==lo else []
                prev=ending.get(bridge_start if bridge else i,[])
                if i!=origin and not prev: continue
                v,error=representative(profile,i/scale,j/scale,options)
                best=None;best_bridge=[];best_key=None
                for k in prev:
                    old=states[k];delta=v-old['v'];values=[]
                    if bridge:
                        zone=next(item for item in found if abs(item['transition_bounds_km'][0]-bridge[0][0])<1e-9)
                        if zone['reversal_fallback'] and not options.get('monotonic_transition_fallback',False):
                            _,original_reason=equal_bridge(old['v'],v,[None]*zone['requested_transition_layers'],options)
                            if original_reason!='contrast':
                                continue
                        values,reason=equal_bridge(old['v'],v,bridge,options)
                        if reason:
                            failures.add(reason);continue
                    else:
                        if not options.get('allow_low_velocity_zone',False) and np.any(delta < -1e-9): continue
                        if old['block']==block_id and np.max(np.abs(delta))<=options['min_velocity_change_kmps']+1e-9: continue
                    # Transition residuals intentionally excluded: their samples are biased.
                    key=(-old['count'],old['cost'],k)
                    if best_key is None or key<best_key:
                        best,best_key,best_bridge=k,key,values
                if i!=origin and best is None: continue
                extra=[]
                for (a,b,bid),vel in zip(bridge,best_bridge):
                    extra.append(dict(top_depth_km=a,bottom_depth_km=b,vp_kmps=float(vel[0]),vs_kmps=float(vel[1]),major_layer_index=bid,transition_layer=True,velocity_method='equal_steps'))
                state=dict(i=i,j=j,v=v,block=block_id,parent=best,bridge=extra,
                           count=1 if best is None else states[best]['count']+1+len(extra),
                           cost=error+(0 if best is None else states[best]['cost']))
                ending.setdefault(j,[]).append(len(states));states.append(state)
        pending=[]
    candidates=ending.get(int(round(profile[-1,0]*scale)),[])
    if not candidates: return None,found,failures
    k=min(candidates,key=lambda k:(-states[k]['count'],states[k]['cost'],k));cost=states[k]['cost'];chunks=[]
    while k is not None:
        r=states[k]
        row=dict(top_depth_km=r['i']/scale,bottom_depth_km=r['j']/scale,vp_kmps=float(r['v'][0]),vs_kmps=float(r['v'][1]),major_layer_index=r['block'],transition_layer=False,velocity_method='median')
        chunks.append(r['bridge']+[row]);k=r['parent']
    rows=[r for chunk in reversed(chunks) for r in chunk]
    for index,item in enumerate(found):
        a,b=item['transition_bounds_km']
        first=next(i for i,r in enumerate(rows) if abs(r['top_depth_km']-a)<1e-9)
        last=next(i for i,r in enumerate(rows) if abs(r['bottom_depth_km']-b)<1e-9)
        item['velocity_method']='equal_steps'
        item['anchor_velocities_kmps']=[[rows[i]['vp_kmps'],rows[i]['vs_kmps']] for i in (first-1,last+1)]
        item['equal_step_unrounded_kmps']=((np.array(item['anchor_velocities_kmps'][1])-item['anchor_velocities_kmps'][0])/(last-first+2)).tolist()
        item['one_layer_fallback']=index in collapse
        item['fallback_reason']='joint_contrast_or_monotonicity_constraints' if index in collapse else None
        if index in collapse:
            _,reason=equal_bridge(np.array(item['anchor_velocities_kmps'][0]),np.array(item['anchor_velocities_kmps'][1]),[None]*item['requested_transition_layers'],options)
            item['contrast_mismatch']=reason=='contrast'
            item['reversal_fallback']=reason!='contrast'
        else:
            item['reversal_fallback']=False
    return (rows,cost),found,failures


def build_equal(profile,options):
    solution,found,reasons=solve_equal(profile,options)
    if solution: return solution[0],found
    candidates=[i for i,r in enumerate(found) if r['transition_layers']>1]
    # Contrast fallback is always allowed; monotonic fallback follows its setting.
    if 'contrast' not in reasons and not options.get('monotonic_transition_fallback',False):
        raise ValueError('No feasible equal-step layering under current constraints')
    for count in range(1,len(candidates)+1):
        feasible=[]
        for subset in itertools.combinations(candidates,count):
            result,metadata,_=solve_equal(profile,options,subset)
            if result:
                rows,cost=result;feasible.append(((-len(rows),cost,subset),rows,metadata))
        if feasible:
            _,rows,metadata=min(feasible,key=lambda r:r[0]);return rows,metadata
    raise ValueError('No feasible equal-step layering under current constraints')


# Combined stages, lateral maps and Hypoinverse datum conversion
def validate_workflow_options(cfg):
    m,h=cfg['maps'],cfg['hypoinverse']
    if type(m['enabled']) is not bool or m['interpolation'] not in ('linear','nearest'):
        raise ValueError('maps.enabled must be boolean; interpolation must be linear or nearest')
    positive(m['dpi'],'maps.dpi',True)
    if not isinstance(m['cmap'],str) or not m['cmap']:
        raise ValueError('maps.cmap must be a colormap name')
    if h['model_type'] not in ('CRE','CRH','CRT'):
        raise ValueError('hypoinverse.model_type must be CRE, CRH or CRT')
    if h['surface_elevation_method'] not in ('grid_median','explicit'):
        raise ValueError('surface_elevation_method must be grid_median or explicit')
    for key in ('surface_elevation_km','reference_elevation_km'):
        if h[key] is not None and (type(h[key]) not in (int,float) or not np.isfinite(h[key])):
            raise ValueError(f'{key} must be finite or null')
    if h['surface_elevation_method']=='explicit' and h['surface_elevation_km'] is None:
        raise ValueError('Explicit surface elevation is required')
    for key in ('export_cre','acknowledge_approximate_surface','acknowledge_halfspace_extension','use_station_elevations'):
        if type(h[key]) is not bool: raise ValueError(f'{key} must be boolean')
    for key in ('p_model_number','s_model_number'):
        if type(h[key]) is not int or not 1<=h[key]<=36: raise ValueError(f'{key} must be 1..36')
    if h['p_model_number']==h['s_model_number']: raise ValueError('P and S model numbers must differ')


def sample_cube(cfg):
    path=location(cfg,cfg['average']['input'])
    meta=read_metadata(path)
    data=read_samples(path)
    axes=[np.unique(data[:,k]) for k in range(3)]
    lon,lat,z=axes
    if len(data)!=len(lon)*len(lat)*len(z): raise ValueError('Lateral maps require a complete saved grid')
    order=np.lexsort((data[:,0],data[:,1],data[:,2]))
    cube=data[order,3:5].reshape(len(z),len(lat),len(lon),2)/1000
    return lon,lat,z/1000,cube,meta


def slice_at(z,cube,depth,method):
    if depth<z[0] or depth>z[-1]: raise ValueError('Map depth outside saved grid')
    if method=='nearest':
        i=int(np.argmin(abs(z-depth)));return cube[i],float(z[i])
    j=int(np.searchsorted(z,depth))
    if j==0: return cube[0],float(depth)
    t=(depth-z[j-1])/(z[j]-z[j-1])
    return (1-t)*cube[j-1]+t*cube[j],float(depth)


def maps(cfg):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    lon,lat,z,cube,meta=sample_cube(cfg)
    model=read_metadata(location(cfg,cfg['layers']['output']))
    if model['dense_metadata']['input_sha256']!=meta['artifact_sha256']:
        raise ValueError('Layered model and map grid come from different extractions')
    edges=[model['model_depth_range_km'][0],*[x['depth_km'] for x in model.get('major_interfaces',[])],model['model_depth_range_km'][1]]
    if not model.get('major_interfaces') and cfg['layers'].get('method')=='legacy':
        raise ValueError('Major-layer maps require interface-based layering')
    target=location(cfg,cfg['maps']['output'])
    require_outputs([target,sidecar(target)],cfg['overwrite'])
    slices=[];records=[]
    for top,bottom in zip(edges[:-1],edges[1:]):
        depth=(top+bottom)/2
        values,used=slice_at(z,cube,depth,cfg['maps']['interpolation']);slices.append(values)
        records.append(dict(top_km=top,bottom_km=bottom,midpoint_km=depth,plotted_depth_km=used))
    values=np.array(slices)
    fig,axes=plt.subplots(len(slices),2,figsize=(11,3.3*len(slices)),squeeze=False,layout='constrained')
    for i,(v,r) in enumerate(zip(slices,records)):
        for col,phase in enumerate(('P','S')):
            ax=axes[i,col]
            mesh=ax.pcolormesh(lon,lat,v[:,:,col],shading='nearest',cmap=cfg['maps']['cmap'],vmin=values[:,:,:,col].min(),vmax=values[:,:,:,col].max())
            ax.set_aspect(1/np.cos(np.deg2rad(np.mean(lat))))
            ax.set_xlabel('Longitude (degrees)');ax.set_ylabel('Latitude (degrees)')
            ax.set_title(f"{phase}: {r['top_km']:.1f}–{r['bottom_km']:.1f} km | slice {r['plotted_depth_km']:.2f} km")
            fig.colorbar(mesh,ax=ax,label=f'{phase} velocity (km/s)',shrink=.9)
    fig.suptitle(cfg['case_name']+' | Major-layer midpoint maps\nDepth reference: '+model['depth_reference'].replace('_',' '))
    save_figure(fig,target,cfg['maps']['dpi']);plt.close(fig)
    write_metadata(target,dict(kind='major_layer_maps',layers=records,settings=cfg['maps'],depth_reference=model['depth_reference'],input_sha256=meta['artifact_sha256']))
    return target


def build_1d(cfg):
    # Check every downstream destination before rewriting any stage.
    names=[cfg['average']['output'],cfg['average']['output_full'],cfg['layers']['output'],cfg['layers']['model_output'],cfg['plot']['output']]
    if cfg['plot'].get('gradient_output'): names.append(cfg['plot']['gradient_output'])
    if cfg['maps']['enabled']: names.append(cfg['maps']['output'])
    targets=[location(cfg,n) for n in names]
    targets += [sidecar(location(cfg,n)) for n in (cfg['average']['output'],cfg['layers']['output'])]
    if cfg['maps']['enabled']: targets.append(sidecar(location(cfg,cfg['maps']['output'])))
    require_outputs(targets,cfg['overwrite'])
    protect_input(location(cfg,cfg['average']['input']),targets)
    average(cfg);model=layer(cfg);plot(cfg)
    if cfg['maps']['enabled']: maps(cfg)
    return model


def surface_stats(path):
    points={}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'): continue
        row=line.split()
        if len(row)!=8: raise ValueError('Datum report requires extraction with surface-elevation column')
        key=tuple(map(float,row[:2]));h=float(row[7])
        if not np.isfinite(h): raise ValueError('Nonfinite surface elevation')
        if key in points and abs(points[key]-h)>1e-6: raise ValueError('Surface elevation changes with queried depth')
        points[key]=h
    if not points: raise ValueError('No surface elevation samples')
    v=np.array(list(points.values()))/1000
    return dict(points=len(v),min_km=float(v.min()),max_km=float(v.max()),median_km=float(np.median(v)),mean_km=float(v.mean()))


def cre_tops(layers,offset,depth_reference='local_surface'):
    tops=np.array([r['top_depth_km'] for r in layers],dtype=float)
    if depth_reference=='local_surface' and abs(tops[0])>1e-9:
        raise ValueError('Local-surface CRE export requires original model to start at zero')
    if tops[0]+offset < -1e-9:
        raise ValueError('CRE reference elevation lies below the original model top')
    tops+=offset
    tops[0]=0.  # extend first material only when reference lies above model top
    if np.any(np.diff(np.round(tops,2))<=0): raise ValueError('Datum shift collapses or reverses CRE layer tops')
    return tops


def build_cre_model(cfg):
    h=cfg['hypoinverse']
    source=location(cfg,cfg['average']['input']);meta=read_metadata(source)
    model=read_metadata(location(cfg,cfg['layers']['output']))
    if model['dense_metadata']['input_sha256']!=meta['artifact_sha256']:
        raise ValueError('Model and datum grid come from different extractions')
    stats=surface_stats(source);datum=model['depth_reference']
    H=0. if datum=='sea_level' else (stats['median_km'] if h['surface_elevation_method']=='grid_median' else h['surface_elevation_km'])
    E=h['reference_elevation_km'];offset=None if E is None else E-H
    report=dict(kind='hypoinverse_datum',actual_models=meta['actual_models'],depth_reference=datum,
                grid_surface_elevations=stats,representative_surface_elevation_km=H,
                reference_elevation_km=E,nonfirst_layer_top_shift_km=offset,
                original_model_top_elevation_km=H-model['layers'][0]['top_depth_km'],
                settings=h,input_sha256=meta['artifact_sha256'],
                formula='CRE nonfirst top = original top + reference elevation - representative surface elevation; first top stays zero',
                notes=['Regional local-surface averaging is not equivalent to averaging at a common sea-level depth.',
                       'Grid median is a representative approximation, not a station datum or a uniform ground elevation.',
                       'CVM-H has a native DEM; saved UCVM surface elevation is not independently verified as the CVM-H native surface.',
                       'CRE earthquake depths are sea-level depths: do not apply this model-top shift to located events.',
                       'CRH/CRT GEO T uses an event-dependent station datum; no universal event-depth correction is exported.'])
    target=location(cfg,h['output']);outputs=[target]
    if h['export_cre']:
        if h['model_type']!='CRE': raise ValueError('Only CRE file export is supported; use GEO for CRH/CRT')
        if E is None: raise ValueError('Set reference_elevation_km to cover station/topographic elevations before CRE export')
        if datum=='local_surface' and not h['acknowledge_approximate_surface']:
            raise ValueError('Acknowledge the representative local-surface approximation before CRE export')
        model_top_elevation=H-model['layers'][0]['top_depth_km']
        if E<model_top_elevation-1e-9:
            raise ValueError('Reference elevation must not be below the original model top')
        if model['bottom_policy']=='finite' and not h['acknowledge_halfspace_extension']:
            raise ValueError('CRE extends the last layer as a halfspace; explicit acknowledgement required')
        if len(model['layers'])>20: raise ValueError('Hypoinverse CRE supports at most 20 layers including halfspace')
        for key in ('vp_kmps','vs_kmps'):
            if np.any(abs(np.diff([r[key] for r in model['layers']]))<.01-1e-9):
                raise ValueError('CRE adjacent velocities must differ by at least 0.01 km/s in each phase')
        paths=[location(cfg,h[k]) for k in ('output_p','output_s','commands_output')];outputs+=paths
        tops=cre_tops(model['layers'],offset,datum)
        texts=[]
        for phase,key in [('P','vp_kmps'),('S','vs_kmps')]:
            text=f"{('REGIONAL '+phase+' CRE'):<30}\n"
            for top,row in zip(tops,model['layers']):
                if len(f'{top:5.2f}')>5 or len(f'{row[key]:5.2f}')>5: raise ValueError('CRE value exceeds field width')
                text+=f"{row[key]:5.2f}{top:5.2f}\n"
            texts.append(text)
        flag='T' if h['use_station_elevations'] else 'F'
        texts.append(f"CRE {h['p_model_number']} '{paths[0].as_posix()}' {E:.6f} {flag}\nCRE {h['s_model_number']} '{paths[1].as_posix()}' {E:.6f} {flag}\nSAL {h['p_model_number']} {h['s_model_number']}\n")
        report['exported_cre_layer_tops_km']=tops.tolist()
        report['original_layer_tops_km']=[r['top_depth_km'] for r in model['layers']]
        report['first_layer_extension_km']=float(tops[1]-tops[0]-(model['layers'][1]['top_depth_km']-model['layers'][0]['top_depth_km'])) if len(tops)>1 else None
        report['cre_model_zero_elevation_km']=E
        report['earthquake_depth_reference']='sea_level'
        report['export_bottom_policy']='last_layer_halfspace'
        report['layered_input_sha256']=digest(location(cfg,cfg['layers']['output']))
    require_outputs(outputs,cfg['overwrite'])
    for input_path in (source,location(cfg,cfg['layers']['model_output']),location(cfg,cfg['layers']['output'])):
        protect_input(input_path,outputs)
    if h['export_cre']:
        for path,text in zip(paths,texts): atomic_text(path,text)
    atomic_text(target,json.dumps(report,indent=2,allow_nan=False)+'\n')
    return target


def run_stage(stage, config_path=None):
    if stage not in ('extract', 'build_1d', 'build_cre_model'):
        raise ValueError(f'Unknown velocity-model stage: {stage}')
    if config_path is None:
        # Optional installed shell wrapper; numbered scripts pass their setting.
        parser=argparse.ArgumentParser(description=f'1D velocity model preparation: {stage}')
        parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
        config_path=parser.parse_args().config
    cfg=load_config(config_path)
    result=globals()[stage](cfg)
    print(f'{stage} completed: {result}')
    return result
