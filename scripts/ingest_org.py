"""Chunk, embed and load data/org/documents.jsonl into the chunks table.

Run:  python scripts/ingest_org.py
Replaces all existing chunks. Uses DATABASE_URL.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import psycopg

from clearance.ingest.chunker import chunk
from clearance.ingest.embedder import embed_passages, to_pgvector
from clearance.ingest.scanner import scan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", default="data/org/documents.jsonl")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.dsn:
        print("DATABASE_URL is not set (or pass --dsn)", file=sys.stderr)
        return 2

    rows, quarantined = [], []
    with Path(args.docs).open() as fh:
        for line in fh:
            doc = json.loads(line)
            for i, c in enumerate(chunk(doc["text"])):
                result = scan(c)
                if result["flagged"]:
                    quarantined.append({"doc_id": doc["id"], "ord": i, **result})
                else:
                    rows.append((doc["id"], i, c))

    started = time.perf_counter()
    vectors = embed_passages([r[2] for r in rows])
    embed_seconds = round(time.perf_counter() - started, 1)

    with psycopg.connect(args.dsn) as conn:
        conn.execute("TRUNCATE chunks RESTART IDENTITY")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (doc_id, ord, text, embedding) VALUES (%s, %s, %s, %s::vector)",
                [(d, o, t, to_pgvector(v)) for (d, o, t), v in zip(rows, vectors, strict=True)],
            )
        conn.commit()
        conn.execute("ANALYZE chunks")

    Path(args.docs).with_name("quarantine.jsonl").write_text(
        "".join(json.dumps(q) + "\n" for q in quarantined)
    )
    print(f"{len(rows)} chunks embedded in {embed_seconds}s and loaded, "
          f"{len(quarantined)} quarantined")
    return 0


if __name__ == "__main__":
    sys.exit(main())