# BuildPy

I wrote BuildPy to manage data analysis pipelines.
BuildPy has following features:

- Parallel processing (similar to `-j` option of Make)
- Generation of multiple targets with a single job
- Dry-run (similar to `-n` option of Make)
- Deferred error (similar to `-k` option of Make)
- Description for jobs (similar to `desc` method of Rake)
- Load-average based control of the number of parallel jobs (similar to `-l` option of Make)
- Machine-readable output of the dependency graph (similar to `-P` option of Rake)

BuildPy is available from [PyPI](https://pypi.python.org/pypi/buildpy):

```bash
pip install --user --upgrade buildpy
```

The typical form of `build.py` is as follows:

```py
import sys

import buildpy

dsl = buildpy.DSL()
file = dsl.file
phony = dsl.phony
sh = dsl.sh

phony("all", ["test"])
phony("test", ["main.exe.done"])
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
