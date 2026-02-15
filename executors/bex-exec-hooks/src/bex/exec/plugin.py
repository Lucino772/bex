from __future__ import annotations

import functools
import importlib
import re
from typing import TYPE_CHECKING, Any

from rich.console import Console
from stdlibx.compose import flow, pipe
from stdlibx.option import fn as option
from stdlibx.option import optional_of
from stdlibx.result import Error, Ok, Result, as_result, result_of
from stdlibx.result import fn as result

from bex.exec.spec import BexPluginError, HookFunc, PluginInfo

if TYPE_CHECKING:
    from rich.console import Console

_ENTRYPOINT_PATTERN = re.compile(
    r"(?P<module>[\w.]+)\s*"
    r"(:\s*(?P<attr>[\w.]+)\s*)?"
    r"((?P<extras>\[.*\])\s*)?$"
)


def load_plugins(console: Console, plugins: list[str]):
    return result.collect_all(
        flow(
            _plugin_from_entrypoint(_plugin),
            result.inspect(lambda _: console.print(f"[+] Imported plugin '{_plugin}'")),
            result.inspect_err(
                lambda _: console.print(f"[+] Failed to import plugin '{_plugin}'")
            ),
        )
        for _plugin in plugins
    ).apply(result.map_(list))


def _plugin_from_entrypoint(entrypoint: str):
    def _map_errors(error: Exception):
        match error:
            case ImportError():
                return BexPluginError(f"Failed to import plugin from '{entrypoint}'")
            case AttributeError():
                return BexPluginError(f"Failed to import plugin from '{entrypoint}'")
            case _:
                return error

    return flow(
        result_of(_ENTRYPOINT_PATTERN.match, entrypoint),
        result.and_then(
            lambda match: (
                Ok[re.Match[str], Exception](match)
                if match is not None
                else Error[re.Match[str], Exception](
                    BexPluginError(f"Invalid plugin entrypoint format '{entrypoint}'")
                )
            )
        ),
        result.and_then(
            as_result(
                lambda match_: functools.reduce(
                    getattr,
                    filter(None, (match_.group("attr") or "").split(".")),
                    importlib.import_module(match_.group("module")),
                )
            )
        ),
        result.and_then(
            lambda module: result.collect(
                Ok(getattr(module, "__plugin_name__", module.__name__)),
                _load_hooks(module),
            )
        ),
        result.map_(lambda value: PluginInfo(value[0], value[1])),
        result.map_err(_map_errors),
    )


def _load_hooks(module: Any) -> Result[dict[str, HookFunc], Exception]:
    return flow(
        optional_of(getattr, module, "get_hooks", None),
        option.map_or_else(
            lambda: Ok({}),
            pipe(
                as_result(lambda callback: callback()),
                result.map_(lambda value: value if isinstance(value, dict) else {}),
            ),
        ),
    )
