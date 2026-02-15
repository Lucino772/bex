from __future__ import annotations

from bex_hooks.python.setup import setup_python


def get_hooks():
    return {
        "python/setup-python": setup_python,
    }
