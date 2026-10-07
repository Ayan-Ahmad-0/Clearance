"""Load data/org/org.json into the local database.

Run:  python scripts/load_org.py --org data/org/org.json
Uses DATABASE_URL. Replaces whatever organisation is already loaded.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

from clearance.access.loader import SnapshotError, replace_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", default="data/org/org.json")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.dsn:
        print("DATABASE_URL is not set (or pass --dsn)", file=sys.stderr)
        return 2

    graph = json.loads(Path(args.org).read_text())
    try:
        with psycopg.connect(args.dsn) as conn:
            report = replace_snapshot(conn, graph)
    except SnapshotError as exc:
        print(f"invalid snapshot: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())