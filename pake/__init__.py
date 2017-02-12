import argparse
import concurrent.futures
import os
import queue
import subprocess
import sys
import threading
import warnings
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Set,
)


__version__ = "0.1.0"


class Err(Exception):
    def __init__(self, msg=""):
        self.msg=msg


class _Nil:
    __slots__ = ()

    def __contains__(self, x):
        return False


_nil = _Nil()


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
        self._n_rest = len(self.unique_ds)
        self.visited = False
        self._lock = threading.Lock()

    def __str__(self):
        return f"{type(self).__name__}({self.ts}, {self.ds})"

    def rm_targets(self):
        pass

    def need_update(self):
        return True

    def n_rest(self):
        with self._lock:
            return self._n_rest

    def dec_n_rest(self):
        with self._lock:
            self._n_rest -= 1

    def set_n_rest(self, x):
        with self._lock:
            self.n_rest = x


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

    def task(self, targets: List[str], deps: List[str]) -> Callable[[_Job], Any]:
        def _(f: Callable[[_Job], Any]) -> Callable[[_Job], Any]:
            j = _FileJob(f, targets, deps)
            for t in targets:
                _set_unique(self._job_of_target, t, j)
            return _do_nothing
        return _

    def phony(self, target: str, deps: List[str]) -> Callable[[Callable[[_Job], Any]], Callable[[_Job], Any]]:
        self._deps_of_phony.setdefault(target, []).extend(deps)

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
                self._deps_of_phony,
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


def sh(s: str, stdout=None):
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


def rm(path: str) -> Any:
    print(f"os.remove({repr(path)})", file=sys.stderr)
    try:
        os.remove(path)
    except:
        pass


def _collect_phonies(
        job_of_target: Dict[str, _Job],
        deps_of_phony: Dict[str, List[str]],
        f_of_phony: Dict[str, Callable[[_Job], Any]],
) -> Any:
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
        dependent_jobs: Dict[str, List[_Job]],
        leaf_jobs: List[_Job],
        target: str,
        job_of_target: Dict[str, _Job],
        task,
        phonies,
        call_chain,
):
    if target in call_chain:
        raise Err(f"A circular dependency detected: {repr(target)} for {repr(call_chain)}")
    if target not in job_of_target:
        assert target not in phonies
        if os.path.lexists(target):
            @task([target], [])
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
            task,
            phonies,
            current_call_chain,
        )
    j.unique_ds or leaf_jobs.append(j)


def _process_jobs(jobs, dependent_jobs, keep_going, n_jobs):
    tp = _ThreadPool(dependent_jobs, keep_going, n_jobs)
    defered_errors = queue.Queue()
    for j in jobs:
        tp.push_job(j)
    tp.wait()
    if defered_errors.qsize() > 0:
        warnings.warn("Following errors have thrown during the execution")
        for _ in range(defered_errors.qsize()):
            j, e = defered_errors.get()
            warnings.warn(repr(e))
            warnings.warn(j)
        raise Err("Execution failed.")


class _TSet:
    def __init__(self):
        self._lock = threading.Lock()
        self._set = set()

    def __len__(self):
        with self._lock:
            return len(self._set)

    def add(self, x):
        with self._lock:
            self._set.add(x)

    def remove(self, x):
        with self._lock:
            self._set.remove(x)

    def pop(self):
        with self._lock:
            return self._set.pop()


class _ThreadPool:
    def __init__(self, dependent_jobs, keep_going, n_max):
        assert n_max > 0
        self._dependent_jobs = dependent_jobs
        self._keep_going = keep_going
        self._n_max = n_max
        self._threads = _TSet()
        self._threads_loc = threading.Lock()
        self._queue = queue.Queue()

    def push_job(self, j):
        self._queue.put(j)
        with self._threads_loc:
            if len(self._threads) < self._n_max:
                t = threading.Thread(
                    target=self._worker,
                    daemon=True,
                )
                self._threads.add(t)
                t.start()

    def wait(self):
        while True:
            try:
                t = self._threads.pop()
            except KeyError as e:
                break
            t.join()

    def _worker(self):
        try:
            while True:
                j = self._deq()
                if not j:
                    break
                assert j.n_rest() == 0
                got_error = False
                if j.need_update():
                    try:
                        j.f(j)
                    except Exception as e:
                        got_error = True
                        warnings.warn(f"{repr(e)}\t{j}")
                        j.rm_targets()
                        if self._keep_going:
                            defered_errors.put((j, e))
                        else:
                            raise e
                    j.set_n_rest(-1)
                if not got_error:
                    for t in j.ts:
                        # top targets does not have dependent jobs
                        for dj in self._dependent_jobs.get(t, ()):
                            dj.dec_n_rest()
                            if dj.n_rest() == 0:
                                self.push_job(dj)
        finally:
            with self._threads_loc:
                try:
                    self._threads.remove(threading.current_thread())
                except:
                    pass

    def _deq(self):
        try:
            return self._queue.get(block=True, timeout=0.02)
        except queue.Empty:
            return False


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
        "-j", "--jobs",
        type=int,
        default=1,
        help="Number of parallel external jobs.",
    )
    parser.add_argument(
        "-k", "--keep-going",
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
