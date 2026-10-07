"""Run one query as each persona and report results and leaks.

Run:  python scripts/try_search.py "approved reserve Project" --mode prefilter
"""
import argparse
import json
import os
from pathlib import Path

import psycopg

from clearance.retrieval.search import search


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--mode", default="prefilter", choices=["prefilter", "postfilter"])
    parser.add_argument("--personas", default="data/org/personas.json")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    personas = json.loads(Path(args.personas).read_text())
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for name, info in personas.items():
            user = info["user"]
            hits = search(conn, user, args.query, mode=args.mode, top=args.top)
            rows = conn.execute("SELECT doc_id FROM authorised_documents(%s)", (user,))
            allowed = {r[0] for r in rows}
            leaks = [h["doc_id"] for h in hits if h["doc_id"] not in allowed]
            print(f"\n{name} ({user}, sees {len(allowed)} docs): {len(hits)} results, "
                  f"{len(leaks)} leaks")
            for h in hits[:3]:
                print(f"  {h['doc_id']}  {h['score']:.4f}  {h['text'][:80]}...")


if __name__ == "__main__":
    main()