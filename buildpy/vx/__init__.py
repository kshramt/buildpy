import _thread
import argparse
import asyncio
import collections
import concurrent.futures
import datetime
import functools
import itertools
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
import typing
import uuid

import psutil

from ._log import logger
from . import _convenience
from . import _tval
from . import exception
from . import resource


__version__ = "8.1.2"
T1 = typing.TypeVar("T1")
T2 = typing.TypeVar("T2")
TK = typing.TypeVar("TK")
TV = typing.TypeVar("TV")
CLOSED = object()
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
    hash_dir_of = staticmethod(_convenience.hash_dir_of)

    def __init__(self, argv):
        self.args = _parse_argv(argv[1:])
        assert self.args.jobs > 0
        assert self.args.load_average > 0

        logger.setLevel(getattr(logging, self.args.log))
        self.job_of_target = _tval.NonOverwritableDict()
        self.jobs_of_key = _tval.TListOf()
        self.time_of_dep_cache = _tval.Cache()
        self.metadata = _tval.TDefaultDict()
        self.event_loop = _event_loop_of()
        self.executor = _ThreadPoolExecutor(
            n_max=self.args.jobs,
            n_serial_max=self.args.n_serial,
            load_average=self.args.load_average,
        )
        self.deferred_errors = queue.Queue()
        self.got_error = False
        self._cleanuped = False

        self.execution_log_dir = (
            _convenience.jp(self.args.execution_log_dir, self.args.id)
            if self.args.execution_log_dir_append_id
            else self.args.execution_log_dir
        )
        if self.execution_log_dir:
            _convenience.mkdir(self.execution_log_dir)
            with open(_convenience.jp(self.execution_log_dir, "meta.json"), "w") as fp:
                json.dump(
                    dict(
                        args=vars(self.args),
                        executable=sys.executable,
                        id=self.args.id,
                        version=sys.version,
                        __name__=__name__,
                        __version__=__version__,
                    ),
                    fp,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
        self.execution_logger_defined = _ExecutionLogger(
            self.execution_log_dir, "defined.jsonl"
        )
        self.execution_logger_invoked = _ExecutionLogger(
            self.execution_log_dir, "invoked.jsonl"
        )
        self.execution_logger_enqueued = _ExecutionLogger(
            self.execution_log_dir, "enqueued.jsonl"
        )
        self.execution_logger_executed = _ExecutionLogger(
            self.execution_log_dir, "executed.jsonl"
        )
        self.execution_logger_done = _ExecutionLogger(
            self.execution_log_dir, "done.jsonl"
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
        key=None,
        auto=False,
        auto_prefix=None,
        auto_use_ds_structure=False,
    ):
        """Declare a file job.
        Arguments:
            use_hash: Use the file checksum in addition to the modification time.
            serial: Jobs declared as `@file(serial=True)` runs exclusively to each other.
                The argument maybe useful to declare tasks that require a GPU or large amount of memory.
        """

        if cut:
            return None

        ts_prefix = ""
        if auto:
            auto_prefix = _coalesce(auto_prefix, self.args.auto_prefix)
            ds = _de_with_meta(dict(), deps)
            # The use of `+ "/" +` is intentional.
            ts_prefix = _convenience.jp(
                auto_prefix,
                _convenience.hash_dir_of(
                    dict(data=data, ds=ds if auto_use_ds_structure else _unique_of(ds))
                ),
            )
            targets = _prepend_prefix(ts_prefix, targets)
        j = _FileJob(
            None,
            targets,
            deps,
            desc,
            _coalesce(use_hash, self.args.use_hash),
            serial,
            priority=priority,
            dsl=self,
            data=data,
            key=key,
            ts_prefix=ts_prefix,
        )
        return j

    def phony(
        self,
        target,
        deps,
        desc=None,
        priority=_PRIORITY_DEFAULT,
        data=None,
        cut=False,
        key=None,
    ):
        if cut:
            return None

        j = _PhonyJob(
            _do_nothing, target, deps, desc, priority, dsl=self, data=data, key=key
        )
        return j

    def run(self):
        if self.args.descriptions:
            _print_descriptions(set(self.job_of_target.values()))
        elif self.args.dependencies:
            _print_dependencies(set(self.job_of_target.values()))
        elif self.args.dependencies_dot:
            print(self.dependencies_dot())
        elif self.args.dependencies_json:
            print(self.dependencies_json())
        else:
            try:
                for target in self.args.targets:
                    self.job_of_target[target].invoke()
                for target in self.args.targets:
                    self.job_of_target[target].wait()
            except KeyboardInterrupt as e:
                self._cleanup()
                raise
            if self.deferred_errors.qsize() > 0:
                logger.error("Following errors have thrown during the execution")
                for _ in range(self.deferred_errors.qsize()):
                    j, e_str = self.deferred_errors.get()
                    logger.error(e_str)
                    logger.error(j)
                raise exception.Err("Execution failed.")

    def meta(self, uri, **kwargs):
        self.metadata[uri] = kwargs
        return uri

    def check_existence_only(self, uri):
        return _with_meta(uri, check_existence_only=True)

    def rm(self, uri):
        logger.info(uri)
        puri = self.uriparse(uri)
        meta = self.metadata[uri]
        credential = meta["credential"] if "credential" in meta else None
        if puri.scheme == "file":
            assert puri.netloc == "localhost", puri
        if puri.scheme in resource.of_scheme:
            return resource.of_scheme[puri.scheme].rm(uri, credential)
        else:
            raise NotImplementedError(f"rm({repr(uri)}) is not supported")

    def dependencies_json(self):
        return _dependencies_json_of(set(self.job_of_target.values()))

    def dependencies_dot(self):
        return _dependencies_dot_of(set(self.job_of_target.values()))

    def _cleanup(self):
        if self._cleanuped:
            return
        self._cleanuped = True
        self.executor.shutdown(wait=False)
        self.event_loop.call_soon_threadsafe(self.event_loop.stop)
        # self.event_loop.call_soon_threadsafe(self.event_loop.close)
        if self.args.terminate_subprocesses:
            _terminate_subprocesses()

    def die(self, e: str):
        logger.critical(e)
        self._cleanup()
        _thread.interrupt_main()


# Internal use only.


class _ExecutionLogger:
    def __init__(self, dir_, file):
        if dir_:
            self.path = _convenience.jp(dir_, file)
            _convenience.mkdir(_convenience.dirname(self.path))
            self.fp = open(self.path, "w")
            self.counter = itertools.count(1)
            self.queue = queue.Queue()
            self.processor = threading.Thread(target=self._worker, daemon=True)
            self.processor.start()
        else:
            self.queue = queue.Queue(maxsize=0)  # to support `al.queue.put(x)`

    def _worker(self):
        while True:
            x = self.queue.get(block=True)
            x = _set_unique(x, "t", datetime.datetime.utcnow().isoformat())
            x = _set_unique(x, "i", next(self.counter))
            json.dump(x, self.fp, ensure_ascii=False, sort_keys=True)
            self.fp.write("\n")
            self.fp.flush()


class _Job:
    def __init__(self, f, ts, ds, desc, priority, dsl, data, key):
        self.done = threading.Event()
        self.adone = asyncio.Event()
        self.executed = False  # This flag is used to propagate dry-run.
        self.successed = False  # True if self.execute did not raise an error
        self.serial = False
        self.metadata = _tval.TDefaultDict()

        self.f = f
        self.ts = _de_with_meta(self.metadata, ts)
        self.ds = _de_with_meta(self.metadata, ds)
        self.ts_unique = _unique_of(self.ts)
        self.ds_unique = _unique_of(self.ds)
        self.desc = desc
        self.priority = priority
        self.dsl = dsl
        self.key = key

        self.invoked = False
        self.run_future = None

        for t in self.ts_unique:
            self.dsl.job_of_target[t] = self
        self.dsl.jobs_of_key.append(key, self)

        # User data.
        self.data = data
        self._execution_log_data = _convenience.dictify(
            dict(
                data=self.data,
                desc=self.desc,
                ds=self.ds,
                priority=self.priority,
                serial=self.serial,
                ts=self.ts,
                key=self.key,
            )
        )
        dsl.execution_logger_defined.queue.put(self.to_execution_log_data())

    def __repr__(self):
        return f"{type(self).__name__}({_cdotify(self.ts_unique)}, {_cdotify(self.ds_unique)})"

    def __call__(self, f):
        self.f = f
        return self

    def __lt__(self, other):
        return (self.serial and not other.serial) or self.priority < other.priority

    def execute(self):
        logger.debug(self)
        assert not self.done.is_set(), self
        assert not self.adone.is_set(), self
        if self.dsl.args.dry_run:
            self.write()
        else:
            self.f(self)
        self.dsl.execution_logger_executed.queue.put(self.to_execution_log_data())

    def rm_targets(self):
        pass

    def need_update(self):
        return True

    def write(self, file=sys.stdout):
        logger.debug(self)
        for t in self.ts_unique:
            print(t, file=file)
        for d in self.ds_unique:
            print("\t", d, sep="", file=file)
        print(file=file)

    def invoke(self):
        self.dsl.event_loop.call_soon_threadsafe(
            self.dsl.event_loop.create_task, self.ainvoke(())
        )
        return self

    def wait(self):
        # We can just wait a threading.Event since `wait` is called only in the main thread or an executor, and no event loop will be block by the call.
        logger.debug(self)
        while not self.done.wait(timeout=1):
            pass

    def to_execution_log_data(self):
        return {"successed": self.successed, **self._execution_log_data}

    async def ainvoke(self, call_chain):
        # This coroutine runs inside self.dsl.event_loop.
        logger.debug(self)
        if not self.invoked:
            self.invoked = True
            self.dsl.execution_logger_invoked.queue.put(self.to_execution_log_data())
            if _contains(self, call_chain):
                raise exception.Err(
                    f"A circular dependency detected: {self} for {call_chain}"
                )
            cc = (self, call_chain)
            children = []
            for d in self.ds_unique:
                try:
                    child = self.dsl.job_of_target[d]
                except KeyError:

                    @_convenience.let
                    def _(d=d):
                        @self.dsl.file([self.dsl.meta(d, keep=True)], [])
                        def _(j):
                            raise exception.Err(f"No rule to make {d}")

                    child = self.dsl.job_of_target[d]
                self.dsl.event_loop.create_task(child.ainvoke(cc))
                children.append(child)
            for child in children:
                await child.adone.wait()
            if all(child.successed for child in children):
                self.dsl.event_loop.run_in_executor(
                    self.dsl.executor, self._to_work_item()
                )
                self.dsl.execution_logger_enqueued.queue.put(
                    self.to_execution_log_data()
                )
            else:
                # todo: Move the done calls into j._enq() or a function therein.
                # Order matters.
                self.done.set()
                self.adone.set()

    def _to_work_item(self):
        return _WorkItem(self)

    def post_exception(self):
        logger.error(self)
        e_str = _str_of_exception()
        self.rm_targets()
        if self.dsl.args.keep_going:
            logger.error(e_str)
            self.dsl.deferred_errors.put((self, e_str))
        else:
            self.dsl.got_error = True
            self.dsl.die(e_str)


class _PhonyJob(_Job):
    def __init__(self, f, ts, ds, desc, priority, dsl, data, key):
        if len(_unique_of(ts)) != 1:
            raise exception.Err(
                f"PhonyJob with multiple targets is not supported: {f}, {ts}, {ds}"
            )
        super().__init__(f, ts, ds, desc, priority, dsl=dsl, data=data, key=key)


class _FileJob(_Job):
    def __init__(
        self, f, ts, ds, desc, use_hash, serial, priority, dsl, data, key, ts_prefix
    ):
        super().__init__(f, ts, ds, desc, priority, dsl=dsl, data=data, key=key)
        self._use_hash = use_hash
        self.serial = serial
        self.ts_prefix = ts_prefix

    def __repr__(self):
        return f"{type(self).__name__}({_cdotify(self.ts_unique)}, {_cdotify(self.ds_unique)}, serial={self.serial})"

    def rm_targets(self):
        logger.info(f"rm_targets(%s)", self.ts)
        for t in self.ts_unique:
            meta = self.dsl.metadata[t]
            if not ("keep" in meta and meta["keep"]):
                try:
                    self.dsl.rm(t)
                except resource.exceptions as e:
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
        # Intentionally create hash caches for the all set(self.ds).
        t_ds = -float("inf")
        for d in self.ds_unique:
            t = self._time_of_dep_from_cache(d)
            if (
                "check_existence_only" in self.metadata[d]
                and self.metadata[d]["check_existence_only"]
            ):
                t = -float("inf")
            if t > t_ds:
                t_ds = t
        try:
            t_ts = min(
                _mtime_of(
                    uri=t,
                    credential=self._credential_of(t),
                    use_hash=False,
                    resource_hash_dir=self.dsl.args.resource_hash_dir,
                )
                for t in self.ts_unique
            )
        except resource.exceptions:
            return True
        return t_ds > t_ts
        # Use of `>` instead of `>=` is intentional.
        # In theory, t_deps < t_targets if targets were made from deps, and thus you might expect ≮ (>=).
        # However, t_deps > t_targets should hold if the deps have modified *after* the creation of the targets.
        # As it is common that an accidental modification of deps is made by slow human hands
        # whereas targets are created by a fast computer program, I expect that use of > here to be better.

    def _time_of_dep_from_cache(self, d):
        """
        Return: the last hash time.
        """
        return self.dsl.time_of_dep_cache.get(
            d,
            functools.partial(
                _mtime_of,
                uri=d,
                credential=self._credential_of(d),
                use_hash=self._use_hash,
                resource_hash_dir=self.dsl.args.resource_hash_dir,
            ),
        )

    def _credential_of(self, uri):
        meta = self.dsl.metadata[uri]
        return meta["credential"] if "credential" in meta else None


def _make_channel(name=None, loop=None):
    """
    >>> cin, cout1 = _make_channel()
    >>> cout2 = cout1.dup()
    >>> cin.put(1)
    """
    chan = _Channel(name=name, loop=loop)
    return chan.cin, chan.couts[0]


class _Channel:
    def __init__(self, name=None, loop=None):
        self.name = name
        self.lock = threading.Lock()
        self.buf = []
        if loop is None:
            self.loop = asyncio.get_event_loop()
        else:
            self.loop = loop

        self.cin = _ChannelInput(chan=self)
        # todo: use WeakSet.
        self.couts = [_ChannelOutput(chan=self)]
        self.closed = False

    def __len__(self):
        return len(self.buf)

    def put(self, x):
        with self.lock:
            if self.closed:
                raise ValueError(f"You should not put {x} to the closed channel {self}")
            self.buf.append(x)
        for cout in self.couts:
            self.loop.call_soon_threadsafe(cout.wakeup_next)

    def dup(self):
        cout = _ChannelOutput(chan=self)
        with self.lock:
            self.couts.append(cout)
        return cout

    def close(self):
        with self.lock:
            for cout in self.couts:
                cout.put(CLOSED)
            self.closed = True


class _ChannelInput:
    def __init__(self, chan):
        self.chan = chan

    def put(self, x):
        return self.chan.put(x)

    def close(self):
        return self.chan.close()


class _ChannelOutput:
    def __init__(self, chan):
        self.chan = chan
        self._ptr = 0
        self._getters = collections.deque()

    def __len__(self):
        return len(self.chan)

    async def get(self):
        while len(self) <= self._ptr:
            getter = self.chan.loop.create_future()
            self._getters.append(getter)
            # See https://github.com/python/asyncio/pull/269 for the discussion.
            try:
                # CancelledError is raised if the caller is `cancele()`ed.
                await getter
            except:  # Catch everything since we (including Guido) are not sure if CancelledError is the only exception to be raised.
                # If the exception is not raised by a `cancel()` call (we are unsure of the exact situation), `getter`'s state could be `_PENDING`.
                # This `cancel()` call is written for safety.
                getter.cancel()
                # If `getter` was waked up by `put`, and then the caller is `cancel()`ed (hence `getter.cancelled()` is `False`), we should wake up another getter.
                if self._buf and not getter.cancelled():
                    self.wakeup_next(self._getters)
                raise
        x = self.chan.buf[self._ptr]
        self._ptr += 1
        return x

    def dup(self):
        return self.chan.dup()

    def wakeup_next(self, waiters):
        while waiters:
            waiter = waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                break


class _WorkItem:
    def __init__(self, j):
        self.j = j
        self.future = concurrent.futures.Future()
        self.serial = j.serial
        self.priority = j.priority

    def __repr__(self):
        return f"{self.__class__.__name__}({self.j})"

    def __call__(self):
        if not self.future.set_running_or_notify_cancel():
            return
        try:
            result = self._run()
        except Exception as e:
            self.future.set_exception(e)
        else:
            self.future.set_result(result)

    def _run(self):
        if self.j.dsl.got_error:
            logger.debug("Early return by an error %s", self.j)
            return
        try:
            logger.debug("Running %s", self.j)
            try:
                need_update = self.j.need_update()
            except Exception:
                need_update = None
                self.j.post_exception()
            if need_update:
                try:
                    self.j.execute()
                    self.j.executed = True
                    self.j.successed = True
                except Exception:
                    self.j.post_exception()
            else:
                self.j.executed = False
                if need_update is None:
                    self.j.successed = False
                else:
                    self.j.successed = True
            self.j.done.set()
            self.j.dsl.event_loop.call_soon_threadsafe(self.j.adone.set)
            self.j.dsl.execution_logger_done.queue.put(self.j.to_execution_log_data())
        except Exception:  # Propagate Exception caused by a bug in buildpy code to the main thread.
            e_str = _str_of_exception()
            self.j.dsl.die(e_str)

    def __lt__(self, other):
        return self.j < other.j


class _ThreadPoolExecutor:
    def __init__(self, n_max, n_serial_max, load_average):
        if n_max < 1:
            raise ValueError(f"n_max = {n_max} should be greater than 0")
        if n_serial_max < 1:
            raise ValueError(f"n_serial_max = {n_serial_max} should be greater than 0")
        self._n_max = n_max
        self._load_average = load_average
        self._threads = set()
        self._threads_lock = threading.Lock()
        self._queue = queue.PriorityQueue()
        self._serial_queue = queue.PriorityQueue()
        self._serial_queue_lock = threading.Semaphore(n_serial_max)
        self._n_running = _tval.TInt(0)
        self._shutdown = False

    def submit(self, wi: _WorkItem):
        logger.debug(wi)
        if self._shutdown:
            return
        if wi.serial:
            self._serial_queue.put(wi)
        else:
            self._queue.put(wi)
        with self._threads_lock:
            if len(self._threads) < 1 or (
                len(self._threads) < self._n_max
                and os.getloadavg()[0] <= self._load_average
            ):
                t = threading.Thread(target=self._worker, daemon=True)
                self._threads.add(t)
                t.start()
        return wi.future

    def shutdown(self, wait=True):
        self._shutdown = True

    def _worker(self):
        logger.debug("Start a new worker")
        # No protection against BuildPy's internal error.
        while True:
            if self._shutdown:
                break
            wi = None
            logger.debug("Try to get a work item")
            if self._serial_queue_lock.acquire(blocking=False):
                try:
                    wi = self._serial_queue.get(block=False)
                    assert wi.serial
                except queue.Empty:
                    self._serial_queue_lock.release()
            if wi is None:
                try:
                    wi = self._queue.get(block=True, timeout=0.01)
                except queue.Empty:
                    break
            logger.debug("Working on %s", wi)

            if math.isfinite(self._load_average):
                while (
                    self._n_running.val() > 0
                    and os.getloadavg()[0] > self._load_average
                ):
                    time.sleep(1)
            self._n_running.inc()
            wi()
            self._n_running.dec()
            if wi.serial:
                self._serial_queue_lock.release()
        # todo: Do not discard idle threads immediately.
        logger.debug("Stopping a worker")
        with self._threads_lock:
            self._threads.remove(threading.current_thread())


class _WithMeta:
    def __init__(self, val, **kwargs):
        self.val = val
        self.meta = kwargs


def _parse_argv(argv):
    buildpy_dir = _convenience.jp(os.getcwd(), ".buildpy")

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("targets", nargs="*")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--log",
        default="WARNING",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set log level.",
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=1, help="Number of parallel external jobs."
    )
    parser.add_argument(
        "--n-serial", type=int, default=1, help="Number of parallel serial jobs."
    )
    parser.add_argument(
        "-l",
        "--load-average",
        type=float,
        default=float("inf"),
        help="No new job is started if there are other running jobs and the load average is higher than the specified value.",
    )
    parser.add_argument(
        "-k",
        "--keep-going",
        action="store_true",
        default=False,
        help="Keep going unrelated jobs even if some jobs fail.",
    )
    parser.add_argument(
        "-D",
        "--descriptions",
        action="store_true",
        default=False,
        help="Print descriptions, then exit.",
    )
    parser.add_argument(
        "-P",
        "--dependencies",
        action="store_true",
        default=False,
        help="Print dependencies, then exit.",
    )
    parser.add_argument(
        "-Q",
        "--dependencies-dot",
        type=str,
        const="/dev/stdout",
        nargs="?",
        help=f"Print dependencies in the DOT format, then exit. {os.path.basename(sys.executable)} build.py -Q | dot -Tpdf -Grankdir=LR -Nshape=plaintext -Ecolor='#00000088' >| workflow.pdf",
    )
    parser.add_argument(
        "-J",
        "--dependencies-json",
        type=str,
        const="/dev/stdout",
        nargs="?",
        help=f"Print dependencies in the JSON format, then exit. {os.path.basename(sys.executable)} build.py -J | jq .",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true", default=False, help="Dry-run."
    )
    parser.add_argument(
        "--cut",
        action="append",
        help="Cut the DAG at the job of the specified resource. You can specify --cut=target multiple times.",
    )
    parser.add_argument("--use_hash", type=_bool_of_str, default=True)
    parser.add_argument("--terminate_subprocesses", type=_bool_of_str, default=True)
    parser.add_argument(
        "--id",
        default=(
            datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
            + "-"
            + str(uuid.uuid4())
        ),
    )
    parser.add_argument(
        "--execution_log_dir",
        default=None,
        help="Directory to store the execution logs.",
    )
    parser.add_argument(
        "--execution_log_dir_append_id", type=_bool_of_str, default=False
    )
    parser.add_argument(
        "--resource_hash_dir",
        default=_convenience.jp(buildpy_dir, "resource_hash"),
        help="Directory to store resource hash values.",
    )
    parser.add_argument(
        "--auto_prefix",
        default=_convenience.jp(buildpy_dir, "auto"),
        help="Directory to store automatically named resources.",
    )
    parser.add_argument("--message", default="", help="Message.")
    args = parser.parse_args(argv)
    assert args.jobs > 0
    assert args.n_serial > 0
    assert args.load_average > 0
    if not args.targets:
        args.targets.append("all")
    if args.cut is None:
        args.cut = set()
    args.cut = sorted(set(args.cut))
    if args.execution_log_dir is None:
        args.execution_log_dir = _convenience.jp(buildpy_dir, "log", args.id)
    return args


