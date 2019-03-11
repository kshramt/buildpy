class Err(Exception):
    def __init__(self, msg=""):
        super().__init__(msg)


class NotFound(Err):
    def __init__(self, msg=""):
        super().__init__(msg)
