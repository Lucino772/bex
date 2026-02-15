from __future__ import annotations

import logging
import platform
import time
from typing import TYPE_CHECKING

import cel
from rich.logging import RichHandler
from stdlibx.cancel import is_token_cancelled
from stdlibx.compose import flow
from stdlibx.option import Nothing, Some, optional_of
from stdlibx.result import Error, Ok, Result, as_result, result_of
from stdlibx.result import fn as result

from bex.exec.plugin import load_plugins

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

    from rich.console import Console

    from bex.exec.spec import Context, Environment, HookFunc


def execute(
    console: Console, ctx: Context, env: Environment
) -> Result[Context, Exception]:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    handler = RichHandler(console=console, show_path=False, omit_repeated_times=False)
    root_logger.addHandler(handler)

    match load_plugins(console, env.config.plugins):
        case Ok(value):
            plugins = list(value)
        case Error(_) as err:
            return Error(err.error)

    hooks: MutableMapping[str, HookFunc] = {}
    for plugin in plugins:
        hooks.update(plugin.hooks)
        console.print(f"[+] Loaded hooks from plugin '{plugin.name}'")

    ctx.metadata["platform"] = platform.system().lower()
    ctx.metadata["arch"] = platform.machine().lower()

    cel_ctx = cel.Context()
    hook_logger = logging.getLogger("bex.hooks")

    return flow(
        result.collect_all(
            _execute_hook(console, hooks, hook, ctx, cel_ctx, hook_logger)
            for hook in env.hooks
        ),
        result.map_(lambda _: ctx),
    )


def _execute_hook(
    console: Console,
    hooks: Mapping[str, HookFunc],
    hook: Environment.Hook,
    ctx: Context,
    cel_ctx: cel.Context,
    logger: logging.Logger,
) -> Result[None, Exception]:
    match flow(
        result_of(lambda: cel_ctx.update({**ctx.metadata, "env": ctx.environ})),
        result.and_then(
            as_result(
                lambda _: (
                    hook.if_ is not None
                    and bool(cel.evaluate(hook.if_, cel_ctx)) is False
                )
            )
        ),
    ):
        case Ok(skip_hook) if skip_hook is True:
            console.print(f"[-] Hook skipped: '{hook.id}'")
            return Ok(None)
        case Error(_) as err:
            return Error(err.error)

    if is_token_cancelled(ctx):
        return Error(ctx.get_error())

    match optional_of(hooks.get, hook.id):
        case Some(func):
            hook_func = func
        case Nothing():
            return Error(Exception(f"Hook '{hook.id}' does not exists"))

    console.print(f"[+] Running hook '{hook.id}'")
    start_time = time.perf_counter()
    try:
        hook_func(ctx, hook.__pydantic_extra__, logger=logger)
    except Exception as e:
        duration = time.perf_counter() - start_time
        console.print(
            f"[!] Hook failed to run: '{hook.id}' ({duration:.2f}s)", style="red"
        )
        return Error(e)
    else:
        duration = time.perf_counter() - start_time
        console.print(f"[+] Hook ran successfully: '{hook.id}' ({duration:.2f}s)")

    return Ok(None)