def _print_descriptions(jobs):
    for t, desc in sorted((t, j.desc) for j in jobs for t in j.ts_unique):
        print(t)
        if desc is not None:
            for l in desc.split("\n"):
                print("\t", l, sep="")


def _print_dependencies(jobs):
    # sorted(j.ts_unique) is used to make the output deterministic
    for j in sorted(jobs, key=lambda j: j.ts_unique):
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
        print(action_node + '[label="○"]', file=fp)

        for name in sorted(datum["ts_unique"]):
            node, i = _node_of(name, node_of_name, i)
            print(node + "[label=" + _escape(name) + "]", file=fp)
            print(node + " -> " + action_node, file=fp)

        if len(datum["ts_unique"]) > 1:
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
        [
            dict(ts_unique=j.ts_unique, ds_unique=j.ds_unique)
            for j in sorted((j for j in jobs), key=lambda j: j.ts_unique)
        ],
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


def _escape(s: str):
    return '"' + "".join('\\"' if x == '"' else x for x in s) + '"'


def _mtime_of(uri, use_hash, credential, resource_hash_dir):
    puri = DSL.uriparse(uri)
    if puri.scheme == "file":
        assert puri.netloc == "localhost", puri
    if puri.scheme in resource.of_scheme:
        return resource.of_scheme[puri.scheme].mtime_of(
            uri, credential, use_hash, resource_hash_dir
        )
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


