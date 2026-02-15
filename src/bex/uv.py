from __future__ import annotations

import contextlib
import datetime as dt
import json
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
from urllib.request import Request, urlopen

from stdlibx.compose import flow
from stdlibx.option import Nothing, Option, Some, optional_of
from stdlibx.option import fn as option
from stdlibx.result import Ok, as_result, result_of
from stdlibx.result import fn as result

from bex.shared import BexUvError
from bex.utils import download_file

if TYPE_CHECKING:
    from pathlib import Path

    from stdlibx.cancel import CancellationToken

_UV_DOWNLOAD_URL = "https://github.com/astral-sh/uv/releases/download/{version}/"
_UV_RELEASES_URL = "https://api.github.com/repos/astral-sh/uv/releases"


def download_uv(
    cancel_token: CancellationToken, directory: Path, *, version: str | None = None
):
    _version = flow(
        optional_of(lambda: version),
        option.map_or_else(
            as_result(_get_uv_latest_version), lambda ver: Ok(Some(ver))
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

    temp_filename = flow(
        result_of(
            download_file,
            cancel_token,
            urljoin(_UV_DOWNLOAD_URL.format(version=_version), filename),
        ),
        result.map_err(lambda _: BexUvError(f"Failed to download uv '{_version}'")),
        result.unwrap_or_raise(),
    )
    _result = flow(
        result_of(_extract, temp_filename),
        result.map_(lambda val: (val,)),
        result.zipped(as_result(lambda p: p.chmod(p.stat().st_mode | stat.S_IXUSR))),
        result.map_(lambda val: val[0]),
        result.map_err(lambda _: BexUvError("Failed to extract uv from archive")),
    )
    with contextlib.suppress(Exception):
        temp_filename.unlink(missing_ok=True)

    return _result.apply(result.unwrap_or_raise())


def _get_uv_release_info() -> Option[tuple[str, str]]:
    system = platform.system().lower()
    if system not in ("windows", "linux", "darwin"):
        return Nothing()

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
        return Nothing()

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
        return Some((target + ".zip", target))

    return Some((target + ".tar.gz", target))


def _get_uv_latest_version() -> Option[str]:
    with urlopen(Request(_UV_RELEASES_URL, method="GET")) as res:  #  noqa: S310
        response = json.load(res)
    releases = (
        (str(entry["name"]), dt.datetime.fromisoformat(entry["published_at"]))
        for entry in response
        if entry["draft"] is False and entry["prerelease"] is False
    )
    return optional_of(
        lambda: next(
            iter(sorted(releases, key=lambda entry: entry[1], reverse=True)),
            (None, None),
        )[0]
    )
