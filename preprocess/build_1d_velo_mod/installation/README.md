# UCVM + CVM-S: installation and complete Python workflow

Use one named Conda environment, **ucvm**, for building and using UCVM and for
all processing stages. Its prompt is `(ucvm)`. NumPy and Matplotlib
are installed up front, alongside Perl, certifi, zlib and the compilers.

| Component | New default |
| --- | --- |
| Fresh source checkout | `~/software/ucvm-source-cvms` |
| Permanent software/data prefix | `~/software/ucvm-25.7` |
| Conda environment | Named `ucvm` |
| Query command | `~/bin/ucvm_query` |
| Workflow command | `~/bin/ucvm-model` |
| Case configuration | `../config_eg.json` |

Only **CVM-S4 (`cvms`)** is selected as an optional model. UCVM's core 1D models
and required support data remain. CVMS5, CVMSI and CVMH are different models.
The model choice here is for this minimal installation, not a scientific claim
about the best model for your region. Verify coverage of the requested region.

Do not move binaries or rename the installed tree. The commands in `~/bin` are
launchers that load the runtime in a subprocess; the parent shell is unchanged.
They can be used from base, but the `ucvm` environment must remain installed.

Validate the installation on your own Linux host before processing a region.
The package's synthetic workflow tests do not exercise UCVM installation or
downloaded model data.

## 0. Choose a clean build directory

Use an empty build directory and a separate permanent installation prefix.
Do not delete or overwrite a working installation when testing this example.

Run blocks separately in Bash. Stop at a failed command; do not paste shell
commands while a program is asking yes/no. Use uppercase `$HOME`.

## 1. Create the full environment

Copy this updated wrapper folder to the workstation, including `installation`
and `config_eg.json`. From the wrapper folder:

```bash
conda env create -f installation/environment.yml
conda activate ucvm
```

If `ucvm` already exists, inspect it first instead of overwriting it. Package
choices are in [environment.yml](environment.yml): Python 3.11, GCC/G++/GFortran
16.2, Perl, certifi, zlib, NumPy, Matplotlib, pytest, and build tools. Compiler
package names target Linux x86_64. Exact package builds are not fully locked;
export the solved environment after validation. Keep this toolchain out of base.

```bash
command -v make autoconf automake aclocal libtoolize git tar
perl -MExtUtils::MakeMaker -e 'print "MakeMaker OK\n"'
python -c 'import numpy, matplotlib, certifi; print("Python dependencies OK")'
printf 'CC=%s\nFC=%s\n' "$CC" "$FC"
"$CC" --version
"$FC" --version
ls -l "$CONDA_PREFIX/lib/libz.so"
```

Stop if any required tool/check fails. Conda compiler executable names can be
prefixed; use `$FC`, not an assumption that plain `gfortran` is on PATH. Before
building any dependency, select C17 and position-independent compilation:

```bash
export CFLAGS="${CFLAGS:-} -std=gnu17 -fPIC"
export UCVM_SRC_PATH="$HOME/software/ucvm-source-cvms"
export UCVM_INSTALL_PATH="$HOME/software/ucvm-25.7"
```

Keep the same environment throughout. After opening a new terminal, activate
`ucvm` and repeat these exports. The installed prefix must remain at this path.

## 2. Use a clean v25.7 checkout

Skip the clone if a fresh checkout already exists at the source path above:

```bash
mkdir -p "$HOME/software"
git clone https://github.com/SCECcode/ucvm.git "$UCVM_SRC_PATH"
```

Then:

```bash
cd "$UCVM_SRC_PATH"
git checkout --detach v25.7
git describe --tags --exact-match
git rev-parse HEAD | tee source-revision.txt
df -h "$HOME/software"
```

The exact tested tag includes the `v`: `v25.7`, commit
`e8596694de1fdccb7bdc2942f991972cfba3113f`. Detached HEAD is normal. Choose an
unused installation prefix. If customizing it, also update launcher settings
and `extract.ucvm_conf` in the workflow JSON.

## 3. Download and stage CVM-S only

```bash
cd "$UCVM_SRC_PATH/largefiles"
python get_largefiles.py
```

Answer **yes only for `cvms` / CVM-S4**, and no for all other optional models.
Some shared files may still have names associated with unselected models; do
not delete mandatory/support downloads indiscriminately. After download:

```bash
python check_largefiles_md5.py
```

Verify that `cvms.tar.gz` and required files pass, then:

```bash
python stage_largefiles.py
df -h "$HOME/software"
du -sh "$UCVM_SRC_PATH"
```

