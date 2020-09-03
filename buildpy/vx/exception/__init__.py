class Err(Exception):
    pass


class NotFound(Err):
    pass


class MultipleSetError(Err):
    pass


class NotSetError(Err):
    pass
