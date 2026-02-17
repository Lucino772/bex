from __future__ import annotations

from bex_hooks.files.file import archive, download, inline


def get_hooks():
    return {
        "files/archive": archive,
        "files/download": download,
        "files/inline": inline,
    }
