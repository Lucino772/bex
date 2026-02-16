from __future__ import annotations

import glob
import re
from functools import partial
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from stdlibx.compose import flow
from stdlibx.option import fn as option
from stdlibx.option import optional_of
from stdlibx.result import Error, Ok, Result, as_result, result_of
from stdlibx.result import fn as result

from bex.shared import BexError, Config

_INLINE_METADATA_REGEX = (
    r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$"
)


def load_configuration(
    directory: Path | None,
    filename: Path | None,
    /,
    *,
    bootstrap_only: bool,
    extra_args: list[str] | None,
) -> Result[Config, Exception]:
    _directory = flow(
        optional_of(lambda: directory),
        option.map_or_else(lambda: result_of(Path.cwd), lambda val: Ok(val)),
    )

    _file = flow(
        optional_of(lambda: filename),
        option.map_or_else(
            lambda: flow(
                _directory,
                result.and_then(
                    as_result(
                        lambda directory: (
                            directory / next(glob.iglob("bex.*", root_dir=directory))
                        )
                    )
                ),
                result.map_err(
                    lambda err: (
                        BexError("Could not find bex file")
                        if isinstance(err, StopIteration)
                        else err
                    )
                ),
            ),
            lambda val: Ok(val),
        ),
    )

    return flow(
        result.collect(_directory, _file),
        result.and_then(
            lambda val: _parse_config(
                val[0],
                val[1],
                bootstrap_only=bootstrap_only,
                extra_args=extra_args,
                labels=["bootstrap", "bex"],
            )
        ),
    )


def _parse_config(
    directory: Path,
    file: Path,
    /,
    *,
    bootstrap_only: bool,
    extra_args: list[str] | None,
    labels: list[str],
) -> Result[Config, Exception]:
    return flow(
        result_of(lambda: re.finditer(_INLINE_METADATA_REGEX, file.read_text())),
        result.and_then(
            as_result(
                lambda iterator: (m for m in iterator if m.group("type") in labels)
            )
        ),
        result.and_then(as_result(list)),
        result.and_then(
            lambda matches: (
                Error[list[re.Match[str]], Exception](
                    ValueError("Multiple blocks found")
                )
                if len(matches) > 1
                else Ok[list[re.Match[str]], Exception](matches)
            )
        ),
        result.and_then(
            lambda matches: (
                _parse_inline_metadata(matches[0].group("content"))
                if len(matches) == 1
                else Ok({})
            )
        ),
        result.and_then(as_result(_validate_config)),
        result.map_(
            lambda config: Config(
                {
                    "directory": directory,
                    "filename": file,
                    "bootstrap_only": bootstrap_only,
                    "extra_args": extra_args or [],
                    "uv_version": config.get("uv"),
                    "requires_python": config["requires-python"],
                    "requirements": config.get("requirements", ""),
                    "entrypoint": config["entrypoint"],
                }
            )
        ),
    )


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    if not {"requires-python", "entrypoint"}.issubset(config.keys()):
        _missing = {"requires-python", "entrypoint"}.difference(config.keys())
        msg = "Invalid configuration, missing: {}".format(", ".join(_missing))
        raise BexError(msg)

    return config


def _parse_inline_metadata(content: str) -> Result[dict[str, Any], Exception]:
    return flow(
        Ok(content),
        result.and_then(
            as_result(partial(str.splitlines, keepends=True)),
        ),
        result.and_then(
            as_result(
                lambda lines: (
                    line[2:] if line.startswith("# ") else line[1:] for line in lines
                )
            )
        ),
        result.and_then(as_result("".join)),
        result.and_then(as_result(YAML(typ="safe").load)),
    )
