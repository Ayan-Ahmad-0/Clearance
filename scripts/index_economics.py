"""Index economics: three arms over a scaled chunk table, with EXPLAIN ANALYZE.

Run:  python scripts/index_economics.py --chunks 60000
      python scripts/index_economics.py --chunks 400000      (several minutes)
Builds chunks_bench from the real embeddings (cloned with small noise, real doc_ids),
so authorised sets apply. Vectors are synthetic: valid for latency, size and recall
against exact search, not for answer quality.
Writes benchmarks/results/<date>/index_economics_<n>.json and explain_<n>.txt
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import psycopg

from clearance.ingest.embedder import embed_query, to_pgvector

TOP = 10
EF_SEARCH = "100"
SQL = (
    "SELECT id, doc_id FROM chunks_bench WHERE doc_id = ANY(%(docs)s) "
    "ORDER BY embedding <=> %(qv)s::vector LIMIT %(top)s"
)
EXACT_SQL = (
    "SELECT id, doc_id FROM chunks_bench WHERE doc_id = ANY(%(docs)s) "
    "ORDER BY (embedding <=> %(qv)s::vector) + 0 LIMIT %(top)s"
)
ARMS = {
    "exact_scan": {},
    "hnsw_prefilter": {"enable_seqscan": "off", "hnsw.iterative_scan": "relaxed_order"},
    "hnsw_postfilter": {"enable_seqscan": "off", "hnsw.iterative_scan": "off"},
}


def build_table(conn, n, seed=42, batch=5000, sigma=0.01):
    rng = np.random.default_rng(seed)
    base = conn.execute("SELECT doc_id, embedding::text FROM chunks ORDER BY id").fetchall()
    docs = [r[0] for r in base]
    vecs = np.array([json.loads(r[1]) for r in base], dtype=np.float32)
    conn.execute("DROP TABLE IF EXISTS chunks_bench")
    conn.execute(
        "CREATE TABLE chunks_bench (id bigserial PRIMARY KEY, doc_id text NOT NULL, "
        "embedding vector(384) NOT NULL)"
    )
    done = 0
    with conn.cursor() as cur:
        while done < n:
            m = min(batch, n - done)
            idx = rng.integers(0, len(docs), m)
            v = vecs[idx] + rng.normal(0, sigma, (m, vecs.shape[1])).astype(np.float32)
            v /= np.linalg.norm(v, axis=1, keepdims=True)
            with cur.copy("COPY chunks_bench (doc_id, embedding) FROM STDIN") as cp:
                for i in range(m):
                    cp.write_row((docs[idx[i]], to_pgvector(v[i])))
            done += m
    conn.commit()


def build_index(conn):
    conn.execute("SET maintenance_work_mem = '512MB'")
    conn.execute("DROP INDEX IF EXISTS chunks_bench_hnsw")
    started = time.perf_counter()
    conn.execute(
        "CREATE INDEX chunks_bench_hnsw ON chunks_bench USING hnsw (embedding vector_cosine_ops)"
    )
    conn.commit()
    seconds = round(time.perf_counter() - started, 1)
    conn.execute("ANALYZE chunks_bench")
    conn.commit()
    index_bytes, table_bytes = conn.execute(
        "SELECT pg_relation_size('chunks_bench_hnsw'), pg_relation_size('chunks_bench')"
    ).fetchone()
    return {"build_seconds": seconds, "index_mb": round(index_bytes / 2**20, 1),
            "table_mb": round(table_bytes / 2**20, 1)}


def run(conn, arm, docs, qv, explain=False):
    base = EXACT_SQL if arm == "exact_scan" else SQL
    sql = ("EXPLAIN (ANALYZE, BUFFERS) " + base) if explain else base
    with conn.transaction():
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (EF_SEARCH,))
        for key, value in ARMS[arm].items():
            conn.execute("SELECT set_config(%s, %s, true)", (key, value))
        started = time.perf_counter()
        rows = conn.execute(sql, {"docs": docs, "qv": qv, "top": TOP}).fetchall()
        ms = (time.perf_counter() - started) * 1000
    return rows, ms


def pct(values, p):
    return statistics.quantiles(values, n=100)[p - 1] if len(values) > 1 else values[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=60000)
    parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--ef", type=int, default=100)
    parser.add_argument("--reuse", action="store_true", help="keep the existing chunks_bench and index")
    args = parser.parse_args()
    global EF_SEARCH
    EF_SEARCH = str(args.ef)

    personas = json.loads(Path("data/org/personas.json").read_text())
    questions = [json.loads(x) for x in Path("eval/questions.jsonl").read_text().splitlines()]
    texts = [q["question"] for q in questions if q["kind"] != "probe"][::3][: args.queries]
    qvecs = [to_pgvector(embed_query(t)) for t in texts]

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if args.reuse:
            index_bytes, table_bytes = conn.execute(
                "SELECT pg_relation_size('chunks_bench_hnsw'), pg_relation_size('chunks_bench')"
            ).fetchone()
            index_info = {"index_mb": round(index_bytes / 2**20, 1),
                          "table_mb": round(table_bytes / 2**20, 1)}
        else:
            build_table(conn, args.chunks)
            index_info = build_index(conn)

        print(f"{args.chunks} chunks, index {index_info}")

        rows, explain_text = [], []
        for pname, info in personas.items():
            allowed = sorted(r[0] for r in conn.execute(
                "SELECT doc_id FROM authorised_documents(%s)", (info["user"],)))
            exact = {}
            for arm in ARMS:
                for qv in qvecs[:5]:
                    run(conn, arm, allowed, qv)  # warm up
                lat, recalls, returned = [], [], []
                for i, qv in enumerate(qvecs):
                    got, ms = run(conn, arm, allowed, qv)
                    ids = [r[1] for r in got]
                    lat.append(ms)
                    returned.append(len(ids))
                    if arm == "exact_scan":
                        exact[i] = set(ids)
                    elif exact[i]:
                        recalls.append(len(set(ids) & exact[i]) / len(exact[i]))
                rows.append({
                    "persona": pname, "docs_visible": len(allowed), "arm": arm,
                    "p50_ms": round(statistics.median(lat), 1), "p95_ms": round(pct(lat, 95), 1),
                    "recall_vs_exact": None if arm == "exact_scan" else round(statistics.mean(recalls), 3),
                    "avg_returned": round(statistics.mean(returned), 1),
                })
                plan, _ = run(conn, arm, allowed, qvecs[0], explain=True)
                plan_text = "\n".join(r[0] for r in plan)
                rows[-1]["uses_hnsw_index"] = "chunks_bench_hnsw" in plan_text
                if pname == "mid_visibility":
                    explain_text.append(f"=== {arm} ===\n" + plan_text + "\n")

    print(f"{'persona':<16}{'arm':<17}{'visible':>8}{'p50 ms':>8}{'p95 ms':>8}"
          f"{'recall':>8}{'returned':>9}{'index':>7}")
    for r in rows:
        rec = "-" if r["recall_vs_exact"] is None else f"{r['recall_vs_exact']:.3f}"
        idx = "yes" if r["uses_hnsw_index"] else "no"
        print(f"{r['persona']:<16}{r['arm']:<17}{r['docs_visible']:>8}{r['p50_ms']:>8}"
              f"{r['p95_ms']:>8}{rec:>8}{r['avg_returned']:>9}{idx:>7}")

    out_dir = Path("benchmarks/results") / datetime.now(tz=UTC).date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"index_economics_{args.chunks}_ef{args.ef}.json").write_text(
        json.dumps({"chunks": args.chunks, "index": index_info, "queries": len(qvecs),
                    "ef_search": int(EF_SEARCH), "rows": rows}, indent=1))
    (out_dir / f"explain_{args.chunks}_ef{args.ef}.txt").write_text("\n".join(explain_text))
    return 0


if __name__ == "__main__":
    sys.exit(main())