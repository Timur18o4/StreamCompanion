import sys
from pathlib import Path


def runtime_root():
    """Keep user data beside the executable, never in a frozen extraction area."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
