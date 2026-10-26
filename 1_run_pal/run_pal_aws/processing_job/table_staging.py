"""Stage locally built tables without importing scientific workflow configs."""
import ast
import hashlib
import shutil
from pathlib import Path, PurePosixPath


def stage_table_config(config_file, staged_config, table_dir, remote_dir):
    """Copy explicit table paths and rewrite only the uploaded config copy.

    Relative paths are relative to the executable/config folder. Table values
    must be literal strings or None, matching the packaged user configs.
    """
    config_file = Path(config_file)
    tree = ast.parse(config_file.read_text(encoding='utf-8-sig'))
    copies = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or key.value != 'travel_time_table':
                continue
            if not isinstance(value, ast.Constant) or not (
                    value.value is None or isinstance(value.value, str)):
                raise ValueError('travel_time_table must be a literal path string or None')
            if value.value is None:
                continue
            path = Path(value.value).expanduser()
            if not path.is_absolute():
                path = config_file.parent / path
            path = path.resolve()
            if path.suffix.lower() != '.paltt' or not path.is_file():
                raise FileNotFoundError('Build the configured .paltt locally first: {}'.format(path))
            name = hashlib.sha256(str(path).encode()).hexdigest()[:16] + '.paltt'
            copies[path] = name
            value.value = str(PurePosixPath(remote_dir) / name)
    if copies:
        Path(table_dir).mkdir(parents=True, exist_ok=True)
        for path, name in copies.items():
            shutil.copy2(path, Path(table_dir) / name)
        Path(staged_config).write_text(ast.unparse(tree) + '\n', encoding='utf-8')
    else:
        shutil.copy2(config_file, staged_config)
    return len(copies)
