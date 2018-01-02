import _thread
import argparse
import fcntl
import hashlib
import itertools
import logging
import math
import os
import queue
import shutil
import struct
import subprocess
import sys
import threading
import time

__version__ = "2.6.0"


CACHE_DIR = os.path.join(os.getcwd(), ".cache", "buildpy")
BUF_SIZE = 65535

logger = logging.getLogger(__name__)


class DSL:

    @staticmethod
    def loop(*args, **kwargs):
        return _loop(*args, **kwargs)

    @staticmethod
    def let(f):
        f()

    @staticmethod
    def sh(
        s,
        check=True,
        encoding="utf-8",
        env=None,
        executable="/bin/bash",
        shell=True,
        universal_newlines=True,
        **kwargs,
    ):
        print(s, file=sys.stderr)
        return subprocess.run(
            s,
            check=check,
            encoding=encoding,
            env=env,
            executable=executable,
            shell=shell,
            universal_newlines=universal_newlines,
            **kwargs,
        )

    @staticmethod
    def dirname(path):
        return _dirname(path)

    @staticmethod
    def jp(path, *more):
        return _jp(path, *more)

    @staticmethod
    def mkdir(path):
        print(f"os.makedirs({repr(path)}, exist_ok=True)", file=sys.stderr)
        return _mkdir(path)

    @staticmethod
    def mv(src, dst):
        print(f"shutil.move({repr(src)}, {repr(dst)})", file=sys.stderr)
        return shutil.move(src, dst)

    @staticmethod
    def rm(path):
        print(f"os.remove({repr(path)})", file=sys.stderr)
        try:
            return os.remove(path)
        except Exception:
            pass

    def __init__(self, use_hash=False):
        self._job_of_target = dict()
        self._f_of_phony = dict()
        self._deps_of_phony = dict()
        self._descs_of_phony = dict()
        self._use_hash = use_hash
        self._time_of_dep_cache = _Cache()

    def file(self, targets, deps, desc=None, use_hash=None, serial=False):
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
            j = _FileJob(f, targets, deps, [desc], use_hash, serial, self._time_of_dep_cache)
            for t in targets:
                _set_unique(self._job_of_target, t, j)
            return _do_nothing
        return _

    def phony(self, target, deps, desc=None):
        self._deps_of_phony.setdefault(target, []).extend(_listize(deps))
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
                    _nil,
                )
            _process_jobs(leaf_jobs, dependent_jobs, args.keep_going, args.jobs, args.n_serial, args.load_average, args.dry_run)

    def main(self, argv):
        args = _parse_argv(argv[1:])
        logger.setLevel(getattr(logging, args.log.upper()))
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
        self._dry_run = _TBool(False)

    def __repr__(self):
        return f"{type(self).__name__}({repr(self.ts)}, {repr(self.ds)}, descs={repr(self.descs)})"

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
    def __init__(self, f, ts, ds, descs):
        if len(ts) != 1:
            raise Err(f"PhonyJob with multiple targets is not supported: {f}, {ts}, {ds}")
        super().__init__(f, ts, ds, descs)


class _FileJob(_Job):
    def __init__(self, f, ts, ds, descs, use_hash, serial, time_of_dep_cache):
        super().__init__(f, ts, ds, descs)
        self._time_of_dep = _hash_time_of if use_hash else _time_of
        self._serial = _TBool(serial)
        self._time_of_dep_cache = time_of_dep_cache
        self._hash_orig = None
        self._hash_curr = None
        self._cache_path = None

    def __repr__(self):
        return f"{type(self).__name__}({repr(self.ts)}, {repr(self.ds)}, descs={repr(self.descs)}, serial={self.serial()})"

    def serial(self):
        return self._serial.val()

    def rm_targets(self):
        for t in self.ts:
            DSL.rm(t)

    def need_update(self):
        if self.dry_run():
            return True
        try:
            t_ts = min(os.path.getmtime(t) for t in self.ts)
        except Exception:
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
        return self._time_of_dep_cache.get(d, lambda : self._time_of_dep(d, CACHE_DIR))


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
        self._threads = _TSet()
        self._unwaited_threads = _TSet()
        self._threads_loc = threading.Lock()
        self._queue = queue.Queue()
        self._serial_queue = queue.Queue()
        self._serial_queue_lock = threading.Semaphore(n_serial_max)
        self._n_running = _TInt(0)

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
            except KeyError as e:
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
                        logger.error(repr(e))
                        j.rm_targets()
                        if self._keep_going:
                            self._deferred_errors.put((j, e))
                        else:
                            self._die(e)
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
        except Exception as e: # Propagate Exception caused by a bug in buildpy code to the main thread.
            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            logger.error(str(exc_type) + " " + str(fname) + " " + str(exc_tb.tb_lineno))
            logger.error(repr(e))
            self._die(e)
        with self._threads_loc:
            try:
                self._threads.remove(threading.current_thread())
                self._unwaited_threads.remove(threading.current_thread())
            except Exception:
                pass

    def _die(self, e):
        _thread.interrupt_main()
        sys.exit(e)


class _TVal:
    __slots__ = ("_lock", "_val")

    def __init__(self, val, lock=threading.Lock):
        self._lock = lock()
        self._val = val

    def val(self):
        with self._lock:
            return self._val


class _TDict(_TVal):

    def __init__(self):
        super().__init__(dict())

    def __setitem__(self, k, v):
        with self._lock:
            self._val[k] = v

    def __getitem__(self, k):
        with self._lock:
            return self._val[k]


