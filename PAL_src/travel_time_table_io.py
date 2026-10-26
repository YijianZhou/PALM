"""Single-file sparse PAL P/S travel-table reader. Mirrored in both PAL packages."""
import io
import json
import zipfile
import time
from pathlib import Path
import numpy as np


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



class StationPhaseTables:
    """Expose lazy [z,x,y] phase arrays to PAL; unavailable entries are infinity."""
    def __init__(self, reader, stations):
        self.reader, self.stations = reader, stations
        self.cached = {}
        self.load_seconds = 0.0
        self.cells = np.arange(np.prod(reader.shape), dtype=np.int64)

    def phase(self, station, phase):
        if station not in self.cached:
            started = time.monotonic()
            if station in self.stations:
                geometry = tuple(float(v) for v in self.stations[station][:3])
                tp, ts, valid = self.reader.lookup('.'.join(station.split('.')[:2]), self.cells, geometry)
                tp, ts = np.where(valid, tp, np.inf), np.where(valid, ts, np.inf)
            else:
                tp = np.full(self.cells.shape, np.inf)
                ts = tp.copy()
            self.cached[station] = (tp.reshape(self.reader.shape), ts.reshape(self.reader.shape))
            self.load_seconds += time.monotonic() - started
        return self.cached[station][phase]

    def view(self, phase):
        return PhaseView(self, phase)


class PhaseView:
    def __init__(self, owner, phase):
        self.owner, self.phase = owner, phase

    def __getitem__(self, station):
        return self.owner.phase(station, self.phase)

    def values(self):
        for station in self.owner.stations:
            yield self[station]
