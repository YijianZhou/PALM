"""Read-only float32 travel-time storage shared by PAL association workers."""
import errno
import hashlib
import inspect
import io
import math
import os
import pickle
import shutil
from pathlib import Path

import numpy as np

def _attach_tables(engine, descriptor):
    arrays = np.load(descriptor['path'], mmap_mode='r', allow_pickle=False)
    engine.tt_dict = {station: arrays[i, 0] for i, station in enumerate(descriptor['stations'])}
    engine.tt_s_dict = {station: arrays[i, 1] for i, station in enumerate(descriptor['stations'])}
    engine._shared_table_array = arrays
    engine._shared_table_path = descriptor['path']
    # Saved .paltt readers otherwise retain a second, private dense cache.
    engine.__dict__.pop('_station_tables', None)
    engine.__dict__.pop('_table_reader', None)

def _write_table_file(path, engine, names):
    """Allocate through checked writes, never writable mmap page faults.

    Extending a mapped file does not reserve its backing storage. A full tmpfs
    can kill the process with SIGBUS on assignment instead of raising ENOSPC.
    Publish only after all bytes have been written and synced successfully.
    """
    first = engine.tt_dict[names[0]]
    storage_dtype = np.dtype(np.float32)
    shape = (len(names), 2) + first.shape
    header = io.BytesIO()
    np.lib.format.write_array_header_2_0(header, dict(
        descr=np.lib.format.dtype_to_descr(storage_dtype),
        fortran_order=False, shape=shape))
    required = len(header.getvalue()) + math.prod(shape) * storage_dtype.itemsize
    directory = os.path.dirname(path)
    try:
        free = shutil.disk_usage(directory).free
        if required > free:
            raise OSError(errno.ENOSPC, 'requires {} bytes; {} bytes free'.format(required, free))
        with open(path, 'wb') as output:
            output.write(header.getvalue())
            for station in names:
                for tables in (engine.tt_dict, engine.tt_s_dict):
                    data = np.ascontiguousarray(tables[station], dtype=storage_dtype)
                    if data.shape != first.shape:
                        raise ValueError('Inconsistent travel-time table shape for ' + station)
                    raw = memoryview(data).cast('B')
                    if output.write(raw) != raw.nbytes:
                        raise OSError(errno.EIO, 'Short travel-time table write')
                owner = getattr(engine, '_station_tables', None)
                if owner is not None:
                    owner.cached.clear()
            output.flush()
            os.fsync(output.fileno())
        if os.path.getsize(path) != required:
            raise OSError(errno.EIO, 'Incomplete travel-time table file')
    except BaseException as exc:
        if os.path.exists(path):
            os.remove(path)
        if isinstance(exc, OSError):
            raise OSError(exc.errno, (
                'Cannot publish shared PAL table in {} ({} bytes required). '
                'Check free space/quota and set ASSOCIATION_TABLE_DIR to a local directory '
                'with sufficient capacity before launching. Cause: {}'
            ).format(directory, required, exc)) from exc
        raise


def validate_table_dir(value):
    if not isinstance(value, (str, os.PathLike)) or not os.fspath(value).strip():
        raise ValueError("association_table_dir must be a nonempty directory path")
    return os.path.abspath(os.path.expanduser(os.fspath(value)))


def _table_identity(stations, params):
    from associator_pal import PS_Pair_Assoc
    bound = inspect.signature(PS_Pair_Assoc).bind(stations, **params)
    bound.apply_defaults()
    values = bound.arguments
    table = values["travel_time_table"]
    if isinstance(table, (float, np.floating)) and np.isnan(table):
        table = None
    if table is not None:
        path = Path(table).resolve()
        stat = path.stat()
        model = ("saved", str(path), stat.st_size, stat.st_mtime_ns)
    else:
        model = ("homogeneous", *(values[k] for k in (
            "xy_margin", "xy_grid", "z_grids", "velo_mod", "lat_range", "lon_range")))
    geometry = [(name, tuple(float(v) for v in stations[name][:3]))
                for name in sorted(stations)]
    # Gains and association thresholds do not change travel times.
    return hashlib.sha256(pickle.dumps(("float32-v1", geometry, model))).hexdigest()


def shared_associator(stations, params, directory, lock):
    """Publish lazily under a process lock; attach with fresh validated runtime settings."""
    from associator_pal import PS_Pair_Assoc
    key = _table_identity(stations, params)
    descriptor_path = Path(directory) / (key + ".pkl")
    with lock:
        if descriptor_path.exists():
            with descriptor_path.open("rb") as stream:
                descriptor = pickle.load(stream)
        else:
            engine = PS_Pair_Assoc(stations, **params)
            names = tuple(sorted(stations))
            path = str(Path(directory) / (key + ".npy"))
            _write_table_file(path, engine, names)
            descriptor = dict(path=path, stations=names, grid={
                field: getattr(engine, field)
                for field in ("lat_min", "lon_min", "xy_grid", "z_grids")})
            partial = descriptor_path.with_suffix(".pkl.partial")
            with partial.open("wb") as stream:
                pickle.dump(descriptor, stream, protocol=pickle.HIGHEST_PROTOCOL)
                stream.flush()
                os.fsync(stream.fileno())
            partial.replace(descriptor_path)
            del engine

    class MappedAssociator(PS_Pair_Assoc):
        def calc_tt(self):
            self.__dict__.update(descriptor["grid"])
            _attach_tables(self, descriptor)
            return self.tt_dict

    return MappedAssociator(stations, **params)

