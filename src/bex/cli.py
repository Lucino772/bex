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
from importlib.metadata import version as get_version
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

from bex.config import load_configuration
from bex.shared import BexError, BexPyVenvError, BexUvError, Config
from bex.utils import wait_process
from bex.uv import download_uv

_ENTRYPOINT_PATTERN = re.compile(
    r"(?P<module>[\w.]+)\s*"
    r"(:\s*(?P<attr>[\w.]+)\s*)?"
    r"((?P<extras>\[.*\])\s*)?$"
)


def main():
    app = typer.Typer(
        add_completion=False,
        name="bex",
        context_settings={
            "allow_extra_args": True,
            "ignore_unknown_options": True,
        },
    )
    app.command()(_cli)
    app()


def _cli(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version")] = False,
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
    bootstrap_only: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "-b",
            "--bootstrap-only",
        ),
    ] = False,
    passthrough: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="[COMMAND] [ARGS]...",
            help="Any command and arguments are forwarded to bex",
        ),
    ] = None,
):
    if version:
        typer.echo(f"Python: {platform.python_version()} ({platform.system()})")
        typer.echo(f"Bex: {get_version('bex')}")
        ctx.exit(0)

    console = Console()
    token, cancel = with_cancel(default_token())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    handler = RichHandler(console=console, show_path=False, omit_repeated_times=False)
    root_logger.addHandler(handler)

    signal.signal(signal.SIGTERM, lambda _, __: cancel())
    signal.signal(signal.SIGINT, lambda _, __: cancel())

    exec_result = flow(
        load_configuration(
            directory, file, bootstrap_only=bootstrap_only, extra_args=passthrough
        ),
        result.map_(lambda val: (val,)),
        result.zipped(partial(_bootstrap, token, console, root_logger)),
        result.inspect(
            lambda _: console.print(
                "Environment bootstrapped successfully", style="green"
            )
        ),
        result.and_then(lambda val: _execute(*val)),
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
    cancel_token: CancellationToken,
    console: Console,
    logger: logging.Logger,
    config: Config,
) -> Result[Path, Exception]:
    with console.status("Bootstrapping environment"):
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
                    partial(download_uv, cancel_token, version=config["uv_version"])
                )
            ),
            result.inspect(lambda _: console.print("[+] Downloaded UV")),
            result.and_then(
                as_result(
                    lambda uv_bin: _create_isolated_environment(
                        console,
                        logger,
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


def _execute(config: Config, python_bin: Path) -> Result[int, Exception]:
    if config["bootstrap_only"] is True:
        return Ok(0)

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
                *config["extra_args"],
            ]
        ),
    ):
        case Ok((env, args)):
            if sys.platform == "win32":
                return result_of(
                    subprocess.call,
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
    logger: logging.Logger,
    cancel_token: CancellationToken,
    root_dir: Path,
    uv_bin: Path,
    python_specifier: str,
    requirements: str,
):
    venv_dir = root_dir / ".venv"
    requirements_in = root_dir / "requirements.in"
    requirements_txt = root_dir / "requirements.txt"
    python_bin = (
        venv_dir
        / ("Scripts" if platform.system() == "Windows" else "bin")
        / ("python.exe" if platform.system() == "Windows" else "python")
    )

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
        callback=logger.info,
    )
    if create_venv_rc != 0:
        msg = "Failed to create python virtual environment"
        raise BexPyVenvError(msg)

    console.print("[+] Created virtual environment")

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
        callback=logger.info,
    )
    if lock_pip_requirements_rc != 0:
        msg = "Failed to compile pip requirements"
        raise BexPyVenvError(msg)

    console.print("[+] Locked dependencies")

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
        callback=logger.info,
    )
    if sync_pip_requirements_rc != 0:
        msg = "Failed to sync pip requirements"
        raise BexPyVenvError(msg)

    console.print("[+] Synced dependencies")
    return python_bin
