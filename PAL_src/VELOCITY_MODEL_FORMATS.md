# Velocity Model Input Formats

These settings belong only to the travel-time preparation launcher
(`0_build_travel_time_table_eg.py`, or a case equivalent).
Runtime PAL/AI-PAL association still receives only `travel_time_table`, a saved
`.paltt` path. No additional runtime configuration is required, and UCVM/CVM
extraction is optional: you may construct a model from any appropriate source.

## Settings

| Launcher setting | Default | Meaning |
| --- | --- | --- |
| `MODEL_FILE` | `input/velocity_model_1d.json` | JSON, combined CSV, or P CRE file |
| `MODEL_FORMAT` | `'auto'` | Infer from .json/.csv/.cre, or explicitly select json/csv/cre for another suffix |
| `MODEL_S_FILE` | `None` | Required S CRE file for cre; leave None for json/csv |
| `MODEL_REFERENCE_ELEVATION_KM` | `None` | Required finite CSV/CRE model depth-zero elevation above mean sea level, km; may be negative |
| `MODEL_BOTTOM_DEPTH_KM` | `None` | CSV/CRE finite model bottom, km below model zero; must exceed the deepest layer top |
| `BOTTOM_POLICY` | `'finite'` | finite requires an explicit CSV/CRE bottom; halfspace extends the last velocities indefinitely |
| `TOP_EXTENSION` | `'constant_first_layer'` | Extend first material above model zero; error rejects endpoints above it |

All paths resolve relative to the launcher (or standalone builder config).
For JSON, leave MODEL_S_FILE, MODEL_REFERENCE_ELEVATION_KM and
MODEL_BOTTOM_DEPTH_KM as None; the model owns its datum and layer extents.
For CSV/CRE with halfspace, MODEL_BOTTOM_DEPTH_KM may be None.
A finite value, if supplied, must still exceed the deepest top; halfspace
continues to extend the last layer beyond it.

## JSON

The existing schema is unchanged: schema_version=1,
kind=layered_1d_velocity_model, depth_reference=local_surface,
depth_positive=down, interpolation=piecewise_constant, units of km and km/s,
finite model_surface_elevation_km, and explicit contiguous layers starting at
zero with top_depth_km, bottom_depth_km, vp_kmps and vs_kmps.
The supplied preprocessing workflow produces this format, but it is not the
only supported source of velocity models.

## CSV: Both Velocities in One File

Layer-top depths in km below the model's depth zero, with Vp and Vs in km/s:

```csv
top_depth_km,vp_kmps,vs_kmps
0,6.0,3.0
10,8.0,4.0
```

The header is optional. Recognized header aliases are depth_km for
top_depth_km and vp/vs for vp_kmps/vs_kmps. An optional fourth column, vp_vs,
must agree with Vp/Vs within 0.001 (allowing a three-decimal ratio).
Blank lines and whole-line # comments are allowed. No column reordering or
automatic conversion from meters/m/s is performed. Depths must start at zero,
be finite and strictly increasing; velocities must satisfy finite Vp > Vs > 0.
Rows specify constant layers, not samples of a linearly interpolated gradient.
Intermediate bottoms are the next tops; supply the final bottom explicitly
unless using halfspace.

```python
MODEL_FILE = 'input/velocity_model_example.csv'
MODEL_FORMAT = 'csv'
MODEL_S_FILE = None
MODEL_REFERENCE_ELEVATION_KM = 0.0  # Example only: model zero at sea level.
MODEL_BOTTOM_DEPTH_KM = 50.0
BOTTOM_POLICY = 'finite'
```

## CRE: Separate P and S Files

Provide a Hypoinverse CRE velocity-model file per phase, not a Hypoinverse
command file. Each may have one text title followed by velocity and layer-top
depth in that order. Whitespace-separated numbers and two fixed-width F5.2
fields are accepted. Units are km/s and km, respectively.

P file:
```text
Example P model
 6.00 0.00
 8.0010.00
```

S file:
```text
Example S model
 3.00 0.00
 4.0010.00
```

Both profiles must start at zero and use the **same depth-zero datum**.
Different layer-top lists are supported: their union preserves the original
piecewise-constant P and S profiles. Vp > Vs > 0 is checked on every resulting
interval. Vs is not inferred from P using an assumed ratio.

```python
MODEL_FILE = 'input/velocity_p_example.cre'
MODEL_FORMAT = 'cre'
MODEL_S_FILE = 'input/velocity_s_example.cre'
MODEL_REFERENCE_ELEVATION_KM = 0.0  # Replace with the actual CRE reference elevation.
MODEL_BOTTOM_DEPTH_KM = None
BOTTOM_POLICY = 'halfspace'  # CRE's deepest velocity continues indefinitely.
```

For a finite calculation, set BOTTOM_POLICY='finite' and an explicit
MODEL_BOTTOM_DEPTH_KM greater than all P/S layer tops.

## Datum and Provenance

For model-zero elevation H above mean sea level, source depth d below mean sea
level and station elevation e above mean sea level, the solver uses source
depth d+H and receiver depth H-e within the model. Station file elevations are
converted from meters to km. For an exported CRE, H is the CRE reference
elevation, **not** the original regional ground elevation. Do not apply the
CRE export's depth shift again. JSON continues to use its embedded surface
elevation.

The .paltt manifest retains a normalized CSV/CRE model, resolved settings,
and checksums of both CRE inputs. An input change during calculation aborts
publication. Existing tables and the runtime reader contract are unchanged.
Run DRY_RUN=True to check format/datum/grid coverage without creating files,
then False with a new OUTPUT_DIR to build. An example passing validation is
not proof that its velocities are appropriate for a real station network.
