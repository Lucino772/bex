from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from pathlib import Path


class BexError(Exception):
    def __init__(self, msg: str) -> None:
        super().__init__()
        self.msg = msg


class BexPyVenvError(BexError): ...


class BexUvError(BexError): ...


class Config(TypedDict):
    directory: Path
    filename: Path

    uv_version: str | None
    requires_python: str
    requirements: str
    entrypoint: str
