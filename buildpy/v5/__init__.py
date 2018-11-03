import _thread
import argparse
import functools
import io
import json
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
import psutil

from ._log import logger
from . import _convenience
from . import _tval
from . import exception
from . import resource


__version__ = "5.1.0"


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

    def __init__(self, argv, use_hash=False):
        self.args = _parse_argv(argv[1:])
        assert self.args.jobs > 0
        assert self.args.load_average > 0

        logger.setLevel(getattr(logging, self.args.log.upper()))
        self.resource_of_uri = dict()
        self.resource_of_uri_lock = threading.RLock()
        self.job_of_target = _JobOfTarget(self.resource_of_uri, self.resource_of_uri_lock)
        self._use_hash = use_hash
        self.time_of_dep_cache = _tval.Cache()
        self.cut_phony_jobs = set()

        self.thread_pool = _ThreadPool(
            self.resource_of_uri,
            self.args.keep_going, self.args.jobs,
            self.args.n_serial, self.args.load_average
        )

    def file(
            self,
            targets,
            deps,
            desc=None,
            use_hash=None,
            serial=False,
            priority=_PRIORITY_DEFAULT,
            ty=None,
            dy=None,
            data=None,
            cut=False,
    ):
        """Declare a file job.
        Arguments:
            use_hash: Use the file checksum in addition to the modification time.
            serial: Jobs declared as `@file(serial=True)` runs exclusively to each other.
                The argument maybe useful to declare tasks that require a GPU or large amount of memory.
        """

        if cut:
            return

        if data is None:
            data = dict()

        def _(f):
            j = _FileJob(
                f,
                targets,
                deps,
                [desc],
                _coalesce(use_hash, self._use_hash),
                serial,
                priority=priority,
                dsl=self,
                ty=_coalesce(ty, []),
                dy=_coalesce(dy, []),
                data=data,
            )

            self.update_resource_of_uri(targets, deps, j)
            return j
        return _

    def phony(
            self,
            target,
            deps,
            desc=None,
            priority=None,
            ty=None,
            dy=None,
            data=None,
            cut=False,
    ):
        if cut or (target in self.cut_phony_jobs):
            self.cut_phony_jobs.add(target)
            return

        if data is None:
            data = dict()

        with self.resource_of_uri_lock:
            if target in self.job_of_target:
                j = self.job_of_target[target]
                assert j.status == "initial", j
                if desc is not None:
                    j.descs.append(desc)
                j.extend_ds(deps)
                _extend_keys(j.ty, _coalesce(ty, []))
                _extend_keys(j.dy, _coalesce(dy, []))
                j.priority = priority
                j.data._update(data)
            else:
                j = _PhonyJob(
                    None,
                    [target],
                    deps,
                    [] if desc is None else [desc],
                    priority,
                    dsl=self,
                    ty=_coalesce(ty, []),
                    dy=_coalesce(dy, []),
                    data=data,
                )
            self.update_resource_of_uri([target], deps, j)
            return j

    def run(self):
        if self.args.descriptions:
            _print_descriptions(self.job_of_target)
        elif self.args.dependencies:
            _print_dependencies(self.job_of_target)
        elif self.args.dependencies_dot:
            print(self.dependencies_dot())
        elif self.args.dependencies_json:
            print(self.dependencies_json())
        else:
            try:
                for target in self.args.targets:
                    logger.debug(f"{target}")
                    self.resource_of_uri[target].invoke()
                self.thread_pool.wait()
            except KeyboardInterrupt as e:
                self.thread_pool.stop = True
                _terminate_subprocesses()
                raise
            if self.thread_pool.deferred_errors.qsize() > 0:
                logger.error("Following errors have thrown during the execution")
                for _ in range(self.thread_pool.deferred_errors.qsize()):
                    j, e_str = self.thread_pool.deferred_errors.get()
                    logger.error(e_str)
                    logger.error(repr(j))
                raise exception.Err("Execution failed.")

    def meta(self, uri, **kwargs):
        r = self.resource_of_uri[uri]
        for k, v in kwargs.items():
            r[k] = v
        return uri

    def rm(self, uri):
        logger.info(uri)
        puri = self.uriparse(uri)
        meta = self.resource_of_uri[uri]
        credential = meta["credential"] if "credential" in meta else None
        if puri.scheme == "file":
            assert (puri.netloc == "localhost"), puri
        if puri.scheme in resource.of_scheme:
            return resource.of_scheme[puri.scheme].rm(uri, credential)
        else:
            raise NotImplementedError(f"rm({repr(uri)}) is not supported")

    def dependencies_json(self):
        return _dependencies_json_of(self.job_of_target)

    def dependencies_dot(self):
        return _dependencies_dot_of(self.job_of_target)

    def update_resource_of_uri(self, targets, deps, j):
        with self.resource_of_uri_lock:
            for target in targets:
                if target in self.resource_of_uri:
                    r = self.resource_of_uri[target]
                    r.dj = j
                else:
                    r = _Resource(target, set(), j, dsl=self)
                    self.resource_of_uri[target] = r
            for dep in deps:
                if dep in self.resource_of_uri:
                    r = self.resource_of_uri[dep]
                    r.add_tjs(j)
                else:
                    r = _Resource(dep, set([j]), None, dsl=self)
                    self.resource_of_uri[dep] = r


