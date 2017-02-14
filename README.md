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

Please see [`./build.py`](./build.py) and `buildpy/v*/tests/*.sh` for examples.
