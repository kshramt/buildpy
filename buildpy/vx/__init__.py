import _thread
import argparse
import functools
import io
import logging
import math
import os
import queue
import shutil
import sys
import threading
import time
import traceback

import google.cloud.exceptions

from ._log import logger
from . import _convenience
from . import _tval
from . import exception
from . import resource


__version__ = "4.3.0"


_PRIORITY_DEFAULT = 0


# Main

class DSL:

    sh = staticmethod(_convenience.sh)
    let = staticmethod(_convenience.let)
    loop = staticmethod(_convenience.loop)
    dirname = staticmethod(_convenience.dirname)
    jp = staticmethod(_convenience.jp)
    mkdir = staticmethod(_convenience.mkdir)
    mv = staticmethod(shutil.move)
    cd = staticmethod(_convenience.cd)
    serialize = staticmethod(_convenience.serialize)
    uriparse = staticmethod(_convenience.uriparse)

    def __init__(self, use_hash=False):
        self._job_of_target = dict()
        self._f_of_phony = dict()
        self._deps_of_phony = dict()
        self._descs_of_phony = dict()
        self._priority_of_phony = dict()
        self._use_hash = use_hash
        self.time_of_dep_cache = _tval.Cache()
        self.data = _tval.TDict()
        self.data["meta"] = _tval.TDefaultDict(_tval.TDict)

    def file(self, targets, deps, desc=None, use_hash=None, serial=False, priority=_PRIORITY_DEFAULT):
        """Declare a file job.
        Arguments:
            use_hash: Use the file checksum in addition to the modification time.
            serial: Jobs declared as `@file(serial=True)` runs exclusively to each other.
                The argument maybe useful to declare tasks that require a GPU or large amount of memory.
        """
        if use_hash is None:
            use_hash = self._use_hash
        targets = _listize(targets)
        deps = _listize(deps)

        def _(f):
            j = _FileJob(f, targets, deps, [desc], use_hash, serial, priority=priority, dsl=self)
            for t in targets:
                _set_unique(self._job_of_target, t, j)
            return _do_nothing
        return _

    def phony(self, target, deps, desc=None, priority=None):
        self._deps_of_phony.setdefault(target, []).extend(_listize(deps))
        self._descs_of_phony.setdefault(target, []).append(desc)
        if priority is not None:
            self._priority_of_phony[target] = priority

        def _(f):
            _set_unique(self._f_of_phony, target, f)
            return _do_nothing
        return _

    def finish(self, args):
        assert args.jobs > 0
        assert args.load_average > 0
        _collect_phonies(self._job_of_target, self._deps_of_phony, self._f_of_phony, self._descs_of_phony, priority_of_phony=self._priority_of_phony)
        if args.descriptions:
            _print_descriptions(self._job_of_target)
        elif args.dependencies:
            _print_dependencies(self._job_of_target)
        elif args.dependencies_dot:
            _print_dependencies_dot(self._job_of_target)
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
                    self.meta,
                    _nil,
                )
            _process_jobs(leaf_jobs, dependent_jobs, args.keep_going, args.jobs, args.n_serial, args.load_average, args.dry_run)

    def meta(self, name, **kwargs):
        _meta = self.data["meta"][name]
        for k, v in kwargs.items():
            if (k in _meta) and (_meta[k] != v):
                raise exception.Err(f"Tried to overwrite meta[{repr(k)}] = {repr(_meta[k])} by {v}")
            _meta[k] = v
        return name

    def rm(self, uri):
        logger.info(uri)
        puri = self.uriparse(uri)
        meta = self.data["meta"][uri]
        credential = meta["credential"] if "credential" in meta else None
        if puri.scheme == "file":
            assert puri.netloc == "localhost"
        if puri.scheme in resource.of_scheme:
            return resource.of_scheme[puri.scheme].rm(uri, credential)
        else:
            raise NotImplementedError(f"rm({repr(uri)}) is not supported")

    def main(self, argv):
        args = _parse_argv(argv[1:])
        logger.setLevel(getattr(logging, args.log.upper()))
        self.finish(args)


# Internal use only.


