from __future__ import annotations

from bex.hooks.files.file import archive, download


def get_hooks():
    return {
        "files/archive": archive,
        "files/download": download,
    }
