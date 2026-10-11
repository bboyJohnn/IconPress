"""Launcher, so `python IconPress.py` keeps working. The app lives in the iconpress package."""

import sys
from pathlib import Path

REQUIREMENTS = ("PySide6", "shiboken6", "PIL")


def _fail(problem: str, fix: str) -> None:
    sys.exit(f"IconPress can't start: {problem}\n\n{fix}\n")


if sys.version_info < (3, 10):  # noqa: UP036 - this file may be run by any Python
    _fail(f"it needs Python 3.10 or newer, this is {sys.version.split()[0]}.",
          "Install a newer Python from https://www.python.org/downloads/")

try:
    from iconpress.app import main
except ModuleNotFoundError as exc:
    if (exc.name or "").split(".")[0] not in REQUIREMENTS:
        raise
    # This interpreter's own pip, so the packages land in the Python that runs the app
    requirements = Path(__file__).with_name("requirements.txt")
    _fail(f"the {exc.name} module is not installed.",
          "Install the dependencies with:\n"
          f'    "{sys.executable}" -m pip install -r "{requirements}"')

if __name__ == "__main__":
    sys.exit(main())
