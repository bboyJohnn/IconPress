"""Launcher, so `python IconPress.py` keeps working. The app lives in the iconpress package."""

import sys

from iconpress.app import main

if __name__ == "__main__":
    sys.exit(main())