# Internal use only.


class _JobOfTarget(object):

    def __init__(self, resource_of_uri, lock):
        self.lock = lock
        self._resource_of_uri = resource_of_uri

    def __getitem__(self, k):
        with self.lock: # 1
            ret = self._resource_of_uri[k].dj
            if ret is None:
                raise KeyError(k)
            return ret

    def __contains__(self, k):
        with self.lock: # 2
            return (k in self._resource_of_uri) and (self._resource_of_uri[k].dj is not None)

    def keys(self):
        for k in self._resource_of_uri.keys():
            if k in self:
                yield k

    def values(self):
        for k in self.keys():
            yield self[k]


class _Nil:
    __slots__ = ()

    def __contains__(self, x):
        return False

    def __repr__(self):
        return "nil"


_nil = _Nil()


class _Cons:
    __slots__ = ("h", "t")

    def __init__(self, h, t):
        self.h = h
        self.t = t

    def __contains__(self, x):
        return (self.h == x) or (x in self.t)

    def __repr__(self):
        return f"({repr(self.h)} . {repr(self.t)})"


class _Resource(object):

    statuses = ("initial", "invoked", "done")

    def __init__(self, uri, tjs, dj, dsl):
        self.lock = threading.RLock()
        self.uri = uri
        self._tjs = tjs
        self._dj = dj
        self.dsl = dsl
        self.meta = dict()
        self._status = "initial"

    def __repr__(self):
        return f"{self.__class__.__name__}({repr(self.uri)}).status={repr(self.status)}.meta={self.meta}"

    @property
    def tjs(self):
        return self._tjs

    @property
    def dj(self):
        return self._dj

    @dj.setter
    def dj(self, dj):
        with self.lock: # 3
            assert (self.dj is None) or self.dj == dj, (self, self.dj)
            self._dj = dj

    @property
    def status(self):
        assert self._status in self.statuses, self
        return self._status

    @status.setter
    def status(self, v):
        with self.lock: # 4
            assert self._status in self.statuses, self._status
            assert v in self.statuses, v
            self._status = v

    def add_tjs(self, tj):
        with self.lock: # 5
            self._tjs.add(tj)

    def __getitem__(self, k):
        with self.lock: # 6
            return self.meta[k]

    def __setitem__(self, k, v):
        with self.lock: # 7
            if (k in self.meta) and (self.meta[k] != v):
                raise exception.Err(f"Tried to overwrite {self}[{repr(k)}] = {self.meta[k]} by {v}")
            self.meta[k] = v

    def __contains__(self, v):
        with self.lock: # 8
            return v in self.meta

    def invoke(self, call_chain=_nil):
        logger.debug(f"{self}")
        if self in call_chain:
            raise exception.Err(f"A circular dependency detected: {self} for {call_chain}")
        with self.lock: # 9
            status = self.status
            if status == "initial":
                self.status = "invoked"
            elif status == "invoked":
                return
            elif status == "done":
                pass
            else:
                raise exception.Err(f"Must not happen {self}")
        if status == "initial":
            assert self.status == "invoked"
            if self.dj is None:
                puri = DSL.uriparse(self.uri)
                if (puri.scheme == "file") and (puri.netloc == "localhost"):
                    # Although this branch is not necessary since the `else` branch does the job,
                    # this branch is useful for a quick sanity check.
                    if os.path.lexists(puri.path):
                        @self.dsl.file([self.dsl.meta(self.uri, keep=True)], [])
                        def _(j):
                            raise exception.Err(f"Must not happen: the job for a leaf node {self.uri} is called")
                    else:
                        raise exception.Err(f"No rule to make {self.uri}")
                else:
                    # There is no easy (and cheap) way to check the existence of remote resources.
                    @self.dsl.file([self.dsl.meta(self.uri, keep=True)], [])
                    def _(j):
                        raise exception.Err(f"No rule to make {self.uri}")
            self.dj.invoke(_Cons(self, call_chain))
        elif status == "invoked":
            raise exception.Err("Must not happen {self}")
        elif status == "done":
            self.kick_ts()
        else:
            raise exception.Err(f"Must not happen {status} for {self}")

    def kick(self):
        logger.debug(f"{self}")
        with self.lock: # 10
            assert self.status in ("initial", "invoked"), self
            if self.status == "initial":
                self.status = "done"
            elif self.status == "invoked":
                self.kick_ts()
            else:
                raise exception.Err(f"Must not happen: {self}")

    def kick_ts(self):
        logger.debug(f"{self}")
        assert self.status in ("invoked", "done")
        for tj in self.tjs:
            tj.kick(self.uri)
        self.status = "done"


