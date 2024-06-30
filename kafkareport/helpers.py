"""Random tidbits used in various components."""

import sys


def slurp(filename):
    """Suck a file into a str."""
    try:
        with open(filename, encoding="utf-8") as x:
            f = x.read()
    except FileNotFoundError:
        print(f"{filename} not found.")
        sys.exit(1)
    return f
