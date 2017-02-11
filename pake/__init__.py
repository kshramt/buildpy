import argparse
import os
import subprocess
import sys
import warnings
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Set,
)


__version__ = "0.1.0"


class _Nil:
    __slots__ = ()

    def __contains__(self, x):
        return False


_nil = _Nil()


class Err(Exception):
    def __init__(self, msg=""):
        self.msg=msg


class _Cons:
    __slots__ = ("h", "t")

    def __init__(self, h, t):
        self.h = h
        self.t = t

    def __contains__(self, x):
        return (self.h == x) or (x in self.t)


class _Job:
    def __init__(self, f, ts, ds):
        self.f = f
        self.ts = ts
        self.ds = ds
        self.unique_ds = _unique(ds)
        self.n_rest = len(self.unique_ds)
        self.visited = False

    def rm_targets(self):
        pass

    def need_update(self):
        return True


class _PhonyJob(_Job):
    def __init__(self, f, ts, ds):
        if len(ts) != 1:
            raise Err(f"PhonyJob with multiple targets is not supported: {f}, {ts}, {ds}")
        super().__init__(f, ts, ds)


class _FileJob(_Job):
    def __init__(self, f, ts, ds):
        super().__init__(f, ts, ds)

    def rm_targets(self):
        for t in self.ts:
            rm(t)

    def need_update(self):
        stat_ds = [os.stat(d) for d in self.unique_ds]
        if not all(os.path.lexists(t) for t in self.ts):
            return True
        if not stat_ds:
            return False
        return max(d.st_mtime for d in stat_ds) > max(os.path.getmtime(t) for t in self.ts)


class DSL:
    def __init__(self) -> Any:
        self._job_of_target: Dict[str, _Job] = dict()
        self._f_of_phony: Dict[str, Callable[[_Job], Any]] = dict()
        self._deps_of_phony: Dict[str, List[str]] = dict()
        self._phonies: Set[str] = set()

    def task(self, targets: List[str], deps: List[str]) -> Callable[[_Job], Any]:
        def _(f: Callable[[_Job], Any]) -> Callable[[_Job], Any]:
            j = _FileJob(f, targets, deps)
            for t in targets:
                _set_unique(self._job_of_target, t, j)
            return _do_nothing
        return _

    def phony(self, target: str, deps: List[str]) -> Callable[[Callable[[_Job], Any]], Callable[[_Job], Any]]:
        self._deps_of_phony.setdefault(target, []).extend(deps)
        self._phonies.add(target)
        def _(f: Callable[[_Job], Any]) -> Callable[[_Job], Any]:
            _set_unique(self._f_of_phony, target, f)
            return _do_nothing
        return _

    def finish(
            self,
            targets,
            keep_going,
            n_jobs,
    ):
        assert n_jobs > 0
        _collect_phonies(self._job_of_target, self._deps_of_phony, self._f_of_phony)
        dependent_jobs = dict()
        leaf_jobs = []
        for target in targets:
            _make_graph(
                dependent_jobs,
                leaf_jobs,
                target,
                self._job_of_target,
                self.task,
                self._phonies,
                _nil,
            )
        _process_jobs(leaf_jobs, dependent_jobs, keep_going, n_jobs)

    def main(self, argv):
        args = _parse_argv(argv[1:])
        self.finish(
            args.targets,
            args.keep_going,
            args.jobs,
        )


class _TaskPool:
    def __init__(self, dependent_jobs, keep_going, n_jobs_max):
        assert n_jobs_max > 0
        self._stack = []
        self._tasks = set()
        self._all_tasks = []
        self._defered_errors = []
        self.dependent_jobs = dependent_jobs
        self.keep_going = keep_going
        self.n_jobs_max = n_jobs_max

    def wait_all_tasks(self):
        i = -1
        while True:
            i += 1
            if len(self._all_tasks) < i:
                return

    def push_job(self, j):
        self._stack.append(j)
        while self._stack:
            j = self._stack.pop()
            # Start executing the job
            # `Job` is called only once
            assert j.n_rest == 0
            got_error = False
            if j.need_update():
                try:
                    j.f(j)
                except Exception as e:
                    got_error = True
                    # Use string interpolation for async output
                    warnings.warn(f"{repr(e)}\t{j}")
                    j.rm_targets()
                    if self.keep_going:
                        self._defered_errors.append((j, e))
                    else:
                        raise e
                    j.n_rest = -1
            if not got_error:
                for t in j.ts:
                    # top targets does not have dependent jobs
                    for dj in self.dependent_jobs.setdefault(t, []):
                        dj.n_rest -= 1
                        if dj.n_rest == 0:
                            self.push_job(dj)


def sh(s, stdout=None):
    print(s, file=sys.stderr)
    return subprocess.run(
        s,
        shell=True,
        check=True,
        env=os.environ,
        executable="/bin/bash",
        stdout=stdout,
        universal_newlines=True,
    )


def rm(path):
    print(f"os.remove({repr(path)})", file=sys.stderr)
    try:
        os.remove(path)
    except:
        pass


def _collect_phonies(job_of_target, deps_of_phony, f_of_phony):
    for target, deps in deps_of_phony.items():
        _set_unique(
            job_of_target,
            target,
            _PhonyJob(
                f_of_phony.get(target, _do_nothing),
                [target],
                deps,
            ),
        )


def _make_graph(
        dependent_jobs,
        leaf_jobs,
        target,
        job_of_target,
        make_job,
        phonies,
        call_chain,
):
    if target in call_chain:
        raise Err(f"A circular dependency detected: {repr(target)} for {repr(call_chain)}")
    if target not in job_of_target:
        assert target not in phonies
        if os.path.lexists(target):
            @make_job([target], [])
            def _(j):
                raise Err(f"Must not happen: job for leaf node {repr(target)} called")
        else:
            raise Err(f"No rule to make {repr(target)}")
    j = job_of_target[target]
    if j.visited:
        return
    j.visited = True
    current_call_chain = _Cons(target, call_chain)
    for dep in j.unique_ds:
        dependent_jobs.setdefault(dep, []).append(j)
        _make_graph(
            dependent_jobs,
            leaf_jobs,
            dep,
            job_of_target,
            make_job,
            phonies,
            current_call_chain,
        )
    j.unique_ds or leaf_jobs.append(j)


def _process_jobs(jobs, dependent_jobs, keep_going, n_jobs):
    task_pool = _TaskPool(dependent_jobs, keep_going, n_jobs)
    for j in jobs:
        task_pool.push_job(j)
    task_pool.wait_all_tasks()
    if task_pool._defered_errors:
        warnings.warn("Following errors have thrown during the execution")
        for j, e in task_pool._defered_errors:
            warnings.warn(repr(e))
            warnings.warn(j)
        raise Err("Execution failed.")


def _parse_argv(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "targets",
        nargs="*",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of parallel external jobs.",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        default=False,
        help="Keep going unrelated jobs even if some jobs fail.",
    )
    args = parser.parse_args(argv)
    assert args.jobs > 0
    if not args.targets:
        args.targets.append("default")
    return args


def _set_unique(d, k, v):
    assert k not in d
    d[k] = v


def _unique(xs):
    seen = set()
    ret = []
    for x in xs:
        if x not in seen:
            ret.append(x)
            seen.add(x)
    return ret


def _do_nothing(*_):
    pass
