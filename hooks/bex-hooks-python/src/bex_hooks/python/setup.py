from __future__ import annotations

import datetime as dt
import glob
import itertools
import os
import platform
import stat
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
from collections import defaultdict
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, Field

from bex_hooks.python.utils import (
    EtaCalculator,
    append_path,
    download_file,
    prepend_path,
    wait_process,
)

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterable, Mapping

    from bex_hooks.python._interface import Context

_UV_RELEASES_URL = "https://api.github.com/repos/astral-sh/uv/releases"
_UV_DOWNLOAD_URL = "https://github.com/astral-sh/uv/releases/download/{version}/"


class _Args(BaseModel):
    version: str
    uv_version: str | None = Field(default=None, alias="uv")
    requirements: str = Field(default="")
    requirements_file: list[str] = Field(default_factory=list)
    activate_env: bool = Field(default=False)
    set_python_path: bool = Field(default=False)
    inexact: bool = Field(default=False)


def setup_python(
    ctx: Context, args: Mapping[str, Any], *, logger: logging.Logger
) -> None:
    data = _Args.model_validate(args, from_attributes=False)

    bex_dir = Path(ctx.working_dir) / ".bex"
    root_dir = Path(ctx.working_dir) / "python"
    root_dir.mkdir(exist_ok=True)

    uv = _download_uv(ctx, logger, bex_dir / "cache" / "uv", version=data.uv_version)
    if uv is None:
        msg = "Failed to download uv"
        raise RuntimeError(msg)

    req_files = list(
        itertools.chain(
            *[
                glob.iglob(
                    Template(file).substitute(
                        {
                            "working_dir": ctx.working_dir,
                            "metadata": ctx.metadata,
                            "environ": ctx.environ,
                        }
                    ),
                    recursive=True,
                )
                for file in data.requirements_file
            ]
        )
    )
    for file in req_files:
        logger.info("Discovered requirement file: %s", file)

    python_bin = _create_isolated_environment(
        ctx,
        root_dir,
        uv,
        data.version,
        data.requirements,
        req_files,
        data.inexact,
        logger,
    )
    if python_bin is None:
        msg = "Failed to create python virtual environment"
        raise RuntimeError(msg)

    # Configure PYTHONPATH environment variables
    _python_path = _get_python_path(python_bin)

    ctx.metadata["python_bin"] = str(python_bin)

    venv_dir = root_dir / ".venv"
    if data.activate_env is True:
        ctx.environ["VIRTUAL_ENV"] = str(venv_dir)
        ctx.environ["VENV_DIR"] = str(venv_dir)
        ctx.environ["PATH"] = prepend_path(
            ctx.environ["PATH"],
            str(venv_dir / ("Scripts" if platform.system() == "Windows" else "bin")),
        )
        ctx.environ["VIRTUAL_ENV_PROMPT"] = Path(ctx.working_dir).name
        if "PYTHONHOME" in ctx.environ:
            del ctx.environ["PYTHONHOME"]

    if data.set_python_path is True and _python_path:
        ctx.environ["PYTHONPATH"] = append_path(
            ctx.environ.get("PYTHONPATH", ""), str(Path(_python_path))
        )


def _create_isolated_environment(
    ctx: Context,
    root_dir: Path,
    uv_bin: Path,
    python_specifier: str,
    requirements: str,
    req_files: Iterable[str],
    inexact: bool,  # noqa: FBT001
    logger: logging.Logger,
):
    venv_dir = root_dir / ".venv"
    requirements_in = root_dir / "requirements.in"
    requirements_txt = root_dir / "requirements.txt"
    python_bin = (
        venv_dir
        / ("Scripts" if platform.system() == "Windows" else "bin")
        / ("python.exe" if platform.system() == "Windows" else "python")
    )

    create_venc_rc = wait_process(
        ctx,
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
        callback=lambda line: logger.info(line),
    )
    if create_venc_rc != 0:
        return None

    full_requirements = requirements
    for file in req_files:
        full_requirements += "\n" + Path(file).read_text()

    requirements_in.write_bytes(
        Template(full_requirements)
        .substitute(
            {
                "working_dir": ctx.working_dir,
                "metadata": ctx.metadata,
                "environ": ctx.environ,
            }
        )
        .encode("utf-8")
    )

    lock_pip_requirements_rc = wait_process(
        ctx,
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
        callback=lambda line: logger.info(line),
    )
    if lock_pip_requirements_rc != 0:
        return None

    sync_pip_requirements_rc = wait_process(
        ctx,
        [
            str(uv_bin),
            "pip",
            "install",
            "--python",
            str(python_bin),
        ]
        + (["--exact"] if inexact is False else [])
        + [
            "-r",
            str(requirements_txt),
        ],
        callback=lambda line: logger.info(line),
    )
    if sync_pip_requirements_rc != 0:
        return None

    return python_bin


