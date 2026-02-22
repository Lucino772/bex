from __future__ import annotations

import contextlib
import datetime as dt
import platform
import shutil
import stat
import sys
import sysconfig
import tarfile
import zipfile
from collections import defaultdict
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx
from rich.progress import DownloadColumn, Progress
from stdlibx import option, result
from stdlibx.compose import flow

from bex.errors import BexUvError
from bex.utils import download_file

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console
    from stdlibx.cancel import CancellationToken
    from stdlibx.option.types import Option

_UV_DOWNLOAD_URL = "https://github.com/astral-sh/uv/releases/download/{version}/"
_UV_RELEASES_URL = "https://api.github.com/repos/astral-sh/uv/releases"


def download_uv(
    console: Console,
    cancel_token: CancellationToken,
    directory: Path,
    *,
    version: str | None = None,
):
    _version = flow(
        option.maybe(lambda: version),
        option.map_or_else(
            result.safe(_get_uv_latest_version), lambda ver: result.ok(option.some(ver))
        ),
        result.unwrap_or_raise(),
        option.ok_or(BexUvError(f"Invalid UV version '{version}'")),
        result.unwrap_or_raise(),
    )

    exe = ".exe" if sys.platform == "win32" else ""
    uv_bin = directory / f"uv-{_version}{exe}"
    if uv_bin.exists():
        return uv_bin

    filename, target = flow(
        _get_uv_release_info(),
        option.ok_or(BexUvError("Could not find release info for UV")),
        result.unwrap_or_raise(),
    )

    def _extract(source: Path) -> Path:
        uv_bin.parent.mkdir(exist_ok=True, parents=True)
        if filename.endswith(".zip"):
            with (
                zipfile.ZipFile(source, "r") as archive,
                archive.open(f"uv{exe}", mode="r") as fsrc,
                open(uv_bin, mode="wb") as fdst,
            ):
                shutil.copyfileobj(fsrc, fdst)
        else:
            with tarfile.open(source, "r:gz") as archive:
                fsrc = archive.extractfile(archive.getmember(f"{target}/uv{exe}"))
                if fsrc is None:
                    msg = "Failed to extract file for tar archive"
                    raise RuntimeError(msg)
                with fsrc, open(uv_bin, mode="wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)

        return uv_bin

    with Progress(
        *Progress.get_default_columns(),
        DownloadColumn(),
        console=console,
        transient=True,
    ) as pb:
        task_id = pb.add_task(f"Downloading uv {_version}")
        temp_filename = flow(
            result.try_(
                download_file,
                cancel_token,
                urljoin(_UV_DOWNLOAD_URL.format(version=_version), filename),
                report_hook=lambda completed, total: pb.update(
                    task_id, completed=completed, total=total
                ),
            ),
            result.map_err(lambda _: BexUvError(f"Failed to download uv '{_version}'")),
            result.unwrap_or_raise(),
        )

    _result = flow(
        result.try_(_extract, temp_filename),
        result.map_(lambda val: (val,)),
        result.zipped(result.safe(lambda p: p.chmod(p.stat().st_mode | stat.S_IXUSR))),
        result.map_(lambda val: val[0]),
        result.map_err(lambda _: BexUvError("Failed to extract uv from archive")),
    )
    with contextlib.suppress(Exception):
        temp_filename.unlink(missing_ok=True)

    return flow(_result, result.unwrap_or_raise())


def _get_uv_release_info() -> Option[tuple[str, str]]:
    system = platform.system().lower()
    if system not in ("windows", "linux", "darwin"):
        return option.nothing()

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
        return option.nothing()

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
        return option.some((target + ".zip", target))

    return option.some((target + ".tar.gz", target))


def _get_uv_latest_version() -> Option[str]:
    response = httpx.get(_UV_RELEASES_URL).json()
    releases = (
        (str(entry["name"]), dt.datetime.fromisoformat(entry["published_at"]))
        for entry in response
        if entry["draft"] is False and entry["prerelease"] is False
    )
    return option.maybe(
        lambda: next(
            iter(sorted(releases, key=lambda entry: entry[1], reverse=True)),
            (None, None),
        )[0]
    )
