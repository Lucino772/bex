from __future__ import annotations

import contextlib
import datetime as dt
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from bex_hooks.files._interface import is_context_cancelled

if TYPE_CHECKING:
    from collections.abc import Callable

    from bex_hooks.files._interface import Context


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