A yes answer alone does not prove download success (the old run selected CVMS5
but did not download it). Compressed sizes do not include unpacked builds and
installed copies. This sequence follows
[SCEC's instructions](https://github.com/SCECcode/ucvm/wiki/How-to-Guides).

## 4. Build and install

```bash
cd "$UCVM_SRC_PATH"
set -o pipefail
python -u ucvm_setup.py -p "$UCVM_INSTALL_PATH" 2>&1 | tee installation.log
ucvm_status=${PIPESTATUS[0]}
printf 'Installer exit status: %s\n' "$ucvm_status"
```

Again select **CVM-S4 only**. Require zero status and `Done installing UCVM!`
before proceeding. No `-r` on the fresh build. If it fails, stop and inspect
the installation log: `-r` skips existing component directories even when
their builds failed, so use it only after repairing and installing that component.

Check that the selected environment supplies Perl, certifi, NumPy and zlib.
Keep compiler versions and `-fPIC` consistent across dependencies; inspect
component build logs if a build system ignores these flags.

## 5. Validate the actual installation

```bash
source "$UCVM_INSTALL_PATH/conf/ucvm_env.sh"
printf '%s\n' '-118.0 34.0 1000' |
  "$UCVM_INSTALL_PATH/bin/ucvm_query" \
  -f "$UCVM_INSTALL_PATH/conf/ucvm.conf" -m cvms -c gd
```

Require a `cvms` row with physical P/S values. Installer example rows are not
an executed query. This point does not establish coverage of our region. Then:

```bash
cd "$UCVM_INSTALL_PATH/tests"
set -o pipefail
./run-testing 2>&1 | tee "$UCVM_SRC_PATH/runtime-tests.log"
ucvm_status=${PIPESTATUS[0]}
printf 'Test runner exit status: %s\n' "$ucvm_status"
```

Inspect results as well as exit status. Examples requiring omitted models may
fail; record those as unavailable, not passing. Investigate all core or CVM-S
failures. Previous multi-model success is not a promise that every example
passes under the minimal model selection.

## 6. Publish commands in ~/bin

Return to the wrapper folder with `ucvm` active. Review
[launcher_config.json](launcher_config.json), then:

```bash
python installation/create_launchers.py
export PATH="$HOME/bin:$PATH"
hash -r
```

Add `export PATH="$HOME/bin:$PATH"` once to `~/.bashrc` if needed. Do not add
unconditional Conda activation or old UCVM environment sourcing to every shell.
The generator refuses to overwrite unrelated commands or symlinks.

| Launcher setting | Default / validation |
| --- | --- |
| `ucvm_install_prefix` | `~/software/ucvm-25.7`; requires binary, config and environment script |
| `runtime_prefix` | `null` uses active `CONDA_PREFIX`; requires Conda metadata and Python |
| `bin_dir` | `~/bin`; must differ from the real UCVM bin directory |
| `workflow_dir` | `..`, relative to this settings file; all three scripts required |
| `workflow_config` | `../config_eg.json`; existing case JSON |

Paths expand `~`; relative paths resolve against the settings file. Select other
settings with `--config FILE`. Rerun the generator after changing paths or moving
the wrapper project. It may replace its own marked launchers. It never moves
binaries or data. Test from base too:

```bash
conda deactivate
printf '%s\n' '-118.0 34.0 1000' | "$HOME/bin/ucvm_query" -m cvms -c gd
"$HOME/bin/ucvm-model" extract --help
```

The query launcher supplies the installed UCVM config and runtime library paths.
The workflow launcher also chooses the environment's Python. Base does not need
NumPy/Matplotlib for these commands. Validate launchers before full extraction.

## 7. Execute the complete workflow

Review `config_eg.json`: it queries `~/bin/ucvm_query`, selects `cvms`,
uses the permanent installation config, and writes separate `output/eg` files.
From any shell, including base, run each stage after the previous one succeeds:

```bash
"$HOME/bin/ucvm-model" extract
"$HOME/bin/ucvm-model" build
"$HOME/bin/ucvm-model" cre
```

For another case add `--config /absolute/path/case.json` to every command.
Alternatively, from the wrapper directory:

```bash
conda activate ucvm
python 1_extract_3d.py
python 2_build_1d.py
python 3_build_cre_model.py
```

The numbered scripts expose CONFIG_FILE at the top; the optional wrapper uses
installation/launcher_config.json. Both default to the single `config_eg.json`.
After changing the installation paths or wrapper code, regenerate the
launchers with `python installation/create_launchers.py` while `ucvm` is active.
Additional installed models are allowed; `extract.model` selects the model used.
No association settings or averaging algorithms were changed.

## Preserve evidence and dependencies

After validation, activate `ucvm` and export:

```bash
conda list --explicit > "$UCVM_SRC_PATH/conda-explicit-linux-64.txt"
conda env export > "$UCVM_SRC_PATH/conda-environment.yml"
```

Retain revision, setup manifests, checksums, installed config, compiler flags,
logs and launcher settings. Keep the environment installed while using UCVM.
UCVM and model attribution belong to SCEC and model authors; consult upstream
README/CREDITS/LICENSE. These wrappers prepare 1D models; PAL's separate
`0_build_travel_time_table_eg.py` then calculates the saved travel-time table.

Step 3 writes only a datum report by default. CRE export is opt-in after
reviewing elevations and acknowledgements. For PAL use step 2's
`output/eg/velocity_model_1d.json`, as described in the [workflow guide](../README.md).
