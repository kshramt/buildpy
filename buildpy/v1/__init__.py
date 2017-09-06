import _thread
import argparse
import hashlib
import itertools
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import warnings

__version__ = "1.8.0"


CACHE_DIR = os.path.join(os.getcwd(), ".cache", "buildpy")
BUF_SIZE = 65535


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
        return os.makedirs(path, exist_ok=True)

    @staticmethod
    def mv(src, dst):
        print(f"shutil.move({repr(src)}, {repr(dst)})", file=sys.stderr)
        return shutil.move(src, dst)

    @staticmethod
    def rm(path):
        print(f"os.remove({repr(path)})", file=sys.stderr)
        try:
            return os.remove(path)
        except:
            pass

    def __init__(self, use_hash=False):
        self._job_of_target = dict()
        self._f_of_phony = dict()
        self._deps_of_phony = dict()
        self._descs_of_phony = dict()
        self._use_hash = use_hash

    def file(self, targets, deps, desc=None, use_hash=None):
        if use_hash is None:
            use_hash = self._use_hash
        targets = _listize(targets)
        deps = _listize(deps)

        def _(f):
            j = _FileJob(f, targets, deps, [desc], use_hash)
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
        self._dry_run = _TBool(False)

    def __repr__(self):
        return f"{type(self).__name__}({repr(self.ts)}, {repr(self.ds)}, descs={repr(self.descs)})"

    def execute(self):
        self.f(self)

    def post_execute(self):
        pass

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
    def __init__(self, f, ts, ds, descs, use_hash):
        super().__init__(f, ts, ds, descs)
        self._use_hash = use_hash
        self._hash_orig = None
        self._hash_curr = None
        self._cache_path = None

    def post_execute(self):
        if self._use_hash and (self._hash_curr != self._hash_orig):
            _write_bytes(self._cache_path, self._hash_curr)

    def rm_targets(self):
        for t in self.ts:
            DSL.rm(t)

    def need_update(self):
        if self._dry_run.val():
            return True
        try:
            stat_ts = [os.stat(t) for t in self.ts]
            lack_targets = False
        except:
            lack_targets = True
        if lack_targets:
            if self._use_hash:
                self._make_hashes()
            return True
        else:
            if any(os.path.getmtime(d) > min(t.st_mtime for t in stat_ts) for d in self.unique_ds):
                if self._use_hash:
                    return self._make_hashes()
                else:
                    return True
            else:
                return False

    def _make_hashes(self):
        sorted_unique_ds = sorted(self.unique_ds)
        job_hash = hashlib.sha1(
            b"\0".join(t.encode("utf-8") for t in sorted(set(self.ts)))
            + b"\0\0"
            + b"\0".join(d.encode("utf-8") for d in sorted_unique_ds)
        ).hexdigest()
        self._cache_path = _jp(CACHE_DIR, job_hash[:2], job_hash[2:])
        try:
            with open(self._cache_path, "rb") as fp:
                self._hash_orig = fp.read()
        except:
            pass
        self._hash_curr = _hash_of_paths(sorted_unique_ds).digest()
        return self._hash_curr != self._hash_orig


class _ThreadPool:
    def __init__(self, dependent_jobs, deferred_errors, keep_going, n_max, load_average, dry_run):
        assert n_max > 0
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
        self._n_running = _TInt(0)

    def push_jobs(self, jobs):
        # pre-load `jobs` to avoid a situation where no active thread exist while a job is enqueued
        rem = max(len(jobs) - self._n_max, 0)
        for i in range(rem):
            self._queue.put(jobs[i])
        for i in range(rem, len(jobs)):
            self.push_job(jobs[i])

    def push_job(self, j):
        self._queue.put(j)
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
                            j.execute()
                    except Exception as e:
                        got_error = True
                        warnings.warn(repr(j))
                        warnings.warn(repr(e))
                        j.rm_targets()
                        if self._keep_going:
                            self._deferred_errors.put((j, e))
                        else:
                            self._die(e)
                    self._n_running.dec()
                j.set_n_rest(-1)
                if not got_error:
                    if need_update:
                        j.post_execute()
                    for t in j.ts:
                        # top targets does not have dependent jobs
                        for dj in self._dependent_jobs.get(t, ()):
                            dj.dec_n_rest()
                            dj._dry_run.set_self_or_eq(need_update and self._dry_run)
                            if dj.n_rest() == 0:
                                self.push_job(dj)
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            warnings.warn(str(exc_type) + " " + str(fname) + " " + str(exc_tb.tb_lineno))
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
            return self._queue.get(block=True, timeout=0.02)
        except queue.Empty:
            return False

    def _die(self, e):
        _thread.interrupt_main()
        sys.exit(e)


class _T:
    __slots__ = ("_lock", "_val")

    def __init__(self, val):
        self._lock = threading.Lock()
        self._val = val

    def val(self):
        with self._lock:
            return self._val


class _TSet(_T):
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


class _TStack(_T):
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


class _TInt(_T):
    def __init__(self, val):
        super().__init__(val)

    def inc(self):
        with self._lock:
            self._val += 1

    def dec(self):
        with self._lock:
            self._val -= 1


class _TBool(_T):
    def __init__(self, val):
        super().__init__(val)

    def set_self_or_eq(self, x):
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
        help="Print dependencies in DOT format, then exit. python build.py -Q | dot -Tpdf -Grankdir=LR -Nshape=plaintext -Ecolor='#00000088' >| workflow.pdf",
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
            print(node + "[label=\"" + name + "\"]")
            print(node + " -> " + action_node)
        for name in j.ds:
            node, i = _node_of(name, node_of_name, i)
            print(node + "[label=\"" + name + "\"]")
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


def _process_jobs(jobs, dependent_jobs, keep_going, n_jobs, load_average, dry_run):
    deferred_errors = queue.Queue()
    tp = _ThreadPool(dependent_jobs, deferred_errors, keep_going, n_jobs, load_average, dry_run)
    tp.push_jobs(jobs)
    tp.wait()
    if deferred_errors.qsize() > 0:
        warnings.warn("Following errors have thrown during the execution")
        for _ in range(deferred_errors.qsize()):
            j, e = deferred_errors.get()
            warnings.warn(repr(e))
            warnings.warn(repr(j))
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


def _hash_of_paths(paths, buf_size=BUF_SIZE):
    buf = bytearray(buf_size)
    h = hashlib.sha1(b"")
    for path in paths:
        h.update(os.path.getsize(path).to_bytes(64, sys.byteorder))
        with open(path, "rb") as fp:
            while True:
                n = fp.readinto(buf)
                if n <= 0:
                    break
                elif n < buf_size:
                    h.update(buf[:n])
                else:
                    h.update(buf)
    return h


def _write_bytes(path, b):
    os.makedirs(_dirname(path), exist_ok=True)
    with open(path, "wb") as fp:
        fp.write(b)


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
    """
    return os.path.normpath(os.path.join(path, *more))


def _loop(*lists, trans=itertools.product):
    """
    >>> _loop([1, 2], ["a", "b"])(lambda x, y: print(x, y))
    1 a
    1 b
    2 a
    2 b
    >>> _loop([(1, "a"), (2, "b")], trans=lambda x: x)(lambda x, y: print(x, y))
    1 a
    2 b
    """
    def deco(f):
        for xs in trans(*lists):
            f(*xs)
    return deco


def _do_nothing(*_):
    pass


def _dbg(*xs):
    print("DBG:\t" + "\t".join(str(x) for x in xs), file=sys.stderr)