def _download_uv(
    ctx: Context, logger: logging.Logger, directory: Path, *, version: str | None = None
):
    if version is None:
        version = _get_uv_latest_version()
    if version is None:
        return None

    exe = ".exe" if sys.platform == "win32" else ""
    uv_bin = directory / f"uv-{version}{exe}"
    if uv_bin.exists():
        return uv_bin

    filename, target = _get_uv_release_info()
    if filename is None or target is None:
        return None

    eta = EtaCalculator()
    temp_filename = download_file(
        ctx,
        urljoin(_UV_DOWNLOAD_URL.format(version=version), filename),
        report_hook=lambda _bytes, total: logger.info(
            "Downloading uv (%s) [%s]", round(_bytes / total, 2), eta.eta(_bytes, total)
        ),
    )
    try:

        def _filter(info: tarfile.TarInfo, _):
            return info.replace(name=os.path.basename(info.name))

        if filename.endswith(".zip"):
            with zipfile.ZipFile(temp_filename, "r") as archive:
                archive.extract(f"uv{exe}", directory)
        else:
            with tarfile.open(temp_filename, "r:gz") as archive:
                archive.extract(f"{target}/uv{exe}", directory, filter=_filter)

        (directory / f"uv{exe}").rename(uv_bin)
        uv_bin.chmod(uv_bin.stat().st_mode | stat.S_IXUSR)
        return uv_bin
    finally:
        _path = Path(temp_filename)
        if _path.exists():
            _path.unlink()


def _get_python_path(python_bin: Path):
    try:
        _output = subprocess.check_output(
            [
                str(python_bin),
                "-c",
                "import sysconfig;print(sysconfig.get_paths()['purelib'])",
            ],
            shell=False,
            text=True,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError:
        return None
    else:
        if _output[-1:] == "\n":
            _output = _output[:-1]
        return _output


def _get_uv_latest_version() -> str | None:
    response = httpx.get(_UV_RELEASES_URL).json()
    releases = (
        (entry["name"], dt.datetime.fromisoformat(entry["published_at"]))
        for entry in response
        if entry["draft"] is False and entry["prerelease"] is False
    )
    return next(
        iter(sorted(releases, key=lambda entry: entry[1], reverse=True)),
        (None, None),
    )[0]


def _get_uv_release_info():
    system = platform.system().lower()
    if system not in ("windows", "linux", "darwin"):
        return None, None

    arch = defaultdict(
        lambda: None,
        {
            "AMD64": "x86_64",
            "x86_64": "x86_64",
            "arm64": "aarch64",
            "aarch64": "aarch64",
        },
    )[platform.machine()]
    if arch is None:
        return None, None

    vendor = defaultdict(lambda: "unknown", {"windows": "pc", "darwin": "apple"})[
        system
    ]

    abi = None
    if system == "windows":
        cc = sysconfig.get_config_var("CC")
        abi = "msvc" if cc is None or cc == "cl.exe" else "gnu"
    elif system == "linux":
        libc, _ = platform.libc_ver()
        abi = "gnu" if libc in ("glibc", "libc") else "musl"

    if abi is not None:
        target = f"uv-{arch}-{vendor}-{system}-{abi}"
    else:
        target = f"uv-{arch}-{vendor}-{system}"

    if system == "windows":
        return target + ".zip", target

    return target + ".tar.gz", target
