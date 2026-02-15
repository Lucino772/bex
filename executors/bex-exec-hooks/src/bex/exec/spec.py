from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field
from stdlibx.cancel import CancellationToken, with_cancel

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping


class BexExecError(Exception):
    def __init__(self, msg: str) -> None:
        super().__init__()
        self.msg = msg


class BexPluginError(BexExecError): ...


class HookFunc(Protocol):
    def __call__(
        self, ctx: Context, args: Mapping[str, Any], *, logger: logging.Logger
    ) -> None: ...


class Environment(BaseModel):
    class Config(BaseModel):
        model_config = ConfigDict(extra="allow")
        plugins: list[str] = Field(default_factory=list)

    class Hook(BaseModel):
        model_config = ConfigDict(extra="allow")
        __pydantic_extra__: dict[str, Any] = Field(init=False)  # type: ignore

        id: str
        if_: str | None = Field(default=None, alias="if")

    config: Config
    hooks: list[Hook] = Field(default_factory=list)


@dataclass(frozen=True)
class PluginInfo:
    name: str
    hooks: Mapping[str, HookFunc]


class Context(BaseModel):
    working_dir: str
    metadata: dict[str, Any]
    environ: dict[str, str]

    def __init__(self, token: CancellationToken, **kwargs) -> None:
        super().__init__(**kwargs)
        self.__token, self.__cancel = with_cancel(token)

    def register(self, fn: Callable[[Exception], None]) -> None:
        return self.__token.register(fn)

    def is_cancelled(self) -> bool:
        return self.__token.is_cancelled()

    def get_error(self) -> Exception | None:
        return self.__token.get_error()

    def raise_if_cancelled(self):
        self.__token.raise_if_cancelled()

    def wait(self, timeout: float | None) -> Exception | None:
        return self.__token.wait(timeout)

    def cancel(self) -> None:
        return self.__cancel()
