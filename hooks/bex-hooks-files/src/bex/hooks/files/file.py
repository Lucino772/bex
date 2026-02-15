from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from bex.hooks.files.utils import EtaCalculator, download_file

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping

    from bex.hooks.files._interface import Context


def archive(ctx: Context, args: Mapping[str, Any], *, logger: logging.Logger) -> None:
    class _Args(BaseModel):
        source: str
        source_hash: str
        target: str
        format_: str = Field(validation_alias="format")
        keep_source: bool = Field(default=True)

    data = _Args.model_validate(args, from_attributes=False)
    target = Path(
        Template(data.target).substitute(
            {
                "working_dir": ctx.working_dir,
                "metadata": ctx.metadata,
                "environ": ctx.environ,
            }
        )
    )
    enforce_toplevel = args.get("enforce_toplevel", False)

    hash_algo, hash_hex = data.source_hash.split(":")
    cached_file = (
        Path(ctx.working_dir) / ".bex" / "cache" / "files" / hash_algo / hash_hex
    )
    if cached_file.exists() and cached_file.is_file():
        logger.info("Using %s", cached_file)
        filename = cached_file
    else:
        eta = EtaCalculator()
        filename = download_file(
            ctx,
            data.source,
            report_hook=lambda _bytes, total: logger.info(
                "Downloading file (%s) [%s]",
                round((_bytes / total) * 100, 2),
                eta.eta(_bytes, total),
            ),
        )

    _path = Path(filename)
    if hash_hex != hashlib.new(hash_algo, _path.read_bytes()).hexdigest():
        msg = f"Hash mismatched when downloading {data.source}"
        raise ValueError(msg)

    # TODO: Don't extract if file has not changed
    try:
        if data.format_ == "zip":
            with zipfile.ZipFile(filename) as archive:
                has_single_toplevel = True
                if not enforce_toplevel or (
                    len(
                        {
                            Path(name).parts[0]
                            for name in archive.namelist()
                            if not name.startswith("__MACOSX")
                        }
                    )
                    > 1
                ):
                    has_single_toplevel = False

                _members = archive.infolist()
                for idx, member in enumerate(_members, start=1):
                    ctx.raise_if_cancelled()

                    member_path = Path(member.filename)
                    relative_path = (
                        Path(*member_path.parts[1:])
                        if has_single_toplevel
                        else member_path
                    )
                    target_path = (Path(target) / relative_path).resolve()
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    if member.is_dir():
                        target_path.mkdir(parents=True, exist_ok=True)
                    else:
                        with (
                            archive.open(member) as source,
                            open(target_path, "wb") as target_file,
                        ):
                            target_file.write(source.read())

                    logger.info("[%s] - %s", round(idx / len(_members), 2), target_path)
    finally:
        if _path.exists() and data.keep_source is True:
            cached_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(_path, cached_file)
        elif _path.exists():
            _path.unlink()


def download(ctx: Context, args: Mapping[str, Any], *, logger: logging.Logger) -> None:
    class _Args(BaseModel):
        source: str
        source_hash: str
        target: str
        keep_source: bool = Field(default=True)

    data = _Args.model_validate(args, from_attributes=False)
    target = Path(
        Template(data.target).substitute(
            {
                "working_dir": ctx.working_dir,
                "metadata": ctx.metadata,
                "environ": ctx.environ,
            }
        )
    )

    hash_algo, hash_hex = data.source_hash.split(":")
    if (
        target.exists()
        and hashlib.new(hash_algo, target.read_bytes()).hexdigest() == hash_hex
    ):
        logger.info("File already exists %s", target)
        return

    cached_file = (
        Path(ctx.working_dir) / ".bex" / "cache" / "files" / hash_algo / hash_hex
    )
    if cached_file.exists() and cached_file.is_file():
        logger.info("Using %s", cached_file)
        filename = cached_file
    else:
        eta = EtaCalculator()
        filename = download_file(
            ctx,
            data.source,
            report_hook=lambda _bytes, total: logger.info(
                "Downloading file (%s) [%s]",
                round((_bytes / total) * 100, 2),
                eta.eta(_bytes, total),
            ),
        )

    _path = Path(filename)
    if hash_hex != hashlib.new(hash_algo, _path.read_bytes()).hexdigest():
        msg = f"Hash mismatched when downloading {data.source}"
        raise ValueError(msg)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_path, target)
    finally:
        if _path.exists() and data.keep_source is True:
            cached_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(_path, cached_file)
        elif _path.exists():
            _path.unlink()
