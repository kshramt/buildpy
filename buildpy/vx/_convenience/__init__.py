import argparse
import collections
import hashlib
import inspect
import io
import itertools
import os
import subprocess
import sys
import urllib

from .. import exception


_URI = collections.namedtuple(
    "_URI", ["uri", "scheme", "netloc", "path", "params", "query", "fragment"]
)


class cd:
    __slots__ = ["old", "new"]

    def __init__(self, new):
        self.old = None
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
    quiet=False,
    **kwargs,
):
    if not quiet:
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
    return f()


def loop(*lists, tform=itertools.product):
    """
    Use

        fns = []
        @loop(xs)
        def _(x):
            fns.append(lambda: x)

    instead of

        fns = []
        for x in xs:
            fns.append(lambda: x)

    to address the late-binding issue.

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
    return _URI(
        uri=uri,
        scheme=scheme,
        netloc=netloc,
        path=path,
        params=params,
        query=query,
        fragment=fragment,
    )


def serialize(x):
    """
    Supported data types:

    * None
    * Integer
    * Float
    * String
    * List
    * Dictionary
    * Tuple
    * Set

    == Examples

    >>> serialize(1)
    'i1_'
    >>> serialize(-10)
    'i-10_'
    >>> serialize(1.0)
    'fi20_0x1.0000000000000p+0'
    >>> serialize(dict(a=[1, 2.0, [3.0, {4: [-5.0, -0.0], -9e301: "直列化"}], None]))
    'di1_si1_ali4_i1_fi20_0x1.0000000000000p+1li2_fi20_0x1.8000000000000p+1di2_fi24_-0x1.0cc7a8fa052b1p+1003si3_直列化i4_li2_fi21_-0x1.4000000000000p+2fi9_-0x0.0p+0n'
    >>> serialize((1, 2))
    'tli2_i1_i2_'
    >>> serialize(set([1, 2]))
    'Sli2_i1_i2_'
    """

    fp = io.StringIO()

    def _save(x):
        if x is None:
            fp.write("n")
        elif isinstance(x, float):
            fp.write("f")
            h = x.hex()
            _save_int(len(h))
            fp.write(h)
        elif isinstance(x, int):
            _save_int(x)
        elif isinstance(x, str):
            fp.write("s")
            _save_int(len(x))
            fp.write(x)
        elif isinstance(x, list):
            fp.write("l")
            _save_int(len(x))
            for v in x:
                _save(v)
        elif isinstance(x, set):
            fp.write("S")
            _save(sorted(x))
        elif isinstance(x, tuple):
            fp.write("t")
            _save(list(x))
        elif isinstance(x, dict):
            fp.write("d")
            _save_int(len(x))
            for k in sorted(x.keys()):
                _save(k)
                _save(x[k])
        elif isinstance(x, argparse.Namespace):
            fp.write("N")
            _save(vars(x))
        else:
            raise ValueError(f"Unsupported argument {x} of type {type(x)} for `_save`")

    def _save_int(x):
        fp.write("i")
        fp.write(str(x))
        fp.write("_")

    _save(x)
    return fp.getvalue()


def dictify(x):
    if isinstance(x, argparse.Namespace):
        return dictify(vars(x))
    elif isinstance(x, dict):
        return {k: dictify(v) for k, v in x.items()}
    elif isinstance(x, list):
        return [dictify(v) for v in x]
    elif isinstance(x, tuple):
        return tuple(dictify(v) for v in x)
    else:
        return x


def hash_dir_of(x):
    h = _hash_of(x)
    return jp(h[:2], h[2:])


def _hash_of(x):
    return hashlib.sha1(serialize(x).encode()).hexdigest()
