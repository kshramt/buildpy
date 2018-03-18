import collections
import inspect
import io
import itertools
import os
import struct
import subprocess
import sys
import urllib

from .. import exception


_URI = collections.namedtuple("_URI", ["scheme", "netloc", "path", "params", "query", "fragment"])


class cd(object):
    __slots__ = ["old", "new"]

    def __init__(self, new):
        self.new = new

    def __call__(self, f):
        with self as c:
            if len(inspect.signature(f).parameters) == 1:
                f(c)
            else:
                f()

    def __enter__(self):
        self.old = os.getcwd()
        os.chdir(self.new)
        return self

    def __exit__(self, *_):
        os.chdir(self.old)

    def __repr__(self):
        return f"#<{self.__class__.__name__} old={self.old}, new={self.new}>"


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


def let(f):
    f()


def loop(*lists, tform=itertools.product):
    """
    >>> loop([1, 2], ["a", "b"])(lambda x, y: print(x, y))
    1 a
    1 b
    2 a
    2 b
    >>> loop([(1, "a"), (2, "b")], tform=lambda x: x)(lambda x, y: print(x, y))
    1 a
    2 b
    """
    def deco(f):
        for xs in tform(*lists):
            f(*xs)
    return deco


def mkdir(path):
    return os.makedirs(path, exist_ok=True)


def dirname(path):
    """
    >>> dirname("")
    '.'
    >>> dirname("a")
    '.'
    >>> dirname("a/b")
    'a'
    """
    return os.path.dirname(path) or os.path.curdir


def jp(path, *more):
    """
    >>> jp(".", "a")
    'a'
    >>> jp("a", "b")
    'a/b'
    >>> jp("a", "b", "..")
    'a'
    >>> jp("a", "/b", "c")
    'a/b/c'
    """
    return os.path.normpath(os.path.sep.join((path, os.path.sep.join(more))))


def uriparse(uri):
    puri = urllib.parse.urlparse(uri)
    scheme = puri.scheme
    netloc = puri.netloc
    path = puri.path
    params = puri.params
    query = puri.query
    fragment = puri.fragment
    if scheme == "":
        scheme = "file"
    if (scheme == "file") and (netloc == ""):
        netloc = "localhost"
    if (scheme == "file") and (netloc != "localhost"):
        raise exception.Err("netloc of a file URI should be localhost: {uri}")
    return _URI(scheme=scheme, netloc=netloc, path=path, params=params, query=query, fragment=fragment)


def serialize(x):
    """
    Supported data types:

    * None
    * Integer (64 bits)
    * Float (64 bits)
    * String (UTF-8)
    * List
    * Dictionary
    """

    def _save(x, fp):
        if x is None:
            fp.write(b"n")
        elif isinstance(x, float):
            fp.write(b"f")
            fp.write(struct.pack("<d", x))
        elif isinstance(x, int):
            fp.write(b"i")
            _save_int(x, fp)
        elif isinstance(x, str):
            b = x.encode("utf-8")
            fp.write(b"s")
            _save_int(len(b), fp)
            fp.write(b)
        elif isinstance(x, list):
            fp.write(b"l")
            _save_int(len(x), fp)
            for v in x:
                _save(v, fp)
        elif isinstance(x, dict):
            fp.write(b"d")
            _save_int(len(x), fp)
            for k in sorted(x.keys()):
                _save(k, fp)
                _save(x[k], fp)
        else:
            raise ValueError(f"Unsupported argument {x} of type {type(x)} for `_save`")

    def _save_int(x, fp):
        return fp.write(struct.pack("<q", x))

    fp = io.BytesIO()
    _save(x, fp)
    return fp.getvalue()
