import _thread
import argparse
import math
import os
import queue
import subprocess
import sys
import threading
import time
import warnings

version = "1.1.0"


class DSL:
    @staticmethod
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

    @staticmethod
    def rm(path):
        print(f"os.remove({repr(path)})", file=sys.stderr)
        try:
            os.remove(path)
        except:
            pass

    def __init__(self):
        self._job_of_target = dict()
        self._f_of_phony = dict()
        self._deps_of_phony = dict()
        self._descs_of_phony = dict()

    def file(self, targets, deps, desc=None):
        targets = _listize(targets)
        deps = _listize(deps)

        def _(f):
            j = _FileJob(f, targets, deps, [desc])
            for t in targets:
                _set_unique(self._job_of_target, t, j)
            return _do_nothing
        return _

    def phony(self, target, deps, desc=None):
        self._deps_of_phony.setdefault(target, []).extend(deps)
        self._descs_of_phony.setdefault(target, []).append(desc)

        def _(f):
            _set_unique(self._f_of_phony, target, f)
            return _do_nothing
        return _

    def finish(self, args):
        assert args.jobs > 0
        assert args.load_average > 0
        _collect_phonies(self._job_of_target, self._deps_of_phony, self._f_of_phony, self._descs_of_phony)
        if args.descriptions:
            _print_descriptions(self._job_of_target)
        elif args.dependencies:
            _print_dependencies(self._job_of_target)
        else:
            dependent_jobs = dict()
            leaf_jobs = []
            for target in args.targets:
                _make_graph(
                    dependent_jobs,
                    leaf_jobs,
                    target,
                    self._job_of_target,
                    self.file,
                    self._deps_of_phony,
                    _nil,
                )
            _process_jobs(leaf_jobs, dependent_jobs, args.keep_going, args.jobs, args.load_average, args.dry_run)

    def main(self, argv):
        args = _parse_argv(argv[1:])
        self.finish(args)


class Err(Exception):
    def __init__(self, msg=""):
        self.msg=msg


# Internal use only.


class _Job:
    def __init__(self, f, ts, ds, descs):
        self.f = f
        self.ts = _listize(ts)
        self.ds = _listize(ds)
        self.descs = [desc for desc in descs if desc is not None]
        self.unique_ds = _unique(ds)
        self._n_rest = len(self.unique_ds)
        self.visited = False
        self._lock = threading.Lock()
        self._forced = False

    def __repr__(self):
        return f"{type(self).__name__}({repr(self.ts)}, {repr(self.ds)}, descs={repr(self.descs)})"

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
            self._n_rest = x

    def write(self, file=sys.stdout):
        for t in self.ts:
            print(t, file=file)
        for d in self.ds:
            print("\t" + d, file=file)


class _PhonyJob(_Job):
    def __init__(self, f, ts, ds, descs):
        if len(ts) != 1:
            raise Err(f"PhonyJob with multiple targets is not supported: {f}, {ts}, {ds}")
        super().__init__(f, ts, ds, descs)


class _FileJob(_Job):
    def __init__(self, f, ts, ds, descs):
        super().__init__(f, ts, ds, descs)

    def rm_targets(self):
        for t in self.ts:
            DSL.rm(t)

    def need_update(self):
        if self._forced:
            return self._forced
        stat_ds = [os.stat(d) for d in self.unique_ds]
        if not all(os.path.lexists(t) for t in self.ts):
            return True
        if not stat_ds:
            return False
        return max(d.st_mtime for d in stat_ds) > max(os.path.getmtime(t) for t in self.ts)


class _ThreadPool:
    def __init__(self, dependent_jobs, defered_errors, keep_going, n_max, load_average, dry_run):
        assert n_max > 0
        self._dependent_jobs = dependent_jobs
        self._defered_errors = defered_errors
        self._keep_going = keep_going
        self._n_max = n_max
        self._load_average = load_average
        self._dry_run = dry_run
        self._threads = _TSet()
        self._unwaited_threads = _TSet()
        self._threads_loc = threading.Lock()
        self._stack = _TStack()
        self._n_running = _TInt(0)

    def push_jobs(self, jobs):
        # pre-load `jobs` to avoid a situation where no active thread exist while a job is enqueued
        rem = len(jobs) - self._n_max
        for i in range(rem):
            self._stack.put(jobs[i])
        for i in range(rem, len(jobs)):
            self.push_job(jobs[i])

    def push_job(self, j):
        self._stack.put(j)
        with self._threads_loc:
            if (
                    len(self._threads) < 1 or (
                        len(self._threads) < self._n_max and
                        os.getloadavg()[0] <= self._load_average
                    )
            ):
                t = threading.Thread(target=self._worker, daemon=True)
                self._threads.add(t)
                self._unwaited_threads.add(t)
                t.start()

    def wait(self):
        while True:
            try:
                t = self._unwaited_threads.pop()
            except KeyError as e:
                break
            t.join()

    def _worker(self):
        try:
            while True:
                j = self._pop_job()
                if not j:
                    break
                assert j.n_rest() == 0
                got_error = False
                need_update = j.need_update()
                if need_update:
                    assert self._n_running.val() >= 0
                    if math.isfinite(self._load_average):
                        while (
                                self._n_running.val() > 0 and
                                os.getloadavg()[0] > self._load_average
                        ):
                            time.sleep(1)
                    self._n_running.inc()
                    try:
                        if self._dry_run:
                            j.write()
                            print()
                        else:
                            j.f(j)
                    except Exception as e:
                        got_error = True
                        warnings.warn(repr(j))
                        warnings.warn(repr(e))
                        j.rm_targets()
                        if self._keep_going:
                            self._defered_errors.put((j, e))
                        else:
                            self._die(e)
                    self._n_running.dec()
                j.set_n_rest(-1)
                if not got_error:
                    for t in j.ts:
                        # top targets does not have dependent jobs
                        for dj in self._dependent_jobs.get(t, ()):
                            dj.dec_n_rest()
                            if dj.n_rest() == 0:
                                dj._forced = need_update and self._dry_run
                                self.push_job(dj)
        except Exception as e:
            warnings.warn(repr(e))
            if not self._keep_going:
                self._die(e)
        finally:
            with self._threads_loc:
                try:
                    self._threads.remove(threading.current_thread())
                    self._unwaited_threads.remove(threading.current_thread())
                except:
                    pass

    def _pop_job(self):
        try:
            return self._stack.pop(block=True, timeout=0.02)
        except _TStack.Empty:
            return False

    def _die(self, e):
        _thread.interrupt_main()
        sys.exit(e)


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


