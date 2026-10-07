"""Revocation takes effect on the next query: two sessions, writer commits, reader searches.

Run:  python scripts/revocation_demo.py
Writes benchmarks/results/<date>/revocation.json. Restores the organisation afterwards.
"""
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from clearance.retrieval.search import search


def authorised(conn, user):
    return {r[0] for r in conn.execute("SELECT doc_id FROM authorised_documents(%s)", (user,))}


def hit_ids(conn, user, text):
    return [h["doc_id"] for h in search(conn, user, text[:150], mode="prefilter", top=10)]


def main():
    dsn = os.environ["DATABASE_URL"]
    personas = json.loads(Path("data/org/personas.json").read_text())
    docs = {}
    for line in Path("data/org/documents.jsonl").read_text().splitlines():
        d = json.loads(line)
        docs[d["id"]] = d
    user = personas["mid_visibility"]["user"]

    writer = psycopg.connect(dsn)
    reader = psycopg.connect(dsn, autocommit=True)  # a separate session
    report = {"user": user, "checks": {}}
    ok = True
    edge = None
    deny_doc = None
    try:
        before = authorised(reader, user)
        hit_ids(reader, user, "warm up the model")  # first call loads the embedder

        # A. deny row on a document the user can read right now
        deny_doc = sorted(before)[0]
        seen_before = deny_doc in hit_ids(reader, user, docs[deny_doc]["text"])
        writer.execute("INSERT INTO acl (node_id, subject_id, effect) VALUES (%s, %s, 'deny')",
                       (deny_doc, user))
        writer.commit()
        t0 = time.perf_counter()
        seen_after = deny_doc in hit_ids(reader, user, docs[deny_doc]["text"])
        ms = round((time.perf_counter() - t0) * 1000, 1)
        passed = seen_before and not seen_after and deny_doc not in authorised(reader, user)
        ok &= passed
        report["checks"]["deny_row"] = {"doc": deny_doc, "visible_before": seen_before,
                                        "visible_after": seen_after,
                                        "commit_to_answer_ms": ms, "passed": passed}
        writer.execute("DELETE FROM acl WHERE node_id = %s AND subject_id = %s AND effect = 'deny'",
                       (deny_doc, user))
        writer.commit()
        deny_doc = None

        # B. remove a membership edge, closure refreshed incrementally in the same transaction
        parents = [r[0] for r in writer.execute(
            "SELECT parent FROM membership WHERE child = %s AND parent <> 'G000' ORDER BY parent",
            (user,))]
        writer.rollback()
        for parent in parents:
            writer.execute("DELETE FROM membership WHERE child = %s AND parent = %s", (user, parent))
            writer.execute("SELECT refresh_membership_closure(users_below(%s::text[]))", ([user],))
            writer.commit()
            edge = (user, parent)
            lost = sorted(before - authorised(reader, user))
            if lost:
                break
            writer.execute("INSERT INTO membership (child, parent) VALUES (%s, %s)", edge)
            writer.execute("SELECT refresh_membership_closure(users_below(%s::text[]))", ([user],))
            writer.commit()
            edge = None
        if edge and lost:
            t0 = time.perf_counter()
            still_visible = [d for d in lost[:5] if d in hit_ids(reader, user, docs[d]["text"])]
            ms = round((time.perf_counter() - t0) * 1000 / min(len(lost), 5), 1)
            passed = not still_visible
            ok &= passed
            report["checks"]["membership_edge_removed"] = {
                "removed_edge": list(edge), "documents_lost": len(lost),
                "still_visible_in_search": still_visible,
                "avg_query_ms_after_commit": ms, "passed": passed}
        else:
            report["checks"]["membership_edge_removed"] = {"skipped": "no edge removal changed access"}
    finally:
        if deny_doc:
            writer.rollback()
            writer.execute("DELETE FROM acl WHERE node_id = %s AND subject_id = %s AND effect = 'deny'",
                           (deny_doc, user))
        if edge:
            writer.rollback()
            writer.execute("INSERT INTO membership (child, parent) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                           edge)
            writer.execute("SELECT refresh_membership_closure(users_below(%s::text[]))", ([user],))
        writer.commit()

    restored = authorised(reader, user) == before
    ok &= restored
    report["organisation_restored"] = restored
    report["all_passed"] = ok
    out_dir = Path("benchmarks/results") / datetime.now(tz=UTC).date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "revocation.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())