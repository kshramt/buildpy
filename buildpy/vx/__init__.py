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


__version__ = "6.2.4"


_PRIORITY_DEFAULT = 0


_CDOTS = "…"


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

    def __init__(self, argv, use_hash=True, terminate_subprocesses=True):
        self.args = _parse_argv(argv[1:])
        assert self.args.jobs > 0
        assert self.args.load_average > 0

        logger.setLevel(getattr(logging, self.args.log.upper()))
        self.job_of_target = _tval.NonOverwritableDict()
        self.jobs = _tval.TSet()
        self._use_hash = use_hash
        self._terminate_subprocesses = terminate_subprocesses
        self.time_of_dep_cache = _tval.Cache()
        self.metadata = _tval.TDefaultDict()

        self.task_context = _TaskContext()

        self.thread_pool = _ThreadPool(
            self.job_of_target,
            self.args.keep_going, self.args.jobs,
            self.args.n_serial, self.args.load_average,
            die_hooks=[self._cleanup],
        )

    def file(
            self,
            targets,
            deps,
            desc=None,
            use_hash=None,
            serial=False,
            priority=_PRIORITY_DEFAULT,
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

        j = _FileJob(
            None,
            targets,
            deps,
            desc,
            _coalesce(use_hash, self._use_hash),
            serial,
            priority=priority,
            dsl=self,
            data=data,
        )
        self.jobs.add(j)
        return j

    def phony(
            self,
            target,
            deps,
            desc=None,
            priority=_PRIORITY_DEFAULT,
            data=None,
            cut=False,
    ):
        if cut:
            return

        if data is None:
            data = dict()

        j = _PhonyJob(
            None,
            [target],
            deps,
            desc,
            priority,
            dsl=self,
            data=data,
        )
        self.jobs.add(j)
        return j

    def run(self):
        if self.args.descriptions:
            _print_descriptions(self.jobs)
        elif self.args.dependencies:
            _print_dependencies(self.jobs)
        elif self.args.dependencies_dot:
            print(self.dependencies_dot())
        elif self.args.dependencies_json:
            print(self.dependencies_json())
        else:
            try:
                for target in self.args.targets:
                    logger.debug(target)
                    self.job_of_target[target].invoke()
                for target in self.args.targets:
                    self.job_of_target[target].wait()
            except KeyboardInterrupt as e:
                self._cleanup()
                raise
            if self.thread_pool.deferred_errors.qsize() > 0:
                logger.error("Following errors have thrown during the execution")
                for _ in range(self.thread_pool.deferred_errors.qsize()):
                    j, e_str = self.thread_pool.deferred_errors.get()
                    logger.error(e_str)
                    logger.error(j)
                raise exception.Err("Execution failed.")

    def meta(self, uri, **kwargs):
        self.metadata[uri] = kwargs
        return uri

    def rm(self, uri):
        logger.info(uri)
        puri = self.uriparse(uri)
        meta = self.metadata[uri]
        credential = meta["credential"] if "credential" in meta else None
        if puri.scheme == "file":
            assert (puri.netloc == "localhost"), puri
        if puri.scheme in resource.of_scheme:
            return resource.of_scheme[puri.scheme].rm(uri, credential)
        else:
            raise NotImplementedError(f"rm({repr(uri)}) is not supported")

    def dependencies_json(self):
        return _dependencies_json_of(self.jobs)

    def dependencies_dot(self):
        return _dependencies_dot_of(self.jobs)

    def _cleanup(self):
        self.task_context.stop = True
        self.thread_pool.stop = True
        if self._terminate_subprocesses:
            _terminate_subprocesses()


# Internal use only.

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


class _Job(object):

    def __init__(
            self,
            f,
            ts,
            ds,
            desc,
            priority,
            dsl,
            data,
    ):
        self.lock = threading.RLock()
        self.done = threading.Event()
        self.executed = False
        self.successed = False  # True if self.execute did not raise an error
        self.serial = False

        self._f = f
        self.ts = ts
        self.ds = ds
        self.desc = desc
        self.priority = priority
        self.dsl = dsl

        self.ts_unique = set(self.ts)
        self.ds_unique = set(self.ds)

        self.task = None

        for t in self.ts:
            self.dsl.job_of_target[t] = self

        # User data.
        self.data = _tval.ddict(data)

    def __repr__(self):
        ds = self.ds
        if self.ds and (len(self.ds) > 4):
            ds = ds[:2] + [_CDOTS]
        return f"{type(self).__name__}({self.ts}, {ds})"

    def __call__(self, f):
        self.f = f
        return self

    def __lt__(self, other):
        return self.priority < other.priority

    @property
    def f(self):
        return _coalesce(self._f, _do_nothing)

    @f.setter
    def f(self, f):
        if self._f is None:
            self._f = f
        elif self._f == f:
            pass
        else:
            raise exception.Err(f"{self._f} for {self} is overwritten by {f}")

    def execute(self):
        logger.debug(self)
        assert (not self.done.is_set()), self
        if self.dsl.args.dry_run:
            self.write()
        else:
            self.f(self)

    def rm_targets(self):
        pass

    def need_update(self):
        return True

    def write(self, file=sys.stdout):
        logger.debug(self)
        for t in self.ts:
            print(t, file=file)
        for d in self.ds:
            print("\t", d, sep="", file=file)
        print(file=file)

    def invoke(self, call_chain=_nil):
        logger.debug(self)

        if self in call_chain:
            raise exception.Err(f"A circular dependency detected: {self} for {call_chain}")
        with self.lock:
            if self.task is None:
                cc = _Cons(self, call_chain)
                children = []
                for d in self.ds_unique:
                    try:
                        child = self.dsl.job_of_target[d]
                    except KeyError:
                        @self.dsl.file([self.dsl.meta(d, keep=True)], [])
                        def _(j):
                            raise exception.Err(f"No rule to make {d}")
                        child = self.dsl.job_of_target[d]
                    children.append(child.invoke(cc))
                self.task = _Task(self.dsl.task_context, functools.partial(_task_of_invoke, self, children), data=self, priority=self.priority)
                self.task.put()
        return self

    def wait(self):
        logger.debug(self)
        while not self.done.wait(timeout=1):
            pass

    def _enq(self):
        logger.debug(self)
        self.dsl.thread_pool.push_job(self)
        return self


class _PhonyJob(_Job):
    def __init__(
            self,
            f,
            ts,
            ds,
            desc,
            priority,
            dsl,
            data,
    ):
        if len(ts) != 1:
            raise exception.Err(f"PhonyJob with multiple targets is not supported: {f}, {ts}, {ds}")
        super().__init__(
            f,
            ts,
            ds,
            desc,
            priority,
            dsl=dsl,
            data=data,
        )


class _FileJob(_Job):
    def __init__(
            self,
            f,
            ts,
            ds,
            desc,
            use_hash,
            serial,
            priority,
            dsl,
            data,
    ):
        super().__init__(
            f,
            ts,
            ds,
            desc,
            priority,
            dsl=dsl,
            data=data,
        )
        self._use_hash = use_hash
        self.serial = serial

    def __repr__(self):
        ds = self.ds
        if self.ds and (len(self.ds) > 4):
            ds = ds[:2] + [_CDOTS]
        return f"{type(self).__name__}({self.ts}, {ds}, serial={self.serial})"

    def rm_targets(self):
        logger.info(f"rm_targets(%s)", self.ts)
        for t in self.ts_unique:
            meta = self.dsl.metadata[t]
            if not (("keep" in meta) and meta["keep"]):
                try:
                    self.dsl.rm(t)
                # todo: Catch errors from S3  https://stackoverflow.com/questions/33068055/boto3-python-and-how-to-handle-errors
                except (OSError, google.cloud.exceptions.NotFound, exception.NotFound) as e:
                    logger.info("Failed to remove %s", t)

    def need_update(self):
        if self.dsl.args.dry_run:
            for d in self.ds_unique:
                try:
                    if self.dsl.job_of_target[d].executed:
                        return True
                except KeyError:
                    pass
        return self._need_update()

    def _need_update(self):
        try:
            t_ts = min(_mtime_of(uri=t, use_hash=False, credential=self._credential_of(t)) for t in self.ts_unique)
        # todo: Catch errors from S3  https://stackoverflow.com/questions/33068055/boto3-python-and-how-to-handle-errors
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
        return self.dsl.time_of_dep_cache.get(d, functools.partial(_mtime_of, uri=d, use_hash=self._use_hash, credential=self._credential_of(d)))

    def _credential_of(self, uri):
        meta = self.dsl.metadata[uri]
        return meta["credential"] if "credential" in meta else None


class _ThreadPool(object):
    # It is not straightforward to support the `serial` argument by concurrent.future.

    def __init__(self, job_of_target, keep_going, n_max, n_serial_max, load_average, die_hooks):
        assert n_max > 0
        assert n_serial_max > 0
        self.deferred_errors = queue.Queue()
        self.job_of_target = job_of_target
        self._keep_going = keep_going
        self._n_max = n_max
        self._load_average = load_average
        self._die_hooks = die_hooks
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
                logger.debug("working on %s", j)
                def _post_exception():
                    logger.error(j)
                    e_str = _str_of_exception()
                    logger.error(e_str)
                    j.rm_targets()
                    if self._keep_going:
                        self.deferred_errors.put((j, e_str))
                    else:
                        self._die(e_str)
                try:
                    need_update = j.need_update()
                except:
                    need_update = None
                    _post_exception()
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
                        j.executed = True
                        j.successed = True
                    except Exception as e:
                        _post_exception()
                    self._n_running.dec()
                else:
                    j.executed = False
                    if need_update is None:
                        j.successed = False
                    else:
                        j.successed = True
                j.done.set()
                j.task.put()
                if j.serial:
                    self._serial_queue_lock.release()
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
            self._die(e_str)

    def _die(self, e):
        logger.critical(e)
        for h in self._die_hooks:
            try:
                h()
            except Exception:
                pass
        _thread.interrupt_main()


class _TaskContext:
    # It might be difficult to use asyncio with threading.

    def __init__(self):
        self.stop = False

        self.queue = queue.PriorityQueue()
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def task(self, f):
        t = _Task(self, f)
        t.put()
        return t

    def _loop(self):
        # print("vvvv", file=sys.stderr)
        # for t in self.queue.queue.copy():
        #     print("\t", t, file=sys.stderr)
        # print("^^^^", file=sys.stderr)
        while not self.stop:
            # print("vvvv", file=sys.stderr)
            # for t in self.queue.queue.copy():
            #     print("\t", t, file=sys.stderr)
            # print("^^^^", file=sys.stderr)
            task = self.queue.get(block=True)
            try:
                if next(task):
                    pass
                else:
                    task.put()
            except StopIteration:
                pass


class _Task:

    def __init__(self, ctx, f, data=None, priority=_PRIORITY_DEFAULT):
        self._ctx = ctx
        # `f` should behave as if a generator.
        self._g = iter(f(self))
        self.data = data
        self.priority = priority

        self.value = None
        self.error = None
        self.waited = queue.Queue()
        self.done = threading.Event()

    def __repr__(self):
        return f"{self.__class__.__name__} {self.data}"

    def __lt__(self, other):
        return self.priority < other.priority

    def __next__(self):
        if self.done.is_set():
            raise StopIteration
        try:
            return next(self._g)
        except StopIteration as e:
            self.value = e.value
            self.done.set()
            self._put_waited()
            raise
        except Exception as e:
            self.error = e
            self.done.set()
            raise

    def __iter__(self):
        return self

    def wait(self, child=None):
        """
        def f(self):
            child = ...
            yield self.wait(child)

        f.wait()
        """
        if child is None:
            self.done.wait()
            return self
        else:
            # I do not need a lock here since the `yield self.wait(child)` pattern does not occur in another thread.
            if child.done.is_set():
                return False
            child.waited.put(self)
            return self

    def put(self):
        self._ctx.queue.put(self)

    def _put_waited(self):
        assert self.done.is_set(), self
        while True:
            try:
                self.waited.get(block=False).put()
            except queue.Empty:
                break


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
        args.cut = set()
    args.cut = set(args.cut)
    return args


def _print_descriptions(jobs):
    for t, desc in sorted((t, j.desc) for j in jobs for t in j.ts_unique):
        print(t)
        if desc is not None:
            for l in desc.split("\n"):
                print("\t", l, sep="")


def _print_dependencies(jobs):
    # sorted(j.ts_unique) is used to make the output deterministic
    for j in sorted(jobs, key=lambda j: sorted(j.ts_unique)):
        j.write()


def _dependencies_dot_of(jobs):
    data = json.loads(_dependencies_json_of(jobs))
    fp = io.StringIO()
    node_of_name = dict()
    i = 0
    i_cluster = 0

    print("digraph G{", file=fp)
    for datum in data:
        i += 1
        i_cluster += 1
        action_node = "n" + str(i)
        print(action_node + "[label=\"○\"]", file=fp)

        for name in sorted(datum["ts_unique"]):
            node, i = _node_of(name, node_of_name, i)
            print(node + "[label=" + _escape(name) + "]", file=fp)
            print(node + " -> " + action_node, file=fp)

        print(f"subgraph cluster_{i_cluster}" "{", file=fp)
        for name in sorted(datum["ts_unique"]):
            print(node_of_name[name], file=fp)
        print("}", file=fp)

        for name in sorted(datum["ds_unique"]):
            node, i = _node_of(name, node_of_name, i)
            print(node + "[label=" + _escape(name) + "]", file=fp)
            print(action_node + " -> " + node, file=fp)
    print("}", end="", file=fp)
    return fp.getvalue()


def _dependencies_json_of(jobs):
    return json.dumps(
        [dict(ts_unique=list(j.ts_unique), ds_unique=list(j.ds_unique)) for j in sorted((j for j in jobs), key=lambda j: list(j.ts_unique))],
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


def _task_of_invoke(j, children, this):
    for child in children:
        yield this.wait(child.task)
    if all(child.successed for child in children):
        # j.task.put() is called inside _worker()
        # todo: _Task, _Job, and _ThreadPool should be decoupled.
        yield j._enq()
        assert j.done.is_set(), j
    else:
        j.done.set()


def _escape(s):
    return "\"" + "".join('\\"' if x == "\"" else x for x in s) + "\""


def _mtime_of(uri, use_hash, credential):
    puri = DSL.uriparse(uri)
    if puri.scheme == "file":
        assert (puri.netloc == "localhost"), puri
    if puri.scheme in resource.of_scheme:
        return resource.of_scheme[puri.scheme].mtime_of(uri, credential, use_hash)
    else:
        raise NotImplementedError(f"_mtime_of({repr(uri)}) is not supported")


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


def _do_nothing(*_):
    pass