class _Cache:

    def __init__(self):
        self._data_lock_dict = dict()
        self._data_lock_dict_lock = threading.Lock()
        self._data = _TDict()

    def get(self, k, make_val):
        with self._data_lock_dict_lock:
            # This block finishes instantly
            try:
                k_lock = self._data_lock_dict[k]
            except Exception:
                k_lock = threading.Lock()
                self._data_lock_dict[k] = k_lock

        with k_lock:
            try:
                return self._data[k]
            except Exception: # This block could take time to finish
                val = make_val()
                self._data[k] = val
                return val


class _TSet(_TVal):
    def __init__(self):
        super().__init__(set())

    def __len__(self):
        with self._lock:
            return len(self._val)

    def add(self, x):
        with self._lock:
            self._val.add(x)

    def remove(self, x):
        with self._lock:
            self._val.remove(x)

    def pop(self):
        with self._lock:
            return self._val.pop()


class _TStack(_TVal):
    class Empty(Exception):
        def __init__(self):
            pass

    def __init__(self):
        super().__init__([])

    def put(self, x):
        with self._lock:
            self._val.append(x)

    def pop(self, block=True, timeout=-1):
        success = self._lock.acquire(blocking=block, timeout=timeout)
        if success:
            if self._val:
                ret = self._val.pop()
            else:
                success = False
        self._lock.release()
        if success:
            return ret
        else:
            raise self.Empty()


class _TInt(_TVal):
    def __init__(self, val):
        super().__init__(val)

    def inc(self):
        with self._lock:
            self._val += 1

    def dec(self):
        with self._lock:
            self._val -= 1


class _TBool(_TVal):
    def __init__(self, val):
        super().__init__(val)

    def set_self_or(self, x):
        with self._lock:
            self._val = self._val or x


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
    print("digraph G{")
    for j in sorted(set(job_of_target.values()), key=lambda j: j.ts):
        i += 1
        action_node = "n" + str(i)
        print(action_node + "[label=\"○\"]")
        for name in j.ts:
            node, i = _node_of(name, node_of_name, i)
            print(node + "[label=" + _escape(name) + "]")
            print(node + " -> " + action_node)
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
            j, e = deferred_errors.get()
            logger.error(repr(e))
            logger.error(repr(j))
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
    raise NotImplementedError(f"_listize({repr(x)}: {type(x)})")


def _set_unique(d, k, v):
    if k in d:
        raise Err(f"{repr(k)} in {repr(d)}")
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


def _time_of(path, cache_dir):
    return os.path.getmtime(path)


def _hash_time_of(path, cache_dir):
    """
    path cache_path -> min(path_time, cache_time)
    """
    logger.debug(str(threading.get_ident()) + "\t" + path)
    cache_path = _jp(cache_dir, os.path.abspath(path))
    t_path = os.path.getmtime(path)
    try:
        cache_path_stat = os.stat(cache_path)
    except Exception:
        h_path = _hash_of_path(path)
        _dump_hash_time_cache(cache_path, t_path, h_path)
        return t_path

    if cache_path_stat.st_size != 28:
        h_path = _hash_of_path(path)
        _dump_hash_time_cache(cache_path, t_path, h_path)
        return t_path
    else:
        try:
            t_cache, h_cache = _load_hash_time_cache(cache_path)
        except Exception:
            h_path = _hash_of_path(path)
            _dump_hash_time_cache(cache_path, t_path, h_path)
            return t_path

        if cache_path_stat.st_mtime > os.path.getmtime(path):
            return t_cache
        else:
            h_path = _hash_of_path(path)
            if h_path != h_cache:
                _dump_hash_time_cache(cache_path, t_path, h_path)
                return t_path
            else:
                t_now = time.time()
                os.utime(cache_path, (t_now, t_now))
                return t_cache


def _dump_hash_time_cache(cache_path, t_path, h_path):
    logger.debug(str(threading.get_ident()) + "\t" + cache_path)
    _mkdir(_dirname(cache_path))
    with open(cache_path, "wb") as fp:
        fcntl.flock(fp, fcntl.LOCK_EX)
        fp.write(struct.pack("d", t_path))
        fp.write(h_path)


def _load_hash_time_cache(cache_path):
    with open(cache_path, "rb") as fp:
        fcntl.flock(fp, fcntl.LOCK_EX)
        t = struct.unpack("d", fp.read(8))[0]
        h = fp.read(20)
        return t, h


def _hash_of_path(path, buf_size=BUF_SIZE):
    logger.debug(path)
    buf = bytearray(buf_size)
    h = hashlib.sha1(b"")
    with open(path, "rb") as fp:
        while True:
            n = fp.readinto(buf)
            if n <= 0:
                break
            elif n < buf_size:
                h.update(buf[:n])
            else:
                h.update(buf)
    return h.digest()


def _mkdir(path):
    return os.makedirs(path, exist_ok=True)


def _dirname(path):
    """
    >>> _dirname("")
    '.'
    >>> _dirname("a")
    '.'
    >>> _dirname("a/b")
    'a'
    """
    return os.path.dirname(path) or os.path.curdir


def _jp(path, *more):
    """
    >>> _jp(".", "a")
    'a'
    >>> _jp("a", "b")
    'a/b'
    >>> _jp("a", "b", "..")
    'a'
    >>> _jp("a", "/b", "c")
    'a/b/c'
    """
    return os.path.normpath(os.path.sep.join((path, os.path.sep.join(more))))


def _loop(*lists, tform=itertools.product):
    """
    >>> _loop([1, 2], ["a", "b"])(lambda x, y: print(x, y))
    1 a
    1 b
    2 a
    2 b
    >>> _loop([(1, "a"), (2, "b")], tform=lambda x: x)(lambda x, y: print(x, y))
    1 a
    2 b
    """
    def deco(f):
        for xs in tform(*lists):
            f(*xs)
    return deco


def _do_nothing(*_):
    pass
