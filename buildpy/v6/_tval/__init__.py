import collections
import threading


class Err(Exception):
    pass


class TVal:
    __slots__ = ("_lock", "_val")

    def __init__(self, val, lock=threading.RLock):
        self._lock = lock()
        self._val = val

    def val(self):
        with self._lock:
            return self._val


class TDict:

    def __init__(self, *args, **kwargs):
        self.data = dict(*args, **kwargs)
        self.lock = threading.RLock()

    def __len__(self):
        with self.lock:
            return self.data.__len__()

    def __getitem__(self, k):
        with self.lock:
            return self.data.__getitem__(k)

    def __setitem__(self, k, v):
        with self.lock:
            return self.data.__setitem__(k, v)

    def __delitem__(self, k):
        with self.lock:
            return self.data.__delitem__(k)

    def __contains__(self, k):
        with self.lock:
            return self.data.__contains__(k)

    def __repr__(self):
        with self.lock:
            return self.__class__.__name__ + "(" + repr(self.data) + ")"

    def get(self, k, default=None):
        with self.lock:
            return self.data.get(k, default)

    def items(self):
        with self.lock:
            return self.data.items()

    def keys(self):
        with self.lock:
            return self.data.keys()

    def values(self):
        with self.lock:
            return self.data.values()

    def setdefault(self, k, default=None):
        with self.lock:
            return self.data.setdefault(k, default)


class TDefaultDict(TDict):

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.data = collections.defaultdict(dict)


class Cache:

    def __init__(self):
        self._data_lock_dict = dict()
        self._data_lock_dict_lock = threading.Lock()
        self._data = TDict()

    def get(self, k, make_val):
        with self._data_lock_dict_lock:
            # This block finishes instantly
            try:
                k_lock = self._data_lock_dict[k]
            except KeyError:
                k_lock = threading.Lock()
                self._data_lock_dict[k] = k_lock

        with k_lock:
            try:
                return self._data[k]
            except KeyError: # This block may require time to finish.
                val = make_val()
                self._data[k] = val
                return val


class TSet(TVal):
    def __init__(self):
        super().__init__(set())

    def __len__(self):
        with self._lock:
            return len(self._val)

    def __iter__(self):
        return iter(self.val())

    def add(self, x):
        with self._lock:
            self._val.add(x)

    def remove(self, x):
        with self._lock:
            self._val.remove(x)

    def pop(self):
        with self._lock:
            return self._val.pop()


class TInt(TVal):
    def __init__(self, val):
        super().__init__(val)

    def inc(self):
        with self._lock:
            self._val += 1

    def dec(self):
        with self._lock:
            self._val -= 1


class ddict:
    """
    >>> conf = ddict()
    >>> conf.z = 99
    >>> conf
    ddict({'z': 99})
    >>> conf = ddict(a=1, b=ddict(c=2, d=ddict(e=3)))
    >>> conf
    ddict({'a': 1, 'b': ddict({'c': 2, 'd': ddict({'e': 3})})})
    >>> conf.a
    1
    >>> conf.b.c
    2
    >>> conf.a = 99
    >>> conf.b.c = 88
    >>> conf
    ddict({'a': 99, 'b': ddict({'c': 88, 'd': ddict({'e': 3})})})
    >>> conf.a = 1
    >>> conf.b.c = 2
    >>> conf._update(dict(p=9, r=10))
    ddict({'a': 1, 'b': ddict({'c': 2, 'd': ddict({'e': 3})}), 'p': 9, 'r': 10})
    >>> conf._to_dict_rec()
    {'a': 1, 'b': {'c': 2, 'd': {'e': 3}}, 'p': 9, 'r': 10}
    >>> conf._of_dict_rec({'a': 1, 'b': {'c': 2, 'd': {'e': 3}}})
    ddict({'a': 1, 'b': ddict({'c': 2, 'd': ddict({'e': 3})})})
    >>> conf._to_dict()
    {'a': 1, 'b': ddict({'c': 2, 'd': ddict({'e': 3})})}
    >>> conf._of_dict({'a': 1, 'b': {'c': 2, 'd': {'e': 3}}, 'p': 9, 'r': 10})
    ddict({'a': 1, 'b': {'c': 2, 'd': {'e': 3}}, 'p': 9, 'r': 10})
    """

    def __init__(self, *args, **kwargs):
        super().__setattr__("_lock", threading.RLock())
        super().__setattr__("_data", dict())
        self._update(dict(*args, **kwargs))

    def __setattr__(self, k, v):
        with self._lock:
            self[k] = v

    def __getattr__(self, k):
        with self._lock:
            return self[k]

    def __setitem__(self, k, v):
        with self._lock:
            if k in self.__dict__:
                raise ValueError(f"Tried to overwrite {k} of {self} by {v}")
            self._data[k] = v

    def __getitem__(self, k):
        with self._lock:
            return self._data[k]

    def __contains__(self, k):
        with self._lock:
            return k in self._data

    def __repr__(self):
        return f"{self.__class__.__name__}({repr(self._data)})"

    def _values(self):
        with self._lock:
            return self._data.values()

    def _update(self, d):
        with self._lock:
            for k, v in d.items():
                setattr(self, k, v)
            return self

    def _to_dict(self):
        with self._lock:
            return self._data.copy()

    def _to_dict_rec(self):
        with self._lock:
            return {k: v._to_dict_rec() if isinstance(v, self.__class__) else v for k, v in self._data.items()}

    def _of_dict(self, d):
        with self._lock:
            self._data.clear()
            return self._update(d)

    def _of_dict_rec(self, d):
        with self._lock:
            self._data.clear()
            for k, v in d.items():
                setattr(self, k, self.__class__()._of_dict_rec(v) if isinstance(v, dict) else v)
            return self


class NonOverwritableDict(TDict):

    def __setitem__(self, k, v):
        with self.lock:
            if k in self.data:
                raise Err(f"Tried to overwrite {k} with {v} for {self}")
            self.data[k] = v
