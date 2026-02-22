from __future__ import annotations

import glob
import re
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

from ruamel.yaml import YAML
from stdlibx import option, result
from stdlibx.compose import flow

from bex.errors import BexError

if TYPE_CHECKING:
    from stdlibx.result.types import Result


_INLINE_METADATA_REGEX = (
    r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$"
)


class Config(TypedDict):
    directory: Path
    filename: Path
    verbosity: int

    uv_version: str | None
    requires_python: str
    requirements: str
    entrypoint: str


def load_configuration(
    directory: Path | None, filename: Path | None, verbosity: int
) -> Result[Config, Exception]:
    _directory = flow(
        option.maybe(lambda: directory),
        option.map_or_else(lambda: result.try_(Path.cwd), lambda val: result.ok(val)),
    )

    _file = flow(
        option.maybe(lambda: filename),
        option.map_or_else(
            lambda: flow(
                _directory,
                result.and_then(
                    result.safe(
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
            lambda val: result.ok(val),
        ),
    )

    return flow(
        result.collect(_directory, _file),
        result.and_then(
            lambda val: _parse_config(
                val[0], val[1], verbosity, labels=["bootstrap", "bex"]
            )
        ),
    )


def _parse_config(
    directory: Path, file: Path, verbosity: int, /, *, labels: list[str]
) -> Result[Config, Exception]:
    return flow(
        result.try_(lambda: re.finditer(_INLINE_METADATA_REGEX, file.read_text())),
        result.and_then(
            result.safe(
                lambda iterator: (m for m in iterator if m.group("type") in labels)
            )
        ),
        result.and_then(result.safe(list)),
        result.and_then(
            lambda matches: cast(
                "Result[list[re.Match[str]], Exception]",
                (
                    result.ok(matches)
                    if len(matches) == 1
                    else result.error(ValueError("Multiple blocks found"))
                ),
            )
        ),
        result.and_then(
            lambda matches: (
                _parse_inline_metadata(matches[0].group("content"))
                if len(matches) == 1
                else result.ok({})
            )
        ),
        result.and_then(result.safe(_validate_config)),
        result.map_(
            lambda config: Config(
                {
                    "directory": directory,
                    "filename": file,
                    "verbosity": verbosity,
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
        result.ok(content),
        result.and_then(
            result.safe(partial(str.splitlines, keepends=True)),
        ),
        result.and_then(
            result.safe(
                lambda lines: (
                    line[2:] if line.startswith("# ") else line[1:] for line in lines
                )
            )
        ),
        result.and_then(result.safe("".join)),
        result.and_then(result.safe(YAML(typ="safe").load)),
    )