class _Job:
    def __init__(self, f, ts, ds, descs, priority):
        self.f = f
        self.ts = _listize(ts)
        self.ds = _listize(ds)
        self.descs = [desc for desc in descs if desc is not None]
        self.priority = priority
        self.unique_ds = _unique(ds)
        self._n_rest = len(self.unique_ds)
        self.visited = False
        self._lock = threading.Lock()
        self._dry_run = _tval.TBool(False)

    def __repr__(self):
        return f"{type(self).__name__}({repr(self.ts)}, {repr(self.ds)}, descs={repr(self.descs)})"

    def __lt__(self, other):
        return self.priority < other.priority

    def execute(self):
        self.f(self)

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

    def serial(self):
        return False

    def dry_run(self):
        return self._dry_run.val()

    def dry_run_set_self_or(self, x):
        return self._dry_run.set_self_or(x)

    def write(self, file=sys.stdout):
        for t in self.ts:
            print(t, file=file)
        for d in self.ds:
            print("\t" + d, file=file)


class _PhonyJob(_Job):
    def __init__(self, f, ts, ds, descs, priority):
        if len(ts) != 1:
            raise exception.Err(f"PhonyJob with multiple targets is not supported: {f}, {ts}, {ds}")
        super().__init__(f, ts, ds, descs, priority)


class _FileJob(_Job):
    def __init__(self, f, ts, ds, descs, use_hash, serial, priority, dsl):
        super().__init__(f, ts, ds, descs, priority)
        self._use_hash = use_hash
        self._serial = _tval.TBool(serial)
        self._dsl = dsl
        self._hash_orig = None
        self._hash_curr = None
        self._cache_path = None

    def __repr__(self):
        return f"{type(self).__name__}({repr(self.ts)}, {repr(self.ds)}, descs={repr(self.descs)}, serial={self.serial()})"

    def serial(self):
        return self._serial.val()

    def rm_targets(self):
        logger.info(f"rm_targets({repr(self.ts)})")
        for t in self.ts:
            meta = self._dsl.data["meta"][t]
            if not (("keep" in meta) and meta["keep"]):
                try:
                    self._dsl.rm(t)
                except (OSError, google.cloud.exceptions.NotFound, exception.NotFound) as e:
                    logger.info(f"Failed to remove {t}")

    def need_update(self):
        if self.dry_run():
            return True
        try:
            t_ts = min(mtime_of(uri=t, use_hash=False, credential=self._credential_of(t)) for t in self.ts)
        except (OSError, google.cloud.exceptions.NotFound, exception.NotFound):
            # Intentionally create hash caches.
            for d in self.unique_ds:
                self._time_of_dep_from_cache(d)
            return True
        # Intentionally create hash caches.
        # Do not use `any`.
        return max((self._time_of_dep_from_cache(d) for d in self.unique_ds), default=-float('inf')) > t_ts
        # Use of `>` instead of `>=` is intentional.
        # In theory, t_deps < t_targets if targets were made from deps, and thus you might expect ≮ (>=).
        # However, t_deps > t_targets should hold if the deps have modified *after* the creation of the targets.
        # As it is common that an accidental modification of deps is made by slow human hands
        # whereas targets are created by a fast computer program, I expect that use of > here to be better.

    def _time_of_dep_from_cache(self, d):
        """
        Return: the last hash time.
        """
        return self._dsl.time_of_dep_cache.get(d, functools.partial(mtime_of, uri=d, use_hash=self._use_hash, credential=self._credential_of(d)))

    def _credential_of(self, uri):
        meta = self._dsl.data["meta"][uri]
        return meta["credential"] if "credential" in meta else None