class _Job(object):

    statuses = ("initial", "invoked", "enqed", "done")

    def __init__(
            self,
            f,
            ts,
            ds,
            descs,
            priority,
            dsl,
            ty,
            dy,
            data,
    ):
        self.lock = threading.RLock()
        self._status = "initial"
        self.executed = False
        self.serial = False

        self._f = f
        self.ty = _tval.ddict((k, None) for k in ["_ts"] + ty)
        self.dy = _tval.ddict((k, None) for k in ["_ds"] + dy)
        self.descs = descs
        self._priority = priority
        self._dsl = dsl

        self.ts_unique = set()
        self.ds_unique = set()
        self.ds_done = set()
        self.set_ty("_ts", ts)
        self.set_dy("_ds", ds)

        # User data.
        self.data = _tval.ddict(data)

    def __repr__(self):
        ds = self.ds
        if self.ds and (len(self.ds) > 4):
            ds = ds[:2] + [_cdots] + ds[-2:]
        return f"{type(self).__name__}({self.ts}, {ds}).status={repr(self.status)}"

    def __lt__(self, other):
        with self.lock:
            return self.priority < other.priority

    @property
    def f(self):
        with self.lock:
            return _coalesce(self._f, _do_nothing)

    @f.setter
    def f(self, f):
        with self.lock:
            if self._f is None:
                self._f = f
            elif self._f == f:
                pass
            else:
                raise exception.Err(f"{self._f} for {self} is overwritten by {f}")

    @property
    def ts(self):
        with self.lock:
            return self.ty._ts

    @property
    def ds(self):
        with self.lock:
            return self.dy._ds

    @property
    def priority(self):
        with self.lock:
            return _coalesce(self._priority, _PRIORITY_DEFAULT)

    @priority.setter
    def priority(self, priority):
        with self.lock:
            if priority is not None:
                self._priority = priority

    @property
    def dsl(self):
        with self.lock:
            return self._dsl

    @property
    def status(self):
        with self.lock:
            assert self._status in self.statuses, self
            return self._status

    @status.setter
    def status(self, v):
        with self.lock: # 11
            assert self._status in self.statuses, self._status
            assert v in self.statuses, v
            self._status = v

    def execute(self):
        logger.debug(f"{self}")
        with self.lock: # 16
            assert (self.status == "enqed"), self
            assert (not self.executed), self
            self.executed = True
        if self.dsl.args.dry_run:
            self.write()
        else:
            self.f(self)

    def rm_targets(self):
        assert (self.status == "enqed"), self

    def need_update(self):
        return True

    def mark_as_made(self, d):
        with self.lock: # 12
            if d is not None:
                assert d in self.ds_unique, (d, self)
                self.ds_done.add(d)

    def write(self, file=sys.stdout):
        logger.debug(f"{self}")
        for t in self.ts:
            print(t, file=file)
        for d in self.ds:
            print("\t" + d, file=file)
        print(file=file)

    def invoke(self, call_chain=_nil):
        logger.debug(f"{self}")
        if self in call_chain:
            raise exception.Err(f"A circular dependency detected: {self} for {call_chain}")
        with self.lock: # 13
            status = self.status
            if status == "initial":
                self.status = "invoked"
            elif status == "invoked":
                pass
            elif status == "enqed":
                return
            elif status == "done":
                return
            else:
                raise exception.Err(f"Must not happen {status} for {self}")
        if status == "initial":
            self._invoke(call_chain)
        elif status == "invoked":
            # A job with dy may be invoked multiple times
            self._invoke(call_chain)
        elif status == "enqed":
            raise exception.Err(f"Must not happen {status} for {self}")
        elif status == "done":
            raise exception.Err(f"Must not happen {status} for {self}")
        else:
            raise exception.Err(f"Must not happen {status} for {self}")

    def _invoke(self, call_chain):
        assert self.status == "invoked"
        if self.ds_unique:
            cc = _Cons(self, call_chain)
            for d in self.ds_unique:
                self.dsl.resource_of_uri[d].invoke(cc)
        else:
            self.kick()

    def ready(self):
        # It does not take much time to compare two sets
        return (None not in self.dy._values()) and (self.ds_done == self.ds_unique)

    def kick(self, uri=None):
        logger.debug(f"{self}")
        with self.lock: # 14
            if self.status == "initial":
                self.mark_as_made(uri)
            elif self.status == "invoked":
                self.mark_as_made(uri)
                if self.ready():
                    self._enq()
            elif self.status == "enqed":
                assert self.ready()
            elif self.status == "done":
                assert self.ready()
            else:
                raise exception.Err(f"Must not happen: {self}")

    def _enq(self):
        logger.debug(f"{self}")
        with self.lock: # 15
            assert self.status == "invoked"
            self.status = "enqed"
        self.dsl.thread_pool.push_job(self)

    def kick_ts(self):
        logger.debug(f"{self}")
        assert self.status in ("enqed", "done"), self
        assert None not in self.dy._values()
        for t in self.ts_unique:
            self.dsl.resource_of_uri[t].kick()
        self.status = "done"

    def set_ty(self, k, v):
        logger.debug(f"{self} {repr(k)}: {repr(v)}")
        with self.lock:
            assert (k in self.ty), self
            assert self.ty[k] is None, self
            self.ty[k] = v
            ty = set(v) - self.ts_unique
            self.ts_unique.update(ty)
            self._dsl.update_resource_of_uri(ty, [], self)
            if self.status == "done":
                self.kick_ts()

    def set_dy(self, k, v):
        logger.debug(f"{self} {repr(k)}: {repr(v)}")
        with self.lock:
            assert self.status in ("initial", "invoked"), self
            assert k in self.dy, self
            assert self.dy[k] is None, self
            self.dy[k] = v
            dy = set(v) - self.ds_unique
            self.ds_unique.update(dy)
            self._dsl.update_resource_of_uri([], dy, self)
            if self.status == "invoked":
                self.invoke()


