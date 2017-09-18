# BuildPy

[![Build Status](https://travis-ci.org/kshramt/buildpy.svg?branch=master)](https://travis-ci.org/kshramt/buildpy)

BuildPy is a build tool to manage a data analysis pipeline.
It has following features:

- Parallel processing (similar to the `-j` option of Make)
- Correct handling of generation of multiple output files from a single command invocation
- Dry-run (similar to the `-n` option of Make)
- Option to enable content-based update scheme (similar to SCons)
- Deferred error (similar to the `-k` option of Make)
- Descriptions for jobs (similar to the `desc` method of Rake)
- Load-average based control of the number of parallel jobs (similar to the `-l` option of Make)
- Machine-readable output of the dependency graph (similar to the `-P` option of Rake)

BuildPy supports Python version ≥ 3.6 and is available from [PyPI](https://pypi.python.org/pypi/buildpy):

```bash
pip install --user --upgrade buildpy
```

The typical form of `build.py` is as follows:

```bash
python build.py all --jobs="$(nproc)" --keep-going
```

```py
import sys

import buildpy.vx

dsl = buildpy.vx.DSL()
# dsl = buildpy.DSL(use_hash=True) # use content-based update scheme
file = dsl.file
phony = dsl.phony
sh = dsl.sh

phony("all", ["test"])
phony("test", ["main.exe.log1", "main.exe.log2"])
@file(["main.exe.log1", "main.exe.log2"], ["main.exe"])
def _(j):
    # j.ts: list of targets
    # j.ds: list of dependencies
    sh(f"./{j.ds[0]} 1> {j.ts[0]} 2> {j.ts[1]}")

phony("all", ["build"])
phony("build", ["main.exe"])

@file("main.exe", ["main.c"])
def _(j):
    sh(f"gcc -o {j.ts[0]} {j.ds[0]}")

if __name__ == '__main__':
    dsl.main(sys.argv)
```

Please see [`./build.py`](./build.py) and `buildpy/v*/tests/*.sh` for more examples.

## Usage

After importing the `buildpy` module, please make a DSL instance by `dsl = buildpy.DSL()`.
The instance, `dsl`, provides methods to construct a dependency graph and to execute the declared jobs.
`dsl.file` is used to declare the dependencies and the command to make target files.
`dsl.file` is used as follows:

```py
# Make `target` from `dep1` and `dep2` by `cat dep1 dep2 >| target`.
# You are able to pass a description of the job via the `desc` optional argument.
@dsl.file("target", ["dep1", "dep2"], desc="Optional description argument")
def _(job):
    dsl.sh(f"cat {' '.join(job.ds)} >| {job.ts[0]}")

# You are able to declare a job to make multiple outputs via a single command invocation.
# In the following example, `target1` and `target2` are made by `diff dep1 dep2 1>| target1 2>| target2`.
@dsl.file(["target1", "target2"], ["dep1", "dep2"])
def _(job):
    dsl.sh(f"diff {' '.join(job.ds)} 1>| {job.ts[0]} 2>| {job.ts[1]}")
```

Like the `task` method of Rake or `.PHONY` rule of Make, you are able to declare a job, which does not produce target files, by using `dsl.phony`.
`dsl.phony` is used as follows:

```py
# Make a phony target named `taregetA`, which depends on `dep1` and `dep2`.
# An invocation of `targetA` executes the decorated method, `_`, and prints `targetA invoked.`
@dsl.phony("targetA", ["dep1", "dep2"], desc="Optional description argument")
def _(job):
    print(job.ds[0] + " invoked.")

# Make a phony target named `taregetB`, which depends on `dep3` and `dep4`.
# An invocation of `targetB` executes no command.
dsl.phony("targetB", ["dep3", "dep4"])

# You are able to append dependencies by declaring `dsl.phony` without a decoration.
# Following code appends `dep5` to the dependencies of `targetA`.
dsl.phony("targetA", ["dep5"])
```

The phony target named `all` is invoked if no target is specified on the command line.
If you want to make `libfinalproduct.so` by default, please add the following line to your `build.py`:

```py
dsl.phony("all", ["libfinalproduct.so"])
```

To execute the declared jobs, please add the following line to your `build.py`:

```py
dsl.main(sys.argv)
```

## Development

### Release of a new version

Add `"buildpy.v9"` to `setup.py`.

```bash
cd buildpy
mv v9 v10
cp -a vx v9
cd v9
grep -l buildpy.vx -R . | xargs -n1 sed -i'' -e 's/buildpy.vx/buildpy.v9/g'
find . -type f | grep -v done | xargs git add
cd ../..
python build.py sdist
```