class _ThreadPool:
    def __init__(self, dependent_jobs, deferred_errors, keep_going, n_max, n_serial_max, load_average, dry_run):
        assert n_max > 0
        assert n_serial_max > 0
        self._dependent_jobs = dependent_jobs
        self._deferred_errors = deferred_errors
        self._keep_going = keep_going
        self._n_max = n_max
        self._load_average = load_average
        self._dry_run = dry_run
        self._threads = _tval.TSet()
        self._unwaited_threads = _tval.TSet()
        self._threads_loc = threading.Lock()
        self._queue = queue.PriorityQueue()
        self._serial_queue = queue.PriorityQueue()
        self._serial_queue_lock = threading.Semaphore(n_serial_max)
        self._n_running = _tval.TInt(0)

    def dry_run(self):
        return self._dry_run

    def push_jobs(self, jobs):
        # pre-load `jobs` to avoid a situation where no active thread exist while a job is enqueued
        rem = max(len(jobs) - self._n_max, 0)
        for i in range(rem):
            self._enq_job(jobs[i])
        for i in range(rem, len(jobs)):
            self.push_job(jobs[i])

    def push_job(self, j):
        self._enq_job(j)
        with self._threads_loc:
            if (
                    len(self._threads) < 1 or (
                        len(self._threads) < self._n_max and
                        os.getloadavg()[0] <= self._load_average
                    )
            ):
                t = threading.Thread(target=self._worker, daemon=True)
                self._threads.add(t)
                t.start()
                # A thread should be `start`ed before `join`ed
                self._unwaited_threads.add(t)

    def _enq_job(self, j):
        if j.serial():
            self._serial_queue.put(j)
        else:
            self._queue.put(j)

    def wait(self):
        while True:
            try:
                t = self._unwaited_threads.pop()
            except KeyError:
                break
            t.join()

    def _worker(self):
        try:
            while True:
                j = None
                if self._serial_queue_lock.acquire(blocking=False):
                    try:
                        j = self._serial_queue.get(block=False)
                        assert j.serial()
                    except queue.Empty:
                        self._serial_queue_lock.release()
                if j is None:
                    try:
                        j = self._queue.get(block=True, timeout=0.01)
                    except queue.Empty:
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
                        if self.dry_run():
                            j.write()
                            print()
                        else:
                            j.execute()
                    except Exception as e:
                        got_error = True
                        logger.error(repr(j))
                        e_str = _str_of_exception()
                        logger.error(e_str)
                        j.rm_targets()
                        if self._keep_going:
                            self._deferred_errors.put((j, e_str))
                        else:
                            self._die(e_str)
                    self._n_running.dec()
                if j.serial():
                    self._serial_queue_lock.release()
                j.set_n_rest(-1)
                if not got_error:
                    for t in j.ts:
                        # top targets does not have dependent jobs
                        for dj in self._dependent_jobs.get(t, ()):
                            dj.dec_n_rest()
                            dj.dry_run_set_self_or(need_update and self.dry_run())
                            if dj.n_rest() == 0:
                                self.push_job(dj)
            with self._threads_loc:
                try:
                    self._threads.remove(threading.current_thread())
                except KeyError:
                    pass
                try:
                    self._unwaited_threads.remove(threading.current_thread())
                except KeyError:
                    pass
        except Exception as e: # Propagate Exception caused by a bug in buildpy code to the main thread.
            e_str = _str_of_exception()
            logger.error(e_str)
            self._die(e_str)

    def _die(self, e):
        logger.critical(e)
        _thread.interrupt_main()
        sys.exit(e)


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
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
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
        "--log",
        default="warning",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Set log level.",
    )
    parser.add_argument(
        "-j", "--jobs",
        type=int,
        default=1,
        help="Number of parallel external jobs.",
    )
    parser.add_argument(
        "--n-serial",
        type=int,
        default=1,
        help="Number of parallel serial jobs.",
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
        "-Q", "--dependencies-dot",
        action="store_true",
        default=False,
        help=f"Print dependencies in DOT format, then exit. {os.path.basename(sys.executable)} build.py -Q | dot -Tpdf -Grankdir=LR -Nshape=plaintext -Ecolor='#00000088' >| workflow.pdf",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        default=False,
        help="Dry-run.",
    )
    args = parser.parse_args(argv)
    assert args.jobs > 0
    assert args.n_serial > 0
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


def _print_dependencies_dot(job_of_target):
    node_of_name = dict()
    i = 0
    i_cluster = 0
    print("digraph G{")
    for j in sorted(set(job_of_target.values()), key=lambda j: j.ts):
        i += 1
        i_cluster += 1
        action_node = "n" + str(i)
        print(action_node + "[label=\"○\"]")
        for name in j.ts:
            node, i = _node_of(name, node_of_name, i)
            print(node + "[label=" + _escape(name) + "]")
            print(node + " -> " + action_node)

        print(f"subgraph cluster_{i_cluster}{{")
        for name in j.ts:
            print(node_of_name[name])
        print("}")

        for name in j.ds:
            node, i = _node_of(name, node_of_name, i)
            print(node + "[label=" + _escape(name) + "]")
            print(action_node + " -> " + node)
    print("}")


