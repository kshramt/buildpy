# pake

I wrote pake to manage data analysis pipelines.
pake supports following features:

- [x] Parallel processing (similar to `-j` option of Make)
- [ ] Dry-run (similar to `-n` option of Make)
- [x] Deferred error (similar to `-k` option of Make)
- [x] Description for jobs (similar to `desc` method of Rake)
- [ ] Load-average based control of number of parallel jobs (similar to `-l` option of Make)
- [x] Machine-readable output of the dependency graph (similar to `-P` option of Rake)

Please see [`./pakefile.py`](./pakefile.py) and `pake/v*/tests/*.sh` for examples.
