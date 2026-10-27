"""Flat, isotropic, piecewise-constant 1D first arrivals. Units: km, s.

Direct transmitted rays and all admissible head waves above/below endpoints
are considered independently for P and S. No reflected/converted phases.
The archive is directly readable by PAL; model calculation is a separate preprocessing step.
"""
import argparse
import ast
import copy
import csv
import io
import zipfile
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import numpy as np


def positive(value, name, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class LayerModel:
    def __init__(self, tops, bottoms, vp, vs, top_extension="constant_first_layer", bottom_policy="finite"):
        self.tops, self.bottoms, self.vp, self.vs = [np.asarray(a, dtype=float) for a in (tops, bottoms, vp, vs)]
        arrays = (self.tops, self.bottoms, self.vp, self.vs)
        if self.tops.ndim != 1 or not self.tops.size or any(a.ndim != 1 or a.shape != self.tops.shape for a in arrays):
            raise ValueError("Layer arrays must be nonempty and have identical 1D shapes")
        finite_bottoms = np.isfinite(self.bottoms)
        if bottom_policy == "halfspace" and np.isposinf(self.bottoms[-1]):
            finite_bottoms[-1] = True
        if not np.all(finite_bottoms) or any(not np.all(np.isfinite(a)) for a in (self.tops, self.vp, self.vs)):
            raise ValueError("Layer arrays must be nonempty, finite and have identical 1D shapes")
        if self.tops[0] != 0 or np.any(self.bottoms <= self.tops) or not np.allclose(self.tops[1:], self.bottoms[:-1], rtol=0, atol=1e-9):
            raise ValueError("Layers must be contiguous, positive thickness, starting at zero")
        if np.any(self.vs <= 0) or np.any(self.vp <= self.vs):
            raise ValueError("Every layer requires finite Vp > Vs > 0")
        if top_extension not in ("constant_first_layer", "error") or bottom_policy not in ("finite", "halfspace"):
            raise ValueError("Invalid top_extension or bottom_policy")
        self.top_extension, self.bottom_policy = top_extension, bottom_policy

    @classmethod
    def load(cls, path, datum):
        model = read_json(path)
        if (model.get("schema_version") != 1 or model.get("kind") != "layered_1d_velocity_model" or model.get("depth_reference") != "local_surface"
                or model.get("depth_positive") != "down" or model.get("interpolation") != "piecewise_constant"
                or model.get("units") != {"depth": "km", "velocity": "km/s"}):
            raise ValueError("Expected step-2 ground-referenced piecewise-constant model in km and km/s")
        layers = model["layers"]
        # Use the documented model extent for an explicitly infinite final layer.
        bottom = [r["bottom_depth_km"] for r in layers]
        if bottom[-1] is None:
            bottom[-1] = model["model_depth_range_km"][1]
        return cls([r["top_depth_km"] for r in layers], bottom,
                   [r["vp_kmps"] for r in layers], [r["vs_kmps"] for r in layers],
                   datum["top_extension"], datum["bottom_policy"])

    def check_depth(self, depth):
        if not np.isfinite(depth):
            raise ValueError("Depth must be finite")
        if depth < 0 and self.top_extension == "error":
            raise ValueError("Endpoint above model surface: enable constant_first_layer extension")
        if depth > self.bottoms[-1] and self.bottom_policy == "finite":
            raise ValueError("Endpoint below finite model bottom")

    def thickness(self, a, b):
        """Vertical lengths in each layer between endpoints, including extensions."""
        a, b = sorted((float(a), float(b)))
        lo, hi = self.tops.copy(), self.bottoms.copy()
        if self.top_extension == "constant_first_layer":
            lo[0] = -np.inf
        if self.bottom_policy == "halfspace":
            hi[-1] = np.inf
        return np.maximum(0, np.minimum(b, hi) - np.maximum(a, lo))

    def velocity_at(self, depth, v):
        # At an exact interface either adjacent material can carry the ray.
        i = int(np.clip(np.searchsorted(self.tops, depth, side="right") - 1, 0, len(v)-1))
        if i > 0 and depth == self.tops[i]:
            return max(v[i-1], v[i])
        return v[i]

    def first_arrivals(self, distances, source_depth, receiver_depth, phase,
                       distance_tolerance_km=1e-6, max_iterations=80):
        """Vectorized first-arrival times at arbitrary nonnegative offsets.

        Direct-ray root variable u=tan(theta in the fastest traversed layer)
        avoids loss of precision as the ray becomes nearly horizontal.
        Head waves also above both endpoints support velocity inversions.
        """
        self.check_depth(source_depth)
        self.check_depth(receiver_depth)
        if phase not in ("P", "S"):
            raise ValueError("phase must be P or S")
        positive(distance_tolerance_km, "distance_tolerance_km")
        positive(max_iterations, "max_iterations", True)
        x = np.asarray(distances, dtype=float)
        if np.any(~np.isfinite(x)) or np.any(x < 0):
            raise ValueError("Distances must be finite and nonnegative")
        if x.size == 0:
            return x.copy()
        v = self.vp if phase == "P" else self.vs
        h = self.thickness(source_depth, receiver_depth)
        if source_depth == receiver_depth:
            best = x / self.velocity_at(source_depth, v)
        else:
            used = h > 0
            hh, vv = h[used], v[used]
            vmax = vv.max()
            if np.all(vv == vmax):
                best = np.hypot(x, hh.sum()) / vmax
            else:
                ratio = vv / vmax
                hfast = hh[vv == vmax].sum()
                low, high = np.zeros_like(x), x / hfast
                def offset(u):
                    out = np.zeros_like(x)
                    for height, r in zip(hh, ratio):
                        out += height*r*u / np.sqrt(1 + u*u*(1-r*r))
                    return out
                for _ in range(max_iterations):
                    u = (low + high) / 2
                    xx = offset(u)
                    if np.max(abs(xx-x)) <= distance_tolerance_km:
                        break
                    low = np.where(xx < x, u, low)
                    high = np.where(xx < x, high, u)
                else:
                    raise RuntimeError("Direct-ray root did not converge; increase max_iterations")
                best = np.zeros_like(x)
                for height, velocity, r in zip(hh, vv, ratio):
                    best += height*np.sqrt(1+u*u) / (velocity*np.sqrt(1+u*u*(1-r*r)))
        # Refracting material must be strictly faster than every nonzero leg.
        # Both endpoints must approach a boundary from the same side.
        for i, boundary in enumerate(self.tops[1:], start=1):
            if max(source_depth, receiver_depth) <= boundary:
                refractor = v[i]
            elif min(source_depth, receiver_depth) >= boundary:
                refractor = v[i-1]
            else:
                continue
            legs = self.thickness(source_depth, boundary) + self.thickness(receiver_depth, boundary)
            used = legs > 0
            if np.any(v[used] >= refractor):
                continue
            ratio = v[used] / refractor
            cosine = np.sqrt(1-ratio*ratio)
            critical_distance = np.sum(legs[used]*ratio/cosine)
            intercept = np.sum(legs[used]*cosine/v[used])
            head = x/refractor + intercept
            best = np.minimum(best, np.where(x >= critical_distance, head, np.inf))
        return best



def elevation(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError(f"{name} must be explicitly set to finite km above sea level")
    return float(value)


def json_model_surface(path):
    """The JSON carries its datum; runtime CSV/CRE overrides cannot change it."""
    model = read_json(path)
    return elevation(model.get('model_surface_elevation_km'),
                     'JSON model_surface_elevation_km (regenerate step 2 or add its known datum)')


def load_velocity_model(cfg, paths):
    """Normalize preparation-only model formats and capture source provenance."""
    fmt = cfg.get('model_format', 'auto')
    if fmt == 'auto':
        fmt = paths['model_file'].suffix.lower().lstrip('.')
    if fmt not in ('json', 'csv', 'cre'):
        raise ValueError('model_format must be auto (.json/.csv/.cre), json, csv, or cre')
    datum = cfg['datum']
    keys = ['model_file'] + (['model_s_file'] if 'model_s_file' in paths else [])
    hashes = {key: sha256(paths[key]) for key in keys}
    height = cfg.get('model_reference_elevation_km')
    bottom = cfg.get('model_bottom_depth_km')
    if fmt == 'json':
        if height is not None or bottom is not None or 'model_s_file' in paths:
            raise ValueError('JSON uses its embedded datum/layers; leave CSV/CRE-only settings None')
        height = json_model_surface(paths['model_file'])
        model = LayerModel.load(paths['model_file'], datum)
        snapshot = read_json(paths['model_file'])
    else:
        from velocity_model_input import layer_arrays
        height = elevation(height, 'model_reference_elevation_km (model depth zero above MSL)')
        arrays = layer_arrays(fmt, paths['model_file'], paths.get('model_s_file'),
                              bottom, datum['bottom_policy'])
        model = LayerModel(*arrays, datum['top_extension'], datum['bottom_policy'])
        snapshot = dict(kind='normalized_layered_velocity_model', input_format=fmt,
                        units=dict(depth='km', velocity='km/s'), depth_positive='down',
                        depth_reference='model_zero', model_reference_elevation_km=height,
                        bottom_policy=datum['bottom_policy'], layers=[
                            dict(top_depth_km=float(t), bottom_depth_km=float(b) if np.isfinite(b) else None,
                                 vp_kmps=float(p), vs_kmps=float(s))
                            for t,b,p,s in zip(*arrays)])
    for key, digest in hashes.items():
        if sha256(paths[key]) != digest:
            raise RuntimeError('Velocity model changed while reading; use stable input files')
    datum['model_surface_elevation_km'] = height  # Existing solver's model-zero offset.
    model.input_hashes, model.source_snapshot = hashes, snapshot
    return model


def config_literal(node):
    """Read PAL settings without executing workflow imports or arbitrary code."""
    if isinstance(node, ast.Dict):
        return {config_literal(k): config_literal(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, (ast.List, ast.Tuple)):
        return [config_literal(v) for v in node.elts]
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "np"
            and node.func.attr == "arange" and not node.keywords):
        args = [ast.literal_eval(v) for v in node.args]
        return np.arange(*args).tolist()
    return ast.literal_eval(node)


def workflow_grid(path, subnet):
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    values = [n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
              and isinstance(n.targets[0], ast.Attribute) and n.targets[0].attr == "subnet_assoc_params"]
    if len(values) != 1:
        raise ValueError("Workflow config must define one subnet_assoc_params dictionary")
    params = config_literal(values[0])
    out = dict(params["default"])
    out.update(params.get(subnet, {}))
    out.update(params.get(subnet.split("_")[-1], {}))
    return out


def read_stations(path):
    """Retain all distinct NET.STA geometries; record original selectors/epochs."""
    records = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as fp:
        for number, row in enumerate(csv.reader(fp), 1):
            if not row or row[0].lstrip().startswith("#"):
                continue
            parts = row[0].strip().split(".")
            if len(parts) < 2 or not all(parts[:2]) or len(row) < 4:
                raise ValueError(f"Invalid PAL station CSV row {number}")
            lat, lon, ele = map(float, row[1:4])
            if not np.all(np.isfinite([lat, lon, ele])) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError(f"Invalid coordinates on row {number}")
            station = ".".join(parts[:2])
            key = (station, lat, lon, ele)
            record = records.setdefault(key, dict(station_id=station, latitude_deg=lat,
                       longitude_deg=lon, elevation_m=ele, source_rows=[]))
            record["source_rows"].append(dict(line=number, selector=row[0],
                epoch_start=row[7] if len(row) == 9 else None,
                epoch_end=row[8] if len(row) == 9 else None))
    if not records:
        raise ValueError("Station inventory is empty")
    return [records[k] for k in sorted(records)]


def axis(bounds, step, name):
    if len(bounds) != 2 or not np.all(np.isfinite(bounds)) or bounds[1] < bounds[0]:
        raise ValueError(f"{name} must be finite [minimum, maximum]")
    return bounds[0] + np.arange(int(np.floor((bounds[1]-bounds[0])/step + 1e-10))+1)*step


def prepare(config_path, settings=None):
    config_path = Path(config_path).resolve()
    cfg = read_json(config_path) if settings is None else copy.deepcopy(settings)
    base = config_path.parent
    def resolve(value):
        p = Path(value).expanduser()
        return p.resolve() if p.is_absolute() else (base/p).resolve()
    paths = {k: resolve(cfg[k]) for k in ("model_file", "station_file", "output_dir")}
    if cfg.get('model_s_file') is not None:
        paths['model_s_file'] = resolve(cfg['model_s_file'])
    paths["workflow_config"] = resolve(cfg["grid"]["workflow_config"])
    paths["config"] = config_path
    if Path(cfg["table_file"]).name != cfg["table_file"] or not cfg["table_file"].endswith(".paltt"):
        raise ValueError("table_file must be a filename ending in .paltt")
    datum, solver = cfg["datum"], cfg["solver"]
    model = load_velocity_model(cfg, paths)
    if datum["source_depth_reference"] != "mean_sea_level":
        raise ValueError("source_depth_reference must be mean_sea_level for PAL")
    if isinstance(datum["model_surface_elevation_km"], bool) or not np.isfinite(datum["model_surface_elevation_km"]):
        raise ValueError("model_surface_elevation_km must be finite")
    for key in ("max_distance_km", "km_per_degree", "distance_tolerance_km", "max_iterations", "batch_size"):
        positive(solver[key], key, key in ("max_iterations", "batch_size"))
    if solver["distance_metric"] != "pal_local":
        raise ValueError("Only pal_local distance metric is supported")
    stations = read_stations(paths["station_file"])
    settings = workflow_grid(paths["workflow_config"], cfg["grid"]["subnet"])
    grid = cfg["grid"]
    step = grid["xy_grid_deg"] if grid["xy_grid_deg"] is not None else settings["xy_grid"]
    positive(step, "xy_grid_deg")
    margin = grid["xy_margin"] if grid["xy_margin"] is not None else settings["xy_margin"]
    if isinstance(margin, bool) or not np.isfinite(margin) or margin < 0:
        raise ValueError("xy_margin must be finite and nonnegative")
    axes = {}
    bounds = {}
    for short, long in (("lat", "latitude"), ("lon", "longitude")):
        interval = grid[f"{short}_range_deg"]
        if interval is None:
            interval = settings.get(f"{short}_range")
        if interval is None:
            values = [s[f"{long}_deg"] for s in stations]
            padding = (max(values)-min(values))*margin
            interval = [min(values)-padding, max(values)+padding]
        limit = 90 if short == "lat" else 180
        if np.any(np.abs(interval) > limit):
            raise ValueError(f"{short} bounds exceed geographic limits")
        bounds[short] = list(interval)
        axes[short] = axis(interval, step, short)
    axes["depth"] = np.asarray(grid["depths_km"] if grid["depths_km"] is not None else settings["z_grids"], dtype=float)
    if axes["depth"].ndim != 1 or not len(axes["depth"]) or np.any(~np.isfinite(axes["depth"])) or np.any(np.diff(axes["depth"]) <= 0):
        raise ValueError("depths_km must be finite, nonempty and strictly increasing")
    height = datum["model_surface_elevation_km"]
    for z in axes["depth"] + height:
        model.check_depth(z)
    for station in stations:
        model.check_depth(height-station["elevation_m"]/1000)
    return cfg, paths, model, stations, axes, bounds


def horizontal_distance(lon, lat, station, km_per_degree):
    """Exactly PAL's station-latitude local metric, including its 111 km/deg default."""
    dx = km_per_degree*(lon-station["longitude_deg"])*np.cos(np.deg2rad(station["latitude_deg"]))
    dy = km_per_degree*(lat-station["latitude_deg"])
    return np.hypot(dx, dy)


def build(config_path, dry_run=False, settings=None):
    cfg, paths, model, stations, axes, bounds = prepare(config_path, settings)
    shape = (len(axes["depth"]), len(axes["lon"]), len(axes["lat"]))
    summary = dict(grid_shape_z_lon_lat=shape, source_cells=int(np.prod(shape)),
                   station_geometries=len(stations), station_ids=len({s['station_id'] for s in stations}),
                   requested_bounds_deg=bounds, source_depths_msl_km=axes["depth"].tolist())
    print(json.dumps(summary, indent=2), flush=True)
    if dry_run:
        return summary
    model_input_hashes = model.input_hashes
    output = paths["output_dir"]
    if output.exists():
        raise FileExistsError(f"{output} already exists; choose a new output_dir to rebuild")
    output.parent.mkdir(parents=True, exist_ok=True)
    work = output.with_name(output.name+'.building-'+uuid.uuid4().hex)
    work.mkdir()
    np.savez(work/'grid.npz', latitude_deg=axes['lat'], longitude_deg=axes['lon'], source_depth_msl_km=axes['depth'])
    lon, lat = np.meshgrid(axes['lon'], axes['lat'], indexing='ij')
    solver, datum = cfg['solver'], cfg['datum']
    h = datum['model_surface_elevation_km']
    total = 0
    for k, station in enumerate(stations):
        distance = horizontal_distance(lon, lat, station, solver['km_per_degree']).ravel()
        horizontal_ids = np.flatnonzero(distance <= solver['max_distance_km'])
        distance = distance[horizontal_ids]
        indices, ptimes, stimes = [], [], []
        for iz, depth in enumerate(axes['depth']):
            indices.append(iz*lon.size+horizontal_ids)
            for phase, result in (('P', ptimes), ('S', stimes)):
                chunks = [model.first_arrivals(distance[start:start+solver['batch_size']],
                          depth+h, h-station['elevation_m']/1000, phase,
                          solver['distance_tolerance_km'], solver['max_iterations'])
                          for start in range(0, len(distance), solver['batch_size'])]
                # Solve in float64, store relative P/S travel times in float32.
                result.append(np.concatenate(chunks).astype(np.float32)
                              if chunks else np.empty(0, dtype=np.float32))
        station['file'] = f'station_{k:05d}.npz'
        station['covered_source_cells'] = int(len(distance)*shape[0])
        tp, ts = np.concatenate(ptimes), np.concatenate(stimes)
        if not np.all(np.isfinite(tp)) or not np.all(np.isfinite(ts)) or np.any(tp < 0) or np.any(ts < tp):
            raise RuntimeError(f"Invalid P/S first arrivals for {station['station_id']}")
        np.savez_compressed(work/station['file'], cell_index=np.concatenate(indices), tp_sec=tp, ts_sec=ts)
        total += len(tp)
        print(f"{k+1}/{len(stations)} {station['station_id']}: {len(tp)} source cells", flush=True)
    summary.update(covered_source_station_pairs=total, possible_source_station_pairs=int(np.prod(shape))*len(stations))
    model_snapshot = model.source_snapshot
    for key, digest in model_input_hashes.items():
        if sha256(paths[key]) != digest:
            raise RuntimeError('Model JSON/CSV/CRE changed during calculation; rebuild from stable inputs')
    manifest = dict(schema_version=1, kind='pal_sparse_ps_travel_time_table',
                    travel_time_dtype='float32',
                    created_utc=datetime.now(timezone.utc).isoformat(), solver='flat_layers_direct_and_head_waves',
                    units=dict(distance='km', time='s', station_elevation='m'),
                    flatten_order='C: [depth, longitude, latitude]', config=cfg, summary=summary,
                    inputs={k:dict(path=str(v), sha256=sha256(v)) for k,v in paths.items() if k!='output_dir'},
                    model=model_snapshot, stations=stations,
                    missing_policy='absent cell or station: unavailable for BOTH phases',
                    bottom_note='finite excludes propagation below model bottom; halfspace extends last velocity indefinitely')
    write_json(work/'manifest.json', manifest)
    pack_table(work, work/cfg['table_file'])
    # The archive is now complete: transient component files are no longer needed.
    for item in [work/'manifest.json', work/'grid.npz'] + [work/s['file'] for s in stations]:
        item.unlink()
    work.rename(output)
    print(f"Table completed: {output}", flush=True)
    return manifest


def build_workflow(settings, source_file, table_destination, dry_run=False, overwrite_table=False):
    """Build from launcher settings, then atomically publish the association input.

    Paths resolve beside source_file, which is recorded as configuration provenance.
    The build output directory must be new, even when replacing the input table.
    """
    import os
    import shutil
    if not isinstance(dry_run, bool) or not isinstance(overwrite_table, bool):
        raise ValueError('DRY_RUN and OVERWRITE_TABLE must be boolean')
    source_file = Path(source_file).resolve()
    destination = source_file.parent / Path(table_destination).expanduser()
    if destination.exists() and not dry_run and not overwrite_table:
        raise FileExistsError(f'{destination} already exists; set OVERWRITE_TABLE=True to replace it')
    result = build(source_file, dry_run=dry_run, settings=settings)
    if dry_run:
        return result
    source = source_file.parent / Path(settings['output_dir']).expanduser() / settings['table_file']
    if source.resolve() == destination.resolve():
        return result
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.copying-' + uuid.uuid4().hex)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'Association input ready: {destination}', flush=True)
    return result


def pack_table(directory, target):
    """Consolidate an existing completed directory without recomputing rays."""
    directory, target = Path(directory), Path(target)
    if target.exists():
        raise FileExistsError(target)
    manifest = read_json(directory/'manifest.json')
    files = ['manifest.json', 'grid.npz'] + [s['file'] for s in manifest['stations']]
    temporary = target.with_name(target.name+'.building-'+uuid.uuid4().hex)
    # NPZ members are already compressed; STORE avoids double compression.
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for member in files:
            if Path(member).name != member:
                raise ValueError('Invalid table member path')
            archive.write(directory/member, member)
    # A CRC check detects interrupted/corrupt copies before publishing.
    with zipfile.ZipFile(temporary) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f'Corrupt archive member: {bad}')
    temporary.rename(target)
    return target


class TravelTimeTable:
    """Safe sparse lookup. Geometry is (latitude_deg, longitude_deg, elevation_m).

    A missing station/geometry/cell returns NaN P/S times and valid=False.
    A station with multiple geometries requires explicit geometry; never select
    an epoch silently. The caller must select the station geometry for its date.
    """
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.is_archive = self.directory.is_file()
        if self.is_archive:
            with zipfile.ZipFile(self.directory) as archive:
                self.manifest = json.loads(archive.read('manifest.json'))
        else:
            self.manifest = json.loads((self.directory/'manifest.json').read_text(encoding='utf-8'))
        if self.manifest.get('schema_version') != 1 or self.manifest.get('kind') != 'pal_sparse_ps_travel_time_table':
            raise ValueError('Unsupported travel table schema')
        with self._arrays('grid.npz') as grid:
            self.grid = {key:grid[key] for key in grid.files}
        self.shape = tuple(self.manifest['summary']['grid_shape_z_lon_lat'])
        axes = [self.grid[k] for k in ('source_depth_msl_km', 'longitude_deg', 'latitude_deg')]
        if self.shape != tuple(len(a) for a in axes) or any(a.ndim != 1 or not len(a) or not np.all(np.isfinite(a)) or np.any(np.diff(a) <= 0) for a in axes):
            raise ValueError('Invalid table grid axes or shape')
        if self.manifest.get('flatten_order') != 'C: [depth, longitude, latitude]':
            raise ValueError('Unsupported table index order')
        self._cache = None  # Bound memory to one station's sparse arrays.

    def _arrays(self, member):
        # No filesystem extraction; archive members are loaded with pickle disabled.
        if Path(member).name != member or not member.endswith('.npz'):
            raise ValueError('Invalid table member name')
        if self.is_archive:
            with zipfile.ZipFile(self.directory) as archive:
                payload = io.BytesIO(archive.read(member))
            return np.load(payload, allow_pickle=False)
        return np.load(self.directory/member, allow_pickle=False)

    def lookup(self, station_id, cell_indices, geometry=None):
        cells = np.asarray(cell_indices)
        if cells.dtype.kind not in 'iu' or np.any(cells < 0) or np.any(cells >= np.prod(self.shape)):
            raise ValueError('cell_indices must be valid flattened integer source indices')
        records = [s for s in self.manifest['stations'] if s['station_id'] == station_id]
        if geometry is not None:
            records = [s for s in records if tuple(s[k] for k in ('latitude_deg','longitude_deg','elevation_m')) == tuple(geometry)]
        if len(records) > 1:
            raise ValueError('Station has multiple geometries; provide the epoch-resolved geometry')
        p, s = np.full(cells.shape, np.nan), np.full(cells.shape, np.nan)
        if records:
            file = records[0]['file']
            if self._cache is None or self._cache[0] != file:
                with self._arrays(file) as data:
                    ids, pt, st = (data[k] for k in ('cell_index', 'tp_sec', 'ts_sec'))
                    if (ids.ndim != 1 or ids.dtype.kind not in 'iu' or pt.shape != ids.shape or st.shape != ids.shape
                            or np.any(ids < 0) or np.any(ids >= np.prod(self.shape)) or np.any(np.diff(ids) <= 0)
                            or not np.all(np.isfinite(pt)) or not np.all(np.isfinite(st)) or np.any(pt < 0) or np.any(st < pt)):
                        raise ValueError('Invalid sparse P/S station table')
                    self._cache = (file, ids, pt, st)
            _, ids, pt, st = self._cache
            pos = np.searchsorted(ids, cells)
            if len(ids):
                safe = np.minimum(pos, len(ids)-1)
                valid = (pos < len(ids)) & (ids[safe] == cells)
                p = np.where(valid, pt[safe], p)
                s = np.where(valid, st[safe], s)
        valid = np.isfinite(p) & np.isfinite(s)
        return p, s, valid


def plot(config_path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator
    from matplotlib.colors import Normalize
    config_path = Path(config_path).resolve()
    cfg = read_json(config_path)
    directory = (config_path.parent/Path(cfg['output_dir']).expanduser()).resolve()
    table = TravelTimeTable(directory/cfg["table_file"])
    settings = cfg['plot']
    positive(settings['dpi'], 'plot.dpi', True)
    if not np.isfinite(settings['source_depth_km']):
        raise ValueError('plot.source_depth_km must be finite')
    velocities = np.asarray(settings['homogeneous_velocities_kmps'], dtype=float)
    if velocities.shape != (2,) or not np.all(np.isfinite(velocities)) or not velocities[0] > velocities[1] > 0:
        raise ValueError('Plot homogeneous velocities require [Vp, Vs], Vp > Vs > 0')
    records = table.manifest['stations']
    if settings['station_id'] is not None:
        records = [s for s in records if s['station_id'] == settings['station_id']]
        if not records:
            raise ValueError('plot.station_id is absent from table')
    # Select a representative record nearest the grid center; geometry shown on plot.
    lat, lon = table.grid['latitude_deg'], table.grid['longitude_deg']
    station = min(records, key=lambda s: (s['latitude_deg']-np.mean(lat))**2+(s['longitude_deg']-np.mean(lon))**2)
    iz = int(np.argmin(abs(table.grid['source_depth_msl_km']-settings['source_depth_km'])))
    depth = table.grid['source_depth_msl_km'][iz]
    xx, yy = np.meshgrid(lon, lat, indexing='ij')
    cells = iz*xx.size+np.arange(xx.size)
    geom = tuple(station[k] for k in ('latitude_deg','longitude_deg','elevation_m'))
    p, s, valid = table.lookup(station['station_id'], cells, geom)
    distance = horizontal_distance(xx, yy, station, table.manifest['config']['solver']['km_per_degree']).ravel()
    order = np.flatnonzero(valid)[np.argsort(distance[valid])]
    fig, axs = plt.subplots(2, 2, figsize=(12, 9), constrained_layout=True)
    for col, (phase, times, velocity) in enumerate(zip(('P','S'), (p,s), velocities)):
        homogeneous = np.hypot(distance, depth+station['elevation_m']/1000)/velocity
        axs[0,col].plot(distance[order], times[order], label='Layered first arrival')
        axs[0,col].plot(distance[order], homogeneous[order], '--', label='Homogeneous')
        axs[0,col].set(xlabel='Epicentral distance (km)', ylabel=f'{phase} time (s)')
        axs[0,col].legend(); axs[0,col].grid(alpha=.25)
        delta = (times-homogeneous).reshape(xx.shape)
        span = np.nanmax(abs(delta)) if np.any(valid) else 1.0
        span = max(float(span), np.finfo(float).eps)
        image = axs[1,col].pcolormesh(xx, yy, delta, shading='auto', cmap='coolwarm',
                                    norm=Normalize(-span, span))
        axs[1,col].plot(station['longitude_deg'], station['latitude_deg'], 'k^')
        axs[1,col].set(xlabel='Longitude (degrees)', ylabel='Latitude (degrees)', title=f'{phase}: layered minus homogeneous')
        axs[1,col].xaxis.set_major_locator(MaxNLocator(nbins=5))
        fig.colorbar(image, ax=axs[1,col], label='Seconds')
    fig.suptitle(f"{station['station_id']} ({geom[0]:.4f}, {geom[1]:.4f}, {geom[2]:.1f} m)\nSource depth {depth:g} km below MSL; blank cells are outside coverage")
    target = directory/settings['output']
    fig.savefig(target, dpi=settings['dpi']); plt.close(fig)
    print(f'Plot completed: {target}')
    return target


def main(stage):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config_cent_cal.json'))
    if stage == 'build':
        parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if stage == 'build':
        build(args.config, args.dry_run)
    else:
        plot(args.config)
