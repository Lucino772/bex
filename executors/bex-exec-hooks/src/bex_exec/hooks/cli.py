from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import shellingham
import typer
from rich.console import Console
from rich.traceback import Traceback
from ruamel.yaml import YAML
from stdlibx.cancel import CancellationTokenCancelledError, default_token, with_cancel
from stdlibx.compose import flow
from stdlibx.result import Error, Ok, as_result
from stdlibx.result import fn as result

from bex_exec.hooks.executor import execute
from bex_exec.hooks.spec import Context, Environment


class _FormatCommandError(Exception):
    def __init__(self, key: str, value: Context):
        super().__init__()
        self.key = key
        self.value = value


app = typer.Typer(name="bex")


def main():
    app(prog_name="bex")


@app.callback()
def callback(
    ctx: typer.Context,
    file: Annotated[
        Path | None,
        typer.Option(
            "-f",
            "--file",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            envvar="BEX_FILE",
        ),
    ] = None,
    directory: Annotated[
        Path | None,
        typer.Option(
            "-C",
            "--directory",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            envvar="BEX_DIRECTORY",
        ),
    ] = None,
):
    ctx.ensure_object(dict)
    _directory = Path(os.getcwd()) if directory is None else directory
    ctx.obj["directory"] = _directory

    if file is None:
        _candidates = {"bex.yaml", "bex.yml"}
        file = next(
            (
                entry
                for candidate in _candidates
                if (entry := _directory / candidate).exists()
            ),
            None,
        )
    if file is None:
        typer.echo(f"Could not find bex file in '{_directory}'")
        ctx.exit(1)

    ctx.obj["file"] = file

    ctx.obj["env"] = Environment.model_validate(
        YAML(typ="safe").load(Path(file).read_bytes()), from_attributes=False
    )


@app.command(context_settings={"allow_interspersed_args": False})
def run(ctx: typer.Context, command: list[str]):
    console = Console()

    token, cancel = with_cancel(default_token())
    bex_ctx = Context(
        token,
        working_dir=os.fspath(ctx.obj["directory"]),
        metadata={},
        environ=dict(os.environ),
    )
    signal.signal(signal.SIGTERM, lambda _, __: cancel())
    signal.signal(signal.SIGINT, lambda _, __: cancel())

    with console.status("Executing environment"):
        exec_result = execute(console, bex_ctx, ctx.obj["env"])

    def _format_command(value: Context, cmd: list[str]):
        try:
            return [item.format_map(value.metadata) for item in cmd]
        except KeyError as err:
            raise _FormatCommandError(err.args[0], value) from None

    match flow(
        exec_result,
        result.map_(lambda val: (val,)),
        result.zipped(as_result(lambda value: _format_command(value, command))),
    ):
        case Ok((value, cmd)):
            console.print("Executed environment successfully", style="green")
            console.print(f"  Running command: {cmd}")
            ctx.exit(
                subprocess.call(
                    cmd,
                    shell=False,
                    env=value.environ,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
            )
        case Error(CancellationTokenCancelledError()):
            console.print("Process was cancelled", style="red")
            ctx.exit(3)
        case Error(_FormatCommandError() as err):
            console.print(
                f"Failed to prepare command, '{err.key}' is not in metadata",
                style="red",
            )
            console.print(
                err.value.model_dump_json(indent=4, include={"metadata"}), style="red"
            )
            ctx.exit(4)
        case Error(err):
            console.print("Failed to execute environment", style="red")
            console.print(
                Traceback(Traceback.extract(type(err), err, err.__traceback__)),
                style="dim",
            )
            ctx.exit(2)


@app.command()
def shell(ctx: typer.Context):
    console = Console()

    token, cancel = with_cancel(default_token())
    bex_ctx = Context(
        token,
        working_dir=os.fspath(ctx.obj["directory"]),
        metadata={},
        environ=dict(os.environ),
    )
    signal.signal(signal.SIGTERM, lambda _, __: cancel())
    signal.signal(signal.SIGINT, lambda _, __: cancel())

    with console.status("Executing environment"):
        exec_result = execute(console, bex_ctx, ctx.obj["env"])

    match exec_result:
        case Ok(value):
            console.print("Executed environment successfully", style="green")
            shell, path = shellingham.detect_shell()
            args = []
            if shell in ("powershell", "pwsh"):
                args += ["-NoLogo"]

            ctx.exit(
                subprocess.call(
                    [path, *args],
                    shell=False,
                    env=value.environ,
                )
            )
        case Error(CancellationTokenCancelledError()):
            console.print("Process was cancelled", style="red")
            ctx.exit(3)
        case Error(err):
            console.print("Failed to execute environment", style="red")
            console.print(
                Traceback(Traceback.extract(type(err), err, err.__traceback__)),
                style="dim",
            )
            ctx.exit(2)


@app.command()
def export(ctx: typer.Context):
    console = Console()

    token, cancel = with_cancel(default_token())
    bex_ctx = Context(
        token,
        working_dir=os.fspath(ctx.obj["directory"]),
        metadata={},
        environ=dict(os.environ),
    )
    signal.signal(signal.SIGTERM, lambda _, __: cancel())
    signal.signal(signal.SIGINT, lambda _, __: cancel())

    with console.status("Executing environment"):
        exec_result = execute(console, bex_ctx, ctx.obj["env"])

    match exec_result:
        case Ok(value):
            console.print("Executed environment successfully", style="green")
            console.print(value.model_dump_json(indent=4))
        case Error(CancellationTokenCancelledError()):
            console.print("Process was cancelled", style="red")
            ctx.exit(3)
        case Error(err):
            console.print("Failed to execute environment", style="red")
            console.print(
                Traceback(Traceback.extract(type(err), err, err.__traceback__)),
                style="dim",
            )
            ctx.exit(2)
