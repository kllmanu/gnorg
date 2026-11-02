#!/usr/bin/env python3
"""Start Gnorg (Gnorg is not org)."""

import sys

from vim import GnorgApplication


def main():
    return GnorgApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