class _PhonyJob(_Job):
    def __init__(
            self,
            f,
            ts,
            ds,
            descs,
            priority,
            dsl,
            ty,
            dy,
            data,
    ):
        if len(ts) != 1:
            raise exception.Err(f"PhonyJob with multiple targets is not supported: {f}, {ts}, {ds}")
        super().__init__(
            f,
            ts,
            ds,
            descs,
            priority,
            dsl=dsl,
            ty=ty,
            dy=dy,
            data=data,
        )

    def __call__(self, f):
        self.f = f
        return self

    def extend_ds(self, ds):
        with self.lock:
            self.ds.extend(ds)
            self.ds_unique.update(ds)


class _FileJob(_Job):
    def __init__(
            self,
            f,
            ts,
            ds,
            descs,
            use_hash,
            serial,
            priority,
            dsl,
            ty,
            dy,
            data,
    ):
        super().__init__(
            f,
            ts,
            ds,
            descs,
            priority,
            dsl=dsl,
            ty=ty,
            dy=dy,
            data=data,
        )
        self._use_hash = use_hash
        self.serial = serial
        self._hash_orig = None
        self._hash_curr = None
        self._cache_path = None

    def __repr__(self):
        ds = self.ds
        if self.ds and (len(self.ds) > 4):
            ds = ds[:2] + [_cdots] + ds[-2:]
        return f"{type(self).__name__}({self.ts}, {ds}, serial={self.serial}).status={repr(self.status)}"

    def rm_targets(self):
        logger.info(f"rm_targets({repr(self.ts)})")
        for t in self.ts_unique:
            meta = self._dsl.resource_of_uri[t]
            if not (("keep" in meta) and meta["keep"]):
                try:
                    self._dsl.rm(t)
                except (OSError, google.cloud.exceptions.NotFound, exception.NotFound) as e:
                    logger.info(f"Failed to remove {t}")

    def need_update(self):
        with self.lock:
            assert self.status == "enqed", self
            if self.dsl.args.dry_run and self.ds_unique and any(self.dsl.resource_of_uri[d].dj.executed for d in self.ds_unique):
                return True
            return self._need_update()

    def _need_update(self):
        try:
            t_ts = min(mtime_of(uri=t, use_hash=False, credential=self._credential_of(t)) for t in self.ts_unique)
        except (OSError, google.cloud.exceptions.NotFound, exception.NotFound):
            # Intentionally create hash caches.
            for d in self.ds_unique:
                self._time_of_dep_from_cache(d)
            return True
        # Intentionally create hash caches.
        # Do not use `any`.
        return max((self._time_of_dep_from_cache(d) for d in self.ds_unique), default=-float('inf')) > t_ts
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
        meta = self._dsl.resource_of_uri[uri]
        return meta["credential"] if "credential" in meta else None


