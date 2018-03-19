class Err(Exception):
    def __init__(self, msg=""):
        self.msg = msg


class NotFound(Err):
    def __init__(self, msg=""):
        self.msg = msg
