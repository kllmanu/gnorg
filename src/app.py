#!/usr/bin/env python3
"""Start the Organizer application."""

import sys

from vim import OrganizerApplication


def main():
    return OrganizerApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
