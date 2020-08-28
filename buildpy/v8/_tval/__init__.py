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
            except KeyError:  # This block may require time to finish.
                val = make_val()
                self._data[k] = val
                return val


class TInt(TVal):
    def __init__(self, val):
        super().__init__(val)

    def inc(self):
        with self._lock:
            self._val += 1

    def dec(self):
        with self._lock:
            self._val -= 1


class NonOverwritableDict(TDict):
    def __setitem__(self, k, v):
        with self.lock:
            if k in self.data:
                raise Err(f"Tried to overwrite {k} with {v} for {self}")
            self.data[k] = v


class TListOf:
    def __init__(self):
        self.data = dict()
        self.lock = threading.RLock()

    def __len__(self):
        with self.lock:
            return len(self.data)

    def __getitem__(self, k):
        with self.lock:
            return self.data[k]

    def __setitem__(self, k, v):
        with self.lock:
            self.data[k] = v

    def __contains__(self, k):
        with self.lock:
            return k in self.data

    def __repr__(self):
        with self.lock:
            return self.__class__.__name__ + "(" + repr(self.data) + ")"

    def append(self, k, v):
        with self.lock:
            if k in self.data:
                self.data[k].append(v)
            else:
                self.data[k] = [v]

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
