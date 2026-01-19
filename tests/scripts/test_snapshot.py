#!/usr/bin/env python3
"""Test script for daily book snapshot capture.

Usage:
    poetry run python tests/scripts/test_snapshot.py

    # Or via Docker:
    docker exec ibkr-bot python3 /app/tests/scripts/test_snapshot.py
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from ibkr_spy_puts.scheduler import create_snapshot_function


def main():
    print("=" * 60)
    print("Testing Daily Book Snapshot Capture")
    print("=" * 60)

    # Create and run snapshot function
    snapshot_func = create_snapshot_function()
    snapshot_func()

    print("\nSnapshot capture test complete!")
    print("Check the database: SELECT * FROM book_snapshots;")


if __name__ == "__main__":
    main()
