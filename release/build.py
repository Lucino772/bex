import argparse
import hashlib
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import NamedTuple

_ROOT_DIR = Path(__file__).parent.parent
_PYINSTALLER_SPEC = Path(__file__).parent / "bex.spec"

_TARGET_PATTERN = re.compile(
    r"^(?P<arch>[a-zA-Z0-9_]+)-(?P<vendor>[a-zA-Z0-9_]+)-(?P<os>[a-zA-Z0-9_]+)(?:-(?P<abi>[a-zA-Z0-9_]+))?$"
)
EXE = ".exe" if sys.platform == "win32" else ""


class Target(NamedTuple):
    full: str
    arch: str
    vendor: str
    os: str
    abi: str | None


logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def build():
    working_dir = Path(__file__).parent.parent.resolve()

    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("-o", "--out", default=str(working_dir / "build"))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    target = _parse_target(args.target)
    if target is None:
        logger.error("Invalid target '%s'", args.target)
        sys.exit(1)

    build_dir = Path(args.out)
    build_dir.mkdir(exist_ok=True)

    logger.info("Building bex binary")
    bex_bin, version = _build_pyinstaller_dist(build_dir, target, debug=args.debug)
    logger.info("Bex (%s) binary built '%s'", version, str(bex_bin))

    logger.info("Generating sha256 for bex binary")
    bex_bin_sha = _generate_sha256_file(bex_bin)
    logger.info("SHA256 file generated '%s'", bex_bin_sha)


def _build_pyinstaller_dist(build_dir: Path, target: Target, *, debug: bool = False):
    with tempfile.TemporaryDirectory(dir=build_dir if debug else None) as tmp_dir:
        _work_path = Path(tmp_dir) / "_build"

        # Get version from pyproject.toml
        with (_ROOT_DIR / "pyproject.toml").open("rb") as fp:
            version = str(tomllib.load(fp)["project"]["version"])
        logger.info("Bex version: %s", version)

        # Build binary using pyinstaller
        pyinstaller_bin = Path(shutil.which("pyinstaller"))  # type: ignore
        logger.info("Building binary with PyInstaller")
        ret_code = subprocess.call(
            [
                str(pyinstaller_bin),
                str(_PYINSTALLER_SPEC),
                "--noconfirm",
                "--distpath",
                tmp_dir,
                "--workpath",
                _work_path,
            ],
            cwd=str(_ROOT_DIR),
        )
        if ret_code != 0:
            msg = "Failed to build binary with pyinstaller"
            raise RuntimeError(msg)

        # Rename temp binary with version
        _temp_binary_file = Path(tmp_dir) / f"bex{EXE}"
        _target_binary_file = build_dir / (
            f"bex-{version}-{target.arch}-{target.vendor}-{target.os}-{target.abi}{EXE}"
            if target.abi is not None
            else f"bex-{version}-{target.arch}-{target.vendor}-{target.os}{EXE}"
        )
        if _target_binary_file.exists():
            _target_binary_file.unlink()
        shutil.move(_temp_binary_file, _target_binary_file)

        return _target_binary_file, version


# Utils
def _parse_target(target: str) -> Target | None:
    _match = _TARGET_PATTERN.fullmatch(target)
    if _match is None:
        return None

    return Target(
        target,
        _match.group("arch"),
        _match.group("vendor"),
        _match.group("os"),
        _match.group("abi"),
    )


def _generate_sha256_file(path: Path) -> str:
    _filename = str(path.absolute()) + ".sha256"
    with open(_filename, "w") as fp:
        fp.write(hashlib.sha256(path.read_bytes()).hexdigest())
    return _filename


if __name__ == "__main__":
    build()
