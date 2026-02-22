from __future__ import annotations

import contextlib
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from stdlibx import option, result
from stdlibx.cancel import CancellationToken, is_token_cancelled
from stdlibx.compose import flow
from stdlibx.result.types import Error, Ok

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def wait_process(
    args: str | Sequence[str],
    cancel_token: CancellationToken,
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

    cancel_token.register(_terminate_process)

    while True:
        _result = flow(
            option.maybe(lambda: process.stdout),
            option.map_or_else(
                lambda: result.ok("\n") if process.poll() is None else result.ok(""),
                result.safe(
                    lambda stdout: (
                        stdout.readline() or "\n" if process.poll() is None else ""
                    )
                ),
            ),
            result.and_then(
                lambda val: (
                    result.ok(val)
                    if len(val) > 0
                    else result.error(_ProcessEndedError())
                )
            ),
            result.map_(lambda val: val.strip("\n")),
        )

        match _result:
            case Ok(line) if callback is not None:
                callback(line)
            case Error(_ProcessEndedError()):
                cancel_token.raise_if_cancelled()
                return process.poll()  # type: ignore
            case Error():
                _terminate_process(None)
                return process.poll()  # type: ignore


def download_file(
    token: CancellationToken,
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
            while token.is_cancelled() is False:
                dest.write(next(chunk_iter))
                if callable(report_hook):
                    report_hook(response.num_bytes_downloaded, _content_len)

        _path = Path(dest.name)
        if is_token_cancelled(token) and _path.exists():
            _path.unlink()
            raise token.get_error()

        return _path