def _with_meta(x, **kwargs):
    if isinstance(x, _WithMeta):
        return _WithMeta(x.val, **{**x.meta, **kwargs})
    else:
        return _WithMeta(x, **kwargs)


def _event_loop_of():
    loop = asyncio.get_event_loop()
    th = threading.Thread(target=loop.run_forever, daemon=True)
    th.start()
    return loop


def _prepend_prefix(prefix, x):
    def impl(x):
        if isinstance(x, _WithMeta):
            return _WithMeta(impl(x.val), **x.meta)
        elif isinstance(x, list):
            return [impl(v) for v in x]
        elif isinstance(x, dict):
            return {k: impl(v) for k, v in x.items()}
        elif isinstance(x, argparse.Namespace):
            return argparse.Namespace(**impl(vars(x)))
        else:
            return _convenience.jp(prefix, x)

    return impl(x)


def _de_with_meta(metadata, x):
    def impl(x):
        if isinstance(x, _WithMeta):
            metadata[x.val] = x.meta
            return x.val
        elif isinstance(x, list):
            return [impl(v) for v in x]
        elif isinstance(x, dict):
            return {k: impl(v) for k, v in x.items()}
        elif isinstance(x, argparse.Namespace):
            return argparse.Namespace(**impl(vars(x)))
        else:
            return x

    return impl(x)


def _unique_of(xs):
    ret = set()

    def impl(x):
        if isinstance(x, list):
            for y in x:
                impl(y)
        elif isinstance(x, dict):
            for y in x.values():
                impl(y)
        elif isinstance(x, argparse.Namespace):
            impl(vars(x))
        else:
            ret.add(x)

    impl(xs)
    return sorted(ret)


def _coalesce(x: typing.Optional[T1], default: T1):
    return default if x is None else x


def _contains(v, c):
    if c:
        return (c[0] == v) or _contains(v, c[1])
    else:
        return False


def _set_unique(d: typing.MutableMapping[TK, TV], k: TK, v: TV):
    if k in d:
        raise exception.Err(f"{repr(k)} in {repr(d)}")
    d[k] = v
    return d


def _cdotify(xs):
    if xs and len(xs) > 4:
        xs = xs[:3] + [_CDOTS]
    return xs


def _bool_of_str(x):
    if x == "True":
        return True
    elif x == "False":
        return False
    else:
        raise ValueError(f"Unsupported value: {x}")


def _do_nothing(*_):
    pass