def _node_of(name, node_of_name, i):
    if name in node_of_name:
        node = node_of_name[name]
    else:
        i += 1
        node = "n" + str(i)
        node_of_name[name] = node
    return node, i


def _escape(s):
    return "\"" + "".join('\\"' if x == "\"" else x for x in s) + "\""


def _process_jobs(jobs, dependent_jobs, keep_going, n_jobs, n_serial, load_average, dry_run):
    deferred_errors = queue.Queue()
    tp = _ThreadPool(dependent_jobs, deferred_errors, keep_going, n_jobs, n_serial, load_average, dry_run)
    tp.push_jobs(jobs)
    tp.wait()
    if deferred_errors.qsize() > 0:
        logger.error("Following errors have thrown during the execution")
        for _ in range(deferred_errors.qsize()):
            j, e_str = deferred_errors.get()
            logger.error(e_str)
            logger.error(repr(j))
        raise exception.Err("Execution failed.")


def _collect_phonies(job_of_target, deps_of_phony, f_of_phony, descs_of_phony, priority_of_phony):
    for target, deps in deps_of_phony.items():
        targets = _listize(target)
        deps = _listize(deps)
        _set_unique(
            job_of_target, target,
            _PhonyJob(f_of_phony.get(target, _do_nothing), targets, deps, descs_of_phony[target], priority=priority_of_phony.get(target, _PRIORITY_DEFAULT)),
        )


def _make_graph(
        dependent_jobs,
        leaf_jobs,
        target,
        job_of_target,
        file,
        phonies,
        meta,
        call_chain,
):
    if target in call_chain:
        raise exception.Err(f"A circular dependency detected: {target} for {repr(call_chain)}")
    if target not in job_of_target:
        assert target not in phonies
        ptarget = DSL.uriparse(target)
        if (ptarget.scheme == "file") and (ptarget.netloc == "localhost"):
            # Although this branch is not necessary since the `else` branch does the job,
            # this branch is useful for a quick sanity check.
            if os.path.lexists(target):
                @file([meta(target, keep=True)], [])
                def _(j):
                    raise exception.Err(f"Must not happen: the job for a leaf node {target} is called")
            else:
                raise exception.Err(f"No rule to make {target}")
        else:
            # There is no easy (and cheap) way to check existence of a remote resource.
            @file([meta(target, keep=True)], [])
            def _(j):
                raise exception.Err(f"No rule to make {target}")
    j = job_of_target[target]
    if j.visited:
        return
    j.visited = True
    current_call_chain = _Cons(target, call_chain)
    for dep in sorted(j.unique_ds, key=lambda dep: _key_to_sort_unique_ds(dep, job_of_target)):
        dependent_jobs.setdefault(dep, []).append(j)
        _make_graph(
            dependent_jobs,
            leaf_jobs,
            dep,
            job_of_target,
            file,
            phonies,
            meta,
            current_call_chain,
        )
    j.unique_ds or leaf_jobs.append(j)


def _key_to_sort_unique_ds(dep, job_of_target):
    try:
        return job_of_target[dep].priority
    except KeyError:
        return math.inf


def _listize(x):
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        return [x]
    raise NotImplementedError(f"_listize({repr(x)}: {type(x)})")


def _set_unique(d, k, v):
    if k in d:
        raise exception.Err(f"{repr(k)} in {repr(d)}")
    d[k] = v
    return d


def _unique(xs):
    seen = set()
    ret = []
    for x in xs:
        if x not in seen:
            ret.append(x)
            seen.add(x)
    return ret


def mtime_of(uri, use_hash, credential):
    puri = DSL.uriparse(uri)
    if puri.scheme == "file":
        assert puri.netloc == "localhost"
    if puri.scheme in resource.of_scheme:
        return resource.of_scheme[puri.scheme].mtime_of(uri, credential, use_hash)
    else:
        raise NotImplementedError(f"mtime_of({repr(uri)}) is not supported")


def _str_of_exception():
    fp = io.StringIO()
    traceback.print_exc(file=fp)
    return fp.getvalue()


def _do_nothing(*_):
    pass