class _ThreadPool(object):
    def __init__(self, resource_of_uri, keep_going, n_max, n_serial_max, load_average):
        assert n_max > 0
        assert n_serial_max > 0
        self.deferred_errors = queue.Queue()
        self.resource_of_uri = resource_of_uri
        self._keep_going = keep_going
        self._n_max = n_max
        self._load_average = load_average
        self._threads = _tval.TSet()
        self._unwaited_threads = _tval.TSet()
        self._threads_loc = threading.RLock()
        self._queue = queue.PriorityQueue()
        self._serial_queue = queue.PriorityQueue()
        self._serial_queue_lock = threading.Semaphore(n_serial_max)
        self._n_running = _tval.TInt(0)
        self.stop = False

    def push_job(self, j):
        if self.stop:
            return
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
        if j.serial:
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
                if self.stop:
                    break
                j = None
                if self._serial_queue_lock.acquire(blocking=False):
                    try:
                        j = self._serial_queue.get(block=False)
                        assert j.serial
                    except queue.Empty:
                        self._serial_queue_lock.release()
                if j is None:
                    try:
                        j = self._queue.get(block=True, timeout=0.01)
                    except queue.Empty:
                        break
                logger.debug(f"working on {j}")
                assert j.ready()
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
                        j.execute()
                    except Exception as e:
                        got_error = True
                        logger.error(repr(j))
                        e_str = _str_of_exception()
                        logger.error(e_str)
                        j.rm_targets()
                        if self._keep_going:
                            self.deferred_errors.put((j, e_str))
                        else:
                            self._die(e_str)
                    self._n_running.dec()
                if j.serial:
                    self._serial_queue_lock.release()
                if not got_error:
                    j.kick_ts()
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
        not_stopped = not self.stop
        self.stop = True
        _terminate_subprocesses()
        if not_stopped:
            _thread.interrupt_main()