class _TStack:
    class Empty(Exception):
        def __init__(self):
            pass

    def __init__(self):
        self._lock = threading.Lock()
        self._stack = []

    def put(self, x):
        with self._lock:
            self._stack.append(x)

    def pop(self, block=True, timeout=-1):
        success = self._lock.acquire(blocking=block, timeout=timeout)
        if success:
            if self._stack:
                ret = self._stack.pop()
            else:
                success = False
        self._lock.release()
        if success:
            return ret
        else:
            raise self.Empty()


class _TInt:
    def __init__(self, x):
        self._x = x
        self._lock = threading.Lock()

    def val(self):
        with self._lock:
            return self._x

    def inc(self):
        with self._lock:
            self._x += 1

    def dec(self):
        with self._lock:
            self._x -= 1


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


def _parse_argv(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "targets",
        nargs="*",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version}",
    )
    parser.add_argument(
        "-j", "--jobs",
        type=int,
        default=1,
        help="Number of parallel external jobs.",
    )
    parser.add_argument(
        "-l", "--load-average",
        type=float,
        default=float("inf"),
        help="No new job is started if there are other running jobs and the load average is higher than the specified value.",
    )
    parser.add_argument(
        "-k", "--keep-going",
        action="store_true",
        default=False,
        help="Keep going unrelated jobs even if some jobs fail.",
    )
    parser.add_argument(
        "-D", "--descriptions",
        action="store_true",
        default=False,
        help="Print descriptions, then exit.",
    )
    parser.add_argument(
        "-P", "--dependencies",
        action="store_true",
        default=False,
        help="Print dependencies, then exit.",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        default=False,
        help="Dry-run.",
    )
    args = parser.parse_args(argv)
    assert args.jobs > 0
    assert args.load_average > 0
    if not args.targets:
        args.targets.append("all")
    return args


def _print_descriptions(job_of_target):
    for target in sorted(job_of_target.keys()):
        print(target)
        for desc in job_of_target[target].descs:
            for l in desc.split("\t"):
                print("\t" + l)


def _print_dependencies(job_of_target):
    for j in sorted(set(job_of_target.values()), key=lambda j: j.ts):
        j.write()
        print()


def _process_jobs(jobs, dependent_jobs, keep_going, n_jobs, load_average, dry_run):
    defered_errors = queue.Queue()
    tp = _ThreadPool(dependent_jobs, defered_errors, keep_going, n_jobs, load_average, dry_run)
    tp.push_jobs(jobs)
    tp.wait()
    if defered_errors.qsize() > 0:
        warnings.warn("Following errors have thrown during the execution")
        for _ in range(defered_errors.qsize()):
            j, e = defered_errors.get()
            warnings.warn(repr(e))
            warnings.warn(j)
        raise Err("Execution failed.")


def _collect_phonies(job_of_target, deps_of_phony, f_of_phony, descs_of_phony):
    for target, deps in deps_of_phony.items():
        targets = _listize(target)
        deps = _listize(deps)
        _set_unique(
            job_of_target, target,
            _PhonyJob(f_of_phony.get(target, _do_nothing), targets, deps, descs_of_phony[target]),
        )


def _make_graph(
        dependent_jobs,
        leaf_jobs,
        target,
        job_of_target,
        file,
        phonies,
        call_chain,
):
    if target in call_chain:
        raise Err(f"A circular dependency detected: {target} for {repr(call_chain)}")
    if target not in job_of_target:
        assert target not in phonies
        if os.path.lexists(target):
            @file([target], [])
            def _(j):
                raise Err(f"Must not happen: job for leaf node {target} called")
        else:
            raise Err(f"No rule to make {target}")
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
            file,
            phonies,
            current_call_chain,
        )
    j.unique_ds or leaf_jobs.append(j)


def _listize(x):
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        return [x]
    raise NotImplementedError("_listize({x}: {typeof(x)})")


def _set_unique(d, k, v):
    if k in d:
        raise Err(f"{repr(k)} in {repr(d)}")
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
