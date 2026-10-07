"""One full-scale differential run: every (user, document) pair, SQL against reference.

Run:  python scripts/full_differential.py --org data/org/org.json
Loads the organisation first (replacing whatever is loaded), so the database is
the file by construction. Writes benchmarks/results/<date>/differential.json
and exits 1 on any mismatch.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from clearance.access.loader import replace_snapshot
from reference_resolver import authorised


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", default="data/org/org.json")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.dsn:
        print("DATABASE_URL is not set (or pass --dsn)", file=sys.stderr)
        return 2

    raw = Path(args.org).read_bytes()
    graph = json.loads(raw)
    docs = [n for n in graph["node_parent"] if n.startswith("d")]
    mismatches, allowed_pairs = [], 0

    started = time.perf_counter()
    with psycopg.connect(args.dsn) as conn:
        replace_snapshot(conn, graph)
        for u in graph["users"]:
            expected = authorised(graph, u)
            rows = conn.execute("SELECT doc_id FROM authorised_documents(%s)", (u,))
            got = {r[0] for r in rows}
            allowed_pairs += len(expected)
            mismatches += [
                {"user": u, "doc": d, "reference": d in expected, "sql": d in got}
                for d in sorted(expected ^ got)
            ]
    elapsed = round(time.perf_counter() - started, 1)

    result = {
        "org_sha256": hashlib.sha256(raw).hexdigest(),
        "users": len(graph["users"]),
        "documents": len(docs),
        "pairs_compared": len(graph["users"]) * len(docs),
        "allowed_pairs": allowed_pairs,
        "mismatches": len(mismatches),
        "first_mismatches": mismatches[:20],
        "seconds": elapsed,
    }
    out_dir = Path("benchmarks/results") / datetime.now(tz=UTC).date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "differential.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != "first_mismatches"}, indent=2))
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())