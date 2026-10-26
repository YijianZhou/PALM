"""Publish Linux runtime launchers after UCVM installation; never move binaries."""
import argparse
import json
import os
from pathlib import Path
import shlex


MARKER = '# PAL UCVM launcher v1'


def path_value(value, directory):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else directory / path).resolve()


def generate(config_path, runtime=None):
    config_path = Path(config_path).resolve()
    cfg = json.loads(config_path.read_text())
    expected = {'ucvm_install_prefix', 'runtime_prefix', 'bin_dir',
                'workflow_dir', 'workflow_config'}
    if set(cfg) != expected:
        raise ValueError(f'Launcher config must contain exactly {sorted(expected)}')
    for key in expected - {'runtime_prefix'}:
        if not isinstance(cfg[key], str) or not cfg[key].strip():
            raise ValueError(f'{key} must be a nonempty path')
    if cfg['runtime_prefix'] is not None and (
            not isinstance(cfg['runtime_prefix'], str) or not cfg['runtime_prefix'].strip()):
        raise ValueError('runtime_prefix must be null or a nonempty path')
    runtime_value = cfg['runtime_prefix'] or runtime or os.environ.get('CONDA_PREFIX')
    if not isinstance(runtime_value, str) or not runtime_value.strip():
        raise ValueError('Activate conda environment ucvm or set runtime_prefix explicitly')
    root = config_path.parent
    prefix = path_value(cfg['ucvm_install_prefix'], root)
    env = path_value(runtime_value, root)
    bindir = path_value(cfg['bin_dir'], root)
    workflow = path_value(cfg['workflow_dir'], root)
    workflow_config = path_value(cfg['workflow_config'], root)
    required = [prefix/'bin/ucvm_query', prefix/'conf/ucvm.conf',
                prefix/'conf/ucvm_env.sh', env/'bin/python', workflow_config]
    stages = {'extract': '1_extract_3d.py', 'build': '2_build_1d.py',
              'cre': '3_build_cre_model.py'}
    required += [workflow/name for name in stages.values()]
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(f'Required file missing: {path}')
    if os.name == 'posix':
        for path in (prefix/'bin/ucvm_query', env/'bin/python'):
            if not os.access(path, os.X_OK):
                raise PermissionError(f'Not executable: {path}')
    if not (env/'conda-meta').is_dir():
        raise ValueError('runtime_prefix must be an installed Conda environment')
    if bindir == prefix/'bin':
        raise ValueError('Launcher directory must differ from the real UCVM bin directory')
    q = lambda path: shlex.quote(str(path).replace('\\', '/') if os.name == 'nt' else str(path))
    common = f'''#!/bin/bash
{MARKER}
set -e
pal_ucvm_prefix={q(str(prefix))}
pal_ucvm_runtime={q(str(env))}
if [ ! -f "$pal_ucvm_prefix/conf/ucvm_env.sh" ] || [ ! -x "$pal_ucvm_runtime/bin/python" ]; then
  printf '%s\\n' 'UCVM installation/runtime missing. Restore original paths or regenerate launchers.' >&2
  exit 1
fi
export UCVM_INSTALL_PATH="$pal_ucvm_prefix"
source "$pal_ucvm_prefix/conf/ucvm_env.sh"
# Confine runtime paths to this process; the parent shell/base stays unchanged.
export PATH="$pal_ucvm_runtime/bin:$PATH"
export LD_LIBRARY_PATH="$pal_ucvm_runtime/lib${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
'''
    query = common + '''exec "$pal_ucvm_prefix/bin/ucvm_query" -f "$pal_ucvm_prefix/conf/ucvm.conf" "$@"
'''
    model = common + '''if [ "$#" -eq 0 ]; then
  printf '%s\\n' 'Usage: ucvm-model {extract|build|cre} [--config FILE]' >&2
  exit 2
fi
pal_ucvm_stage="$1"
shift
case "$pal_ucvm_stage" in
'''
    stage_functions = {'extract': 'extract', 'build': 'build_1d', 'cre': 'build_cre_model'}
    for stage, function in stage_functions.items():
        model += f'  {stage}) pal_ucvm_function={q(function)} ;;\n'
    entry = 'import sys; sys.path.insert(0, sys.argv.pop(1)); from velocity_model import run_stage; run_stage(sys.argv.pop(1))'
    model += f'''  *) printf '%s\\n' "Unknown stage: $pal_ucvm_stage" >&2; exit 2 ;;
esac
exec "$pal_ucvm_runtime/bin/python" -E -c {q(entry)} {q(str(workflow))} "$pal_ucvm_function" --config {q(str(workflow_config))} "$@"
'''
    outputs = {bindir/'ucvm_query': query, bindir/'ucvm-model': model}
    # Check both destinations before writing either; never overwrite unrelated tools.
    for path in outputs:
        if path.is_symlink() or (path.exists() and
                (not path.is_file() or MARKER not in path.read_text(errors='replace').splitlines()[:3])):
            raise FileExistsError(f'Refusing to replace unrelated destination: {path}')
    bindir.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        with path.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
        path.chmod(0o755)
        print(f'Created {path}')
    return outputs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path,
                        default=Path(__file__).with_name('launcher_config.json'))
    generate(parser.parse_args().config)
