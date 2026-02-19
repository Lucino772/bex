from __future__ import annotations


class BexError(Exception):
    def __init__(self, msg: str) -> None:
        super().__init__()
        self.msg = msg


class BexPyVenvError(BexError): ...


class BexUvError(BexError): ...
