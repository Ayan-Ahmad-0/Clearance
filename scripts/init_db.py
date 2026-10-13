"""Initialize database schema, access rules, and chunk embeddings.

Idempotent: runs migrations and loads data only if the database is uninitialized,
making it safe to run in Render start commands or CI/CD pipelines.

Usage:
    python scripts/init_db.py
    python scripts/init_db.py --force
    python scripts/init_db.py --org data/org_nist/org.json --docs data/org_nist/documents.jsonl
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ORG = REPO_ROOT / "data" / "org_nist" / "org.json"
DEFAULT_DOCS = REPO_ROOT / "data" / "org_nist" / "documents.jsonl"
DEFAULT_SCHEMA = REPO_ROOT / "db" / "schema.sql"


def wait_for_db(dsn: str, max_retries: int = 15, delay: float = 2.0) -> psycopg.Connection:
    """Connect to postgres with retries for container/cloud cold-starts."""
    for attempt in range(1, max_retries + 1):
        try:
            conn = psycopg.connect(dsn, autocommit=True)
            print(f"[init_db] Connected to database on attempt {attempt}.")
            return conn
        except (psycopg.Error, OSError) as exc:
            if attempt == max_retries:
                raise
            print(f"[init_db] Waiting for database ({exc}). Retry {attempt}/{max_retries} in {delay}s...")
            time.sleep(delay)
    raise RuntimeError("Could not connect to database.")


def is_schema_ready(conn: psycopg.Connection) -> bool:
    """Check if core tables exist."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('chunks', 'principals', 'nodes', 'membership', 'acl')
        """)
        count = cur.fetchone()[0]
        return count == 5


def apply_schema(conn: psycopg.Connection, schema_path: Path) -> None:
    """Apply db/schema.sql excluding psql backslash directives."""
    print(f"[init_db] Applying schema from {schema_path}...")
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;")

    raw_sql = schema_path.read_text(encoding="utf-8")
    clean_lines = [
        line for line in raw_sql.splitlines()
        if not line.strip().startswith("\\") and not line.strip().startswith("SET transaction_timeout")
    ]
    clean_sql = "\n".join(clean_lines)

    with conn.transaction():
        conn.execute(clean_sql)
    conn.execute("SET search_path = public, pg_catalog;")
    print("[init_db] Schema and pgvector extension applied successfully.")


def load_organisation(conn: psycopg.Connection, org_path: Path) -> None:
    """Load org graph into principals, nodes, membership, and acl."""
    from clearance.access.loader import replace_snapshot

    print(f"[init_db] Loading organisation snapshot from {org_path}...")
    graph = json.loads(org_path.read_text(encoding="utf-8"))
    with conn.transaction():
        report = replace_snapshot(conn, graph)
    print(f"[init_db] Organisation loaded: {report.users} users, "
          f"{report.groups} groups, {report.documents} documents, {report.acl} ACLs.")


def load_chunks(conn: psycopg.Connection, docs_path: Path) -> None:
    """Chunk, scan, embed, and insert documents into chunks table with memory-safe fast-path."""
    from clearance.ingest.embedder import to_pgvector

    embedded_path = docs_path.with_name("chunks_embedded.jsonl")
    if embedded_path.exists():
        print(f"[init_db] Found pre-computed embeddings at {embedded_path}!")
        print("[init_db] Inserting pre-computed chunks into PostgreSQL...")
        started = time.perf_counter()
        with conn.transaction():
            conn.execute("TRUNCATE chunks RESTART IDENTITY;")
            with conn.cursor() as cur:
                batch = []
                count = 0
                with embedded_path.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        item = json.loads(line)
                        batch.append((item["doc_id"], item["ord"], item["text"], to_pgvector(item["vec"])))
                        count += 1
                        if len(batch) >= 500:
                            cur.executemany(
                                "INSERT INTO chunks (doc_id, ord, text, embedding) VALUES (%s, %s, %s, %s::vector);",
                                batch,
                            )
                            batch = []
                    if batch:
                        cur.executemany(
                            "INSERT INTO chunks (doc_id, ord, text, embedding) VALUES (%s, %s, %s, %s::vector);",
                            batch,
                        )
            conn.execute("ANALYZE chunks;")
        duration = round(time.perf_counter() - started, 1)
        print(f"[init_db] Loaded {count} pre-computed chunks in {duration}s (RAM usage < 25MB)!")
        return

    import gc

    import torch

    from clearance.ingest.chunker import chunk
    from clearance.ingest.embedder import embed_passages
    from clearance.ingest.scanner import scan

    print(f"[init_db] Reading documents from {docs_path}...")
    rows, quarantined = [], []
    with docs_path.open(encoding="utf-8") as fh:
        for line in fh:
            doc = json.loads(line)
            for i, c in enumerate(chunk(doc["text"])):
                result = scan(c)
                if result["flagged"]:
                    quarantined.append({"doc_id": doc["id"], "ord": i, **result})
                else:
                    rows.append((doc["id"], i, c))

    total = len(rows)
    print(f"[init_db] Embedding and loading {total} chunks in memory-safe batches of 64...")
    started = time.perf_counter()

    with conn.transaction():
        conn.execute("TRUNCATE chunks RESTART IDENTITY;")

    batch_size = 64
    with torch.inference_mode():
        for start in range(0, total, batch_size):
            batch = rows[start : start + batch_size]
            batch_texts = [r[2] for r in batch]
            vectors = embed_passages(batch_texts, batch_size=batch_size)

            with conn.transaction(), conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO chunks (doc_id, ord, text, embedding) VALUES (%s, %s, %s, %s::vector);",
                    [(d, o, t, to_pgvector(v)) for (d, o, t), v in zip(batch, vectors, strict=True)],
                )
            del vectors, batch_texts
            if (start // batch_size) % 8 == 0:
                gc.collect()
                pct = round((start + len(batch)) / total * 100)
                print(f"[init_db] Progress: {start + len(batch)}/{total} chunks ({pct}%)...")

    gc.collect()
    with conn.transaction():
        conn.execute("ANALYZE chunks;")

    duration = round(time.perf_counter() - started, 1)

    quarantine_path = docs_path.with_name("quarantine.jsonl")
    try:
        quarantine_path.write_text(
            "".join(json.dumps(q) + "\n" for q in quarantined),
            encoding="utf-8"
        )
    except OSError as exc:
        print(f"[init_db] Warning: Could not write quarantine.jsonl: {exc}")

    print(f"[init_db] Loaded {total} chunks embedded in {duration}s ({len(quarantined)} quarantined).")


def main():
    parser = argparse.ArgumentParser(description="Initialize database schema and data.")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    parser.add_argument("--org", default=str(DEFAULT_ORG))
    parser.add_argument("--docs", default=str(DEFAULT_DOCS))
    parser.add_argument("--force", action="store_true", help="Force re-creation and re-indexing")
    args = parser.parse_args()

    if not args.dsn:
        print("[init_db] ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        return 1

    schema_path = Path(args.schema)
    org_path = Path(args.org)
    docs_path = Path(args.docs)

    for p in (schema_path, org_path, docs_path):
        if not p.exists():
            print(f"[init_db] ERROR: Required file not found: {p}", file=sys.stderr)
            return 1

    conn = wait_for_db(args.dsn)
    try:
        schema_ready = is_schema_ready(conn)
        if not schema_ready or args.force:
            apply_schema(conn, schema_path)

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.principals;")
            principal_count = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM public.chunks;")
            chunk_count = cur.fetchone()[0]

        if principal_count == 0 or args.force:
            load_organisation(conn, org_path)
        else:
            print(f"[init_db] Principals already loaded ({principal_count} records). Skipping.")

        if chunk_count == 0 or args.force:
            load_chunks(conn, docs_path)
        else:
            print(f"[init_db] Chunks already loaded ({chunk_count} chunks). Skipping.")

        print("[init_db] Database is fully ready!")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
