import collections
import threading


class TVal:
    __slots__ = ("_lock", "_val")

    def __init__(self, val, lock=threading.Lock):
        self._lock = lock()
        self._val = val

    def val(self):
        with self._lock:
            return self._val


class TDict(TVal):

    def __init__(self, d=None):
        if d is None:
            d = dict()
        super().__init__(d)

    def __setitem__(self, k, v):
        with self._lock:
            self._val[k] = v

    def __getitem__(self, k):
        with self._lock:
            return self._val[k]

    def __contains__(self, k):
        with self._lock:
            return k in self._val


class TDefaultDict(TVal):

    def __init__(self, default_factory):
        super().__init__(collections.defaultdict(default_factory))

    def __setitem__(self, k, v):
        with self._lock:
            self._val[k] = v

    def __getitem__(self, k):
        with self._lock:
            return self._val[k]

    def __contains__(self, k):
        with self._lock:
            return k in self._val


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

    def add(self, x):
        with self._lock:
            self._val.add(x)

    def remove(self, x):
        with self._lock:
            self._val.remove(x)

    def pop(self):
        with self._lock:
            return self._val.pop()


class TStack(TVal):
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


class TInt(TVal):
    def __init__(self, val):
        super().__init__(val)

    def inc(self):
        with self._lock:
            self._val += 1

    def dec(self):
        with self._lock:
            self._val -= 1


class TBool(TVal):
    def __init__(self, val):
        super().__init__(val)

    def set_self_or(self, x):
        with self._lock:
            self._val = self._val or x
