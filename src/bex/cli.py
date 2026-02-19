from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import signal
import subprocess
import sys
from functools import partial
from pathlib import Path  # noqa: TC003
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import Traceback
from stdlibx.cancel import (
    CancellationToken,
    CancellationTokenCancelledError,
    default_token,
    with_cancel,
)
from stdlibx.compose import flow
from stdlibx.result import Error, Ok, Result, as_result, result_of
from stdlibx.result import fn as result

try:
    from bex._version import __version__
except Exception:
    __version__ = "unknown"

from bex.config import Config, load_configuration
from bex.errors import BexError, BexPyVenvError, BexUvError
from bex.utils import wait_process
from bex.uv import download_uv

_ENTRYPOINT_PATTERN = re.compile(
    r"(?P<module>[\w.]+)\s*"
    r"(:\s*(?P<attr>[\w.]+)\s*)?"
    r"((?P<extras>\[.*\])\s*)?$"
)


app = typer.Typer(
    add_completion=False,
    name="bex",
    context_settings={"allow_interspersed_args": False},
)


def main():
    app(prog_name="bex")


def _show_version(ctx: typer.Context, value: bool):
    if value is False:
        return

    typer.echo(f"Python: {platform.python_version()} ({platform.system()})")
    typer.echo(f"Bex: {__version__}")
    ctx.exit(0)


@app.callback(invoke_without_command=True)
def callback(
    ctx: typer.Context,
    _: Annotated[
        bool, typer.Option("--version", callback=_show_version, is_eager=True)
    ] = False,
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
        ),
    ] = None,
):
    if ctx.invoked_subcommand is None:
        ctx.fail("Missing command.")

    ctx.ensure_object(dict)
    ctx.obj["file"] = file
    ctx.obj["directory"] = directory


@app.command()
def init(ctx: typer.Context):
    console = Console()
    token, cancel = with_cancel(default_token())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    handler = RichHandler(console=console, show_path=False, omit_repeated_times=False)
    root_logger.addHandler(handler)

    signal.signal(signal.SIGTERM, lambda _, __: cancel())
    signal.signal(signal.SIGINT, lambda _, __: cancel())

    bootstrap_result = flow(
        load_configuration(ctx.obj["directory"], ctx.obj["file"]),
        result.map_(lambda val: (val,)),
        result.zipped(partial(_bootstrap, token, console)),
    )

    match bootstrap_result:
        case Ok(_):
            console.print("Environment bootstrapped successfully", style="green")
            ctx.exit(0)
        case Error(CancellationTokenCancelledError()):
            console.print("Process was cancelled", style="red")
            ctx.exit(3)
        case Error(BexPyVenvError() as err):
            console.print(
                f"Error while creating virtual environment: {err.msg}", style="red"
            )
            ctx.exit(1)
        case Error(BexUvError() as err):
            console.print(f"Error while downloading uv: {err.msg}", style="red")
            ctx.exit(1)
        case Error(BexError() as err):
            console.print(err.msg, style="red")
            ctx.exit(1)
        case Error(err):
            console.print("Failed to bootstrap environment", style="red")
            console.print(
                Traceback(Traceback.extract(type(err), err, err.__traceback__)),
                style="dim",
            )
            ctx.exit(1)


@app.command(context_settings={"help_option_names": [], "ignore_unknown_options": True})
def exec(
    ctx: typer.Context,
    args: Annotated[
        list[str] | None,
        typer.Argument(
            help="Any arguments are forwarded to the entrypoint",
        ),
    ] = None,
):
    console = Console()
    token, cancel = with_cancel(default_token())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    handler = RichHandler(console=console, show_path=False, omit_repeated_times=False)
    root_logger.addHandler(handler)

    signal.signal(signal.SIGTERM, lambda _, __: cancel())
    signal.signal(signal.SIGINT, lambda _, __: cancel())

    exec_result = flow(
        load_configuration(ctx.obj["directory"], ctx.obj["file"]),
        result.map_(lambda val: (val,)),
        result.zipped(partial(_bootstrap, token, console)),
        result.inspect(
            lambda _: console.print(
                "Environment bootstrapped successfully", style="green"
            )
        ),
        result.and_then(lambda val: _execute(*val, args or [])),
    )

    match exec_result:
        case Ok(retcode):
            ctx.exit(retcode)
        case Error(CancellationTokenCancelledError()):
            console.print("Process was cancelled", style="red")
            ctx.exit(3)
        case Error(BexPyVenvError() as err):
            console.print(
                f"Error while creating virtual environment: {err.msg}", style="red"
            )
            ctx.exit(1)
        case Error(BexUvError() as err):
            console.print(f"Error while downloading uv: {err.msg}", style="red")
            ctx.exit(1)
        case Error(BexError() as err):
            console.print(err.msg, style="red")
            ctx.exit(1)
        case Error(err):
            console.print("Failed to bootstrap environment", style="red")
            console.print(
                Traceback(Traceback.extract(type(err), err, err.__traceback__)),
                style="dim",
            )
            ctx.exit(1)


