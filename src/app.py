#!/usr/bin/env python3
"""Start GNORG (Gnorg is not org)."""

import sys

from vim import OrganizerApplication


def main():
    return OrganizerApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