class CDots(object):

    def __repr__(self):
        return ".."


_cdots = CDots()


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
        type=str,
        const="/dev/stdout",
        nargs="?",
        help=f"Print dependencies in the DOT format, then exit. {os.path.basename(sys.executable)} build.py -Q | dot -Tpdf -Grankdir=LR -Nshape=plaintext -Ecolor='#00000088' >| workflow.pdf",
    )
    parser.add_argument(
        "-J", "--dependencies-json",
        type=str,
        const="/dev/stdout",
        nargs="?",
        help=f"Print dependencies in the JSON format, then exit. {os.path.basename(sys.executable)} build.py -J | jq .",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        default=False,
        help="Dry-run.",
    )
    parser.add_argument("--cut", action="append", help="Cut the DAG at the job of the specified resource. You can specify --cut=target multiple times.")
    args = parser.parse_args(argv)
    assert args.jobs > 0
    assert args.n_serial > 0
    assert args.load_average > 0
    if not args.targets:
        args.targets.append("all")
    if args.cut is None:
        args.cut = []
    args.cut = set(args.cut)
    return args


def _print_descriptions(job_of_target):
    for target in sorted(job_of_target.keys()):
        print(target)
        for desc in job_of_target[target].descs:
            for l in desc.split("\t"):
                print("\t" + l)


def _print_dependencies(job_of_target):
    # sorted(j.ts_unique) is used to make the output deterministic
    for j in sorted(set(job_of_target.values()), key=lambda j: sorted(j.ts_unique)):
        j.write()


def _dependencies_dot_of(job_of_target):
    data = json.loads(_dependencies_json_of(job_of_target))
    fp = io.StringIO()
    node_of_name = dict()
    i = 0
    i_cluster = 0

    print("digraph G{", file=fp)
    for j in data:
        i += 1
        i_cluster += 1
        action_node = "n" + str(i)
        print(action_node + "[label=\"○\"]", file=fp)

        for name in sorted(set(_flatten1(j["ty"].values()))):
            node, i = _node_of(name, node_of_name, i)
            print(node + "[label=" + _escape(name) + "]", file=fp)
            print(node + " -> " + action_node, file=fp)

        print(f"subgraph cluster_{i_cluster}" "{", file=fp)
        for name in sorted(set(_flatten1(j["ty"].values()))):
            print(node_of_name[name], file=fp)
        print("}", file=fp)

        for name in sorted(set(_flatten1(j["dy"].values()))):
            node, i = _node_of(name, node_of_name, i)
            print(node + "[label=" + _escape(name) + "]", file=fp)
            print(action_node + " -> " + node, file=fp)
    print("}", end="", file=fp)
    return fp.getvalue()


def _dependencies_json_of(job_of_target):
    return json.dumps(
        [dict(ty=j.ty._to_dict_rec(), dy=j.dy._to_dict_rec()) for j in sorted(set(job_of_target.values()), key=lambda j: sorted(j.ts_unique))],
        ensure_ascii=False,
        sort_keys=True,
    )


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


def mtime_of(uri, use_hash, credential):
    puri = DSL.uriparse(uri)
    if puri.scheme == "file":
        assert (puri.netloc == "localhost"), puri
    if puri.scheme in resource.of_scheme:
        return resource.of_scheme[puri.scheme].mtime_of(uri, credential, use_hash)
    else:
        raise NotImplementedError(f"mtime_of({repr(uri)}) is not supported")


def _str_of_exception():
    fp = io.StringIO()
    traceback.print_exc(file=fp)
    return fp.getvalue()


def _terminate_subprocesses():
    for p in psutil.Process().children(recursive=True):
        try:
            logger.info(p)
            p.terminate()
        except Exception:
            pass


def _coalesce(x, default):
    return default if x is None else x


def _extend_keys(d, ks):
    for k in ks:
        if k not in d:
            d[k] = None


def _flatten1(xss):
    for xs in xss:
        if xs:
            yield from xs


def _do_nothing(*_):
    pass