def _bootstrap(
    cancel_token: CancellationToken, console: Console, config: Config
) -> Result[Path, Exception]:
    logger = logging.getLogger("bex.bootstrap")

    # Get working directory
    match flow(
        result_of(lambda: config["directory"] / ".bex"),
        result.and_then(as_result(lambda path: path.mkdir(exist_ok=True) or path)),
    ):
        case Ok(directory):
            working_dir = directory
        case Error(_) as err:
            return Error(err.error)

    # Get current env hash
    # TODO: Show warning if we failed to compute env hash
    env_hash = flow(
        result_of(
            lambda: hashlib.sha1(config["filename"].read_bytes()).hexdigest()  # noqa: S324
        ),
        result.unwrap_or(""),
    )

    # Get env hash file
    match result_of(lambda: working_dir / ".envhash"):
        case Ok(file):
            env_hash_file = file
        case Error(_) as err:
            return Error(err.error)

    # Check if env has changed
    match result_of(
        lambda: env_hash_file.exists() and env_hash == env_hash_file.read_text()
    ):
        case Ok(hash_matched) if hash_matched is True:
            return result_of(
                lambda: (
                    working_dir
                    / ".venv"
                    / ("Scripts" if sys.platform == "win32" else "bin")
                    / ("python.exe" if sys.platform == "win32" else "python")
                )
            )
        case Error(_) as err:
            return Error(err.error)

    # Create / Sync python virtual environment
    match flow(
        result_of(lambda: working_dir / "cache" / "uv"),
        result.and_then(
            as_result(
                partial(
                    download_uv, console, cancel_token, version=config["uv_version"]
                )
            )
        ),
        result.inspect(lambda _: logger.info("Downloaded UV")),
        result.and_then(
            as_result(
                lambda uv_bin: _create_isolated_environment(
                    console,
                    cancel_token,
                    working_dir,
                    uv_bin,
                    config["requires_python"],
                    config["requirements"],
                )
            )
        ),
    ):
        case Ok(python_bin):
            # NOTE: If this fail, we don't want the entire program to crash
            #       instead, we could just show a warning message
            _ = result_of(env_hash_file.write_text, env_hash)
            return Ok(python_bin)
        case Error(_) as err:
            return Error(err.error)


def _execute(
    config: Config, python_bin: Path, extra_args: list[str]
) -> Result[int, Exception]:
    # NOTE: Convert entrypoint to python CLI options
    #       either "-m <module_name>" or to "-c <script>" with a script
    #       that imports module and execute function.
    match flow(
        result_of(_ENTRYPOINT_PATTERN.match, config["entrypoint"]),
        result.and_then(
            lambda match: (
                Ok[re.Match[str], Exception](match)
                if match is not None
                else Error[re.Match[str], Exception](
                    BexError(
                        f"Invalid plugin entrypoint format '{config['entrypoint']}'"
                    )
                )
            )
        ),
        result.and_then(
            as_result(
                lambda match_: (
                    ["-m", str(match_.group("module"))]
                    if len(
                        attrs := list(
                            filter(None, (match_.group("attr") or "").split("."))
                        )
                    )
                    == 0
                    else [
                        "-c",
                        "import {} as _entrypoint;_entrypoint.{}()".format(
                            match_.group("module"), ".".join(attrs)
                        ),
                    ]
                )
            )
        ),
    ):
        case Ok(opts_):
            opts = opts_
        case Error(_) as err:
            return Error(BexError("Failed to convert entrypoint to python CLI options"))

    match result.collect(
        result_of(
            lambda: {
                **os.environ,
                "BEX_FILE": str(config["filename"]),
                "BEX_DIRECTORY": str(config["directory"]),
            }
        ),
        result_of(
            lambda: [
                str(python_bin),
                *opts,
                *extra_args,
            ]
        ),
    ):
        case Ok((env, args)):
            if sys.platform == "win32":
                return result_of(
                    subprocess.call,
                    args,
                    env=env,
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    shell=False,
                )

            # NOTE: Must be careful what process is executed here
            return Ok(os.execve(python_bin, args, env))  # noqa: S606
        case Error(_) as err:
            return Error(err.error)


def _create_isolated_environment(
    console: Console,
    cancel_token: CancellationToken,
    root_dir: Path,
    uv_bin: Path,
    python_specifier: str,
    requirements: str,
):
    logger = logging.getLogger("bex.bootstrap")

    venv_dir = root_dir / ".venv"
    requirements_in = root_dir / "requirements.in"
    requirements_txt = root_dir / "requirements.txt"
    python_bin = (
        venv_dir
        / ("Scripts" if platform.system() == "Windows" else "bin")
        / ("python.exe" if platform.system() == "Windows" else "python")
    )

    with console.status("[not dim]Bootstrapping environment[/not dim]"):
        create_venv_rc = wait_process(
            [
                str(uv_bin),
                "venv",
                "--allow-existing",
                "--no-project",
                "--seed",
                "--python",
                python_specifier,
                "--python-preference",
                "only-managed",
                str(venv_dir),
            ],
            cancel_token,
            callback=logger.debug,
        )
        if create_venv_rc != 0:
            msg = "Failed to create python virtual environment"
            raise BexPyVenvError(msg)

        logger.info("Updated virtual environment")

        requirements_in.write_bytes(requirements.encode("utf-8"))
        lock_pip_requirements_rc = wait_process(
            [
                str(uv_bin),
                "pip",
                "compile",
                "--python",
                str(python_bin),
                "--emit-index-url",
                str(requirements_in),
                "-o",
                str(requirements_txt),
            ],
            cancel_token,
            callback=logger.debug,
        )
        if lock_pip_requirements_rc != 0:
            msg = "Failed to compile pip requirements"
            raise BexPyVenvError(msg)

        logger.info("Locked dependencies")

        sync_pip_requirements_rc = wait_process(
            [
                str(uv_bin),
                "pip",
                "sync",
                "--allow-empty-requirements",
                "--python",
                str(python_bin),
                str(requirements_txt),
            ],
            cancel_token,
            callback=logger.debug,
        )
        if sync_pip_requirements_rc != 0:
            msg = "Failed to sync pip requirements"
            raise BexPyVenvError(msg)

        logger.info("Synced dependencies")
    return python_bin
