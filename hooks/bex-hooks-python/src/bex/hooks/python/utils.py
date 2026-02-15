from __future__ import annotations

import contextlib
import datetime as dt
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from stdlibx.compose import flow
from stdlibx.option import fn as option
from stdlibx.option import optional_of
from stdlibx.result import Error, Ok, as_result
from stdlibx.result import fn as result

from bex.hooks.python._interface import is_context_cancelled

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bex.hooks.python._interface import Context


def append_path(previous: str, *values: str) -> str:
    path_sep = ";" if platform.system() == "Windows" else ":"
    previous_path = previous.split(path_sep)
    for value in values:
        if value not in previous_path:
            previous_path.append(value)
    return path_sep.join([value for value in previous_path if len(value) > 0])


def prepend_path(previous: str, *values: str) -> str:
    path_sep = ";" if platform.system() == "Windows" else ":"
    previous_path = previous.split(path_sep)
    for value in reversed(values):
        if value not in previous_path:
            previous_path.insert(0, value)
    return path_sep.join([value for value in previous_path if len(value) > 0])


def download_file(
    ctx: Context,
    source: str,
    *,
    chunk_size: int | None = None,
    report_hook: Callable[[int, int], Any] | None = None,
) -> Path:
    with (
        tempfile.NamedTemporaryFile(delete=False) as dest,
        httpx.stream(
            "GET", source, follow_redirects=True, headers={"Accept-Encoding": ""}
        ) as response,
    ):
        _content_len = (
            int(response.headers["Content-Length"])
            if "Content-Length" in response.headers
            else -1
        )

        chunk_iter = response.iter_bytes(chunk_size)
        with contextlib.suppress(StopIteration):
            while ctx.is_cancelled() is False:
                dest.write(next(chunk_iter))
                if callable(report_hook):
                    report_hook(response.num_bytes_downloaded, _content_len)

        _path = Path(dest.name)
        if is_context_cancelled(ctx) and _path.exists():
            _path.unlink()
            raise ctx.get_error()

        return _path


def wait_process(
    ctx: Context,
    args: str | Sequence[str],
    /,
    *,
    callback: Callable[[str], Any] | None = None,
    timeout: float | None = None,
    **kwargs,
) -> int:
    class _ProcessEndedError(Exception): ...

    process = subprocess.Popen(
        args,
        shell=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **kwargs,
    )

    def _terminate_process(_: Exception | None):
        if process.poll() is not None:
            return

        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    ctx.register(_terminate_process)

    while True:
        _result = flow(
            optional_of(lambda: process.stdout),
            option.map_or_else(
                lambda: Ok("\n") if process.poll() is None else Ok(""),
                as_result(
                    lambda stdout: (
                        stdout.readline() or "\n" if process.poll() is None else ""
                    )
                ),
            ),
            result.and_then(
                lambda val: Ok(val) if len(val) > 0 else Error(_ProcessEndedError())
            ),
            result.map_(lambda val: val.strip("\n")),
        )

        match _result:
            case Ok(line) if callback is not None:
                callback(line)
            case Error(_ProcessEndedError()):
                ctx.raise_if_cancelled()
                return process.poll()  # type: ignore
            case Error():
                _terminate_process(None)
                return process.poll()  # type: ignore


class EtaCalculator:
    def __init__(self) -> None:
        self.__start_time = None

    def eta(self, value: int, total: int) -> str:
        if value == -1:
            return str(dt.timedelta())

        _now = dt.datetime.now(tz=dt.UTC)
        if self.__start_time is None:
            self.__start_time = _now

        _elapsed = (_now - self.__start_time).total_seconds()
        if _elapsed == 0:
            return str(dt.timedelta())

        return str(dt.timedelta(seconds=(total - value) / (value / _elapsed)))
